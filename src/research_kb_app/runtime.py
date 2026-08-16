from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from research_kb.errors import OPERATION_CANCELLED, Diagnostic, ResearchKBError
from research_kb.services import (
    AgentTaskApplicationService,
    CapabilityService,
    CatalogCapabilityService,
    CatalogProjectionService,
    CatalogQueryService,
    DeterministicIntakeApplicationService,
    DiscoveryApplicationService,
    ExchangeApplicationService,
    IntakeSourceAdequacyResolutionApplicationService,
    ObsidianGeneratedViewsApplicationService,
    ReadingApplicationService,
    ResearchOrganizationApplicationService,
    ResearchSynthesisApplicationService,
    SourceAdequacyResolutionApplicationService,
    QuestionScreeningApplicationService,
    TagApplicationService,
    TrustedParseIntakeApplicationService,
    WorkspaceSession,
    WorkspaceSessionService,
)

from research_kb_app import __version__
from research_kb_app.compatibility import CoreCompatibility
from research_kb_app.config import AppConfig
from research_kb_app.errors import AppOperationError
from research_kb_app.exchange_custody import (
    ManagedExchangeFile,
    cleanup_abandoned_exchange_files,
    create_exchange_output,
)
from research_kb_app.external_reader import ExternalReaderLauncher
from research_kb_app.multipart import ManagedUpload, cleanup_abandoned_uploads
from research_kb_app.obsidian_sync import (
    MAX_SYNC_FILES,
    ObsidianVaultSyncService,
    SourceProjection,
    source_projection_from_status,
)
from research_kb_app.pdf_access import PdfHandleRegistry
from research_kb_app.source_review_confirmation import SourceReviewConfirmationRegistry
from research_kb_app.trusted_parse_leases import TrustedParseLeaseRegistry


_DETERMINISTIC_INTAKE_ROUTE = "local_source"
_DETERMINISTIC_INTAKE_DEPTH = "semantic_gate"
_DETERMINISTIC_INTAKE_CATALOG_QUERY = "local_source semantic_gate"
_INTAKE_FILTER_CURSOR_VERSION = 1
_INTAKE_FILTER_CURSOR_MAX_LENGTH = 4096
_INTAKE_FILTER_CURSOR_URLSAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)


@dataclass(frozen=True, slots=True)
class OperationLease:
    token: str
    category: str


class OperationCoordinator:
    _CATEGORIES = frozenset(
        {
            "agent_task",
            "catalog_rebuild",
            "discovery",
            "exchange",
            "intake",
            "obsidian",
            "screening",
            "tag",
        }
    )

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner: OperationLease | None = None
        self._category: str | None = None
        self._state = "idle"
        self._job_id: str | None = None
        self._diagnostic_code: str | None = None
        self._job_state_id: str | None = None
        self._job_state_digest: str | None = None
        self._cancel_event: threading.Event | None = None
        self._cancel_phase: str | None = None

    def acquire(self, category: str) -> OperationLease:
        if category not in self._CATEGORIES:
            raise AppOperationError("RKBAPP-OPERATION-CATEGORY", "Operation category is not supported")
        with self._lock:
            if self._owner is not None:
                raise AppOperationError("RKBAPP-OPERATION-BUSY", "Another workspace operation is running")
            lease = OperationLease(uuid.uuid4().hex, category)
            self._owner = lease
            self._category = category
            self._state = "running"
            self._job_id = None
            self._diagnostic_code = None
            self._job_state_id = None
            self._job_state_digest = None
            self._cancel_event = None
            self._cancel_phase = None
            return lease

    def set_job_id(self, lease: OperationLease, job_id: str) -> None:
        with self._lock:
            self._require_owner(lease)
            self._job_id = job_id

    def bind_cancellation(
        self,
        lease: OperationLease,
        *,
        job_id: str,
        job_state_id: str,
        job_state_digest: str,
    ) -> None:
        with self._lock:
            self._require_owner(lease)
            self._job_id = job_id
            self._job_state_id = job_state_id
            self._job_state_digest = job_state_digest
            self._cancel_event = threading.Event()
            self._cancel_phase = "worker_cancellable"

    def cancel_requested(self, lease: OperationLease) -> bool:
        with self._lock:
            self._require_owner(lease)
            return self._cancel_event is not None and self._cancel_event.is_set()

    def begin_promotion(self, lease: OperationLease) -> bool:
        with self._lock:
            self._require_owner(lease)
            if self._cancel_event is None:
                raise AppOperationError(
                    "RKBAPP-OPERATION-CANCELLATION",
                    "The operation has no cancellation binding",
                )
            if self._cancel_event.is_set():
                self._cancel_phase = "settling"
                return False
            self._cancel_phase = "promotion_committing"
            return True

    def request_cancel(
        self,
        job_id: str,
        expected_state: dict[str, str],
    ) -> str | None:
        with self._lock:
            if (
                self._owner is None
                or self._category != "intake"
                or self._job_id != job_id
                or self._cancel_event is None
            ):
                return None
            if (
                expected_state.get("state_id") != self._job_state_id
                or expected_state.get("state_digest") != self._job_state_digest
            ):
                raise AppOperationError(
                    "RKBAPP-TRUSTED-PARSE-CANCEL-STALE",
                    "Trusted Parse cancellation does not match the active Job preparation",
                )
            if self._cancel_phase == "worker_cancellable":
                self._cancel_event.set()
                self._cancel_phase = "settling"
                return "accepted"
            if self._cancel_phase == "settling" and self._cancel_event.is_set():
                return "accepted"
            return "too_late"

    def request_shutdown_cancel(self) -> bool:
        with self._lock:
            if self._owner is None or self._cancel_event is None:
                return False
            if self._cancel_phase == "worker_cancellable":
                self._cancel_event.set()
                self._cancel_phase = "settling"
            return True

    def complete(self, lease: OperationLease) -> None:
        with self._lock:
            self._require_owner(lease)
            self._cancel_phase = "complete"
            self._owner = None
            self._state = "current"
            self._diagnostic_code = None

    def fail(self, lease: OperationLease, diagnostic_code: str) -> None:
        with self._lock:
            self._require_owner(lease)
            self._cancel_phase = "complete"
            self._owner = None
            self._state = "failed"
            self._diagnostic_code = diagnostic_code

    def is_owner(self, lease: OperationLease) -> bool:
        with self._lock:
            return self._owner == lease

    def is_busy(self) -> bool:
        with self._lock:
            return self._owner is not None

    def public(self) -> dict[str, str | None]:
        with self._lock:
            state = self._state
            if self._category == "catalog_rebuild" and state == "running":
                state = "building"
            return {
                "category": self._category,
                "state": state,
                "job_id": self._job_id,
                "diagnostic_code": self._diagnostic_code,
            }

    def _require_owner(self, lease: OperationLease) -> None:
        if self._owner != lease:
            raise AppOperationError("RKBAPP-OPERATION-STALE", "Operation lease is no longer current")


@dataclass(frozen=True, slots=True)
class ObsidianPreviewLease:
    token: str
    kind: str
    browser_session_id: str
    workspace_option_id: str
    target_id: str | None
    optional_tables: tuple[str, ...]
    expected_state: dict[str, Any] | None
    source: SourceProjection | None
    expected_destination_state: str | None
    expires_at: float


@dataclass(frozen=True, slots=True)
class ExchangeUploadLease:
    token: str
    browser_session_id: str
    workspace_option_id: str
    upload: ManagedExchangeFile
    expires_at: float


@dataclass(frozen=True, slots=True)
class ExchangePreviewLease:
    token: str
    kind: str
    browser_session_id: str
    workspace_option_id: str
    request: dict[str, Any]
    preview: dict[str, Any]
    upload: ManagedExchangeFile | None
    created_at: str | None
    expires_at: float


@dataclass(frozen=True, slots=True)
class ExchangeDownloadLease:
    token: str
    browser_session_id: str
    workspace_option_id: str
    artifact: ManagedExchangeFile
    filename: str
    expires_at: float


class AppRuntime:
    _OBSIDIAN_PREVIEW_TTL_SECONDS = 300
    _MAX_OBSIDIAN_PREVIEW_LEASES = 32
    _EXCHANGE_LEASE_TTL_SECONDS = 300
    _MAX_EXCHANGE_LEASES = 32

    def __init__(
        self,
        config: AppConfig,
        compatibility: CoreCompatibility,
        *,
        pdf_launcher: ExternalReaderLauncher | None = None,
        pdf_handles: PdfHandleRegistry | None = None,
        source_review_confirmations: SourceReviewConfirmationRegistry | None = None,
        trusted_parse_leases: TrustedParseLeaseRegistry | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.compatibility = compatibility
        self.workspace_service = WorkspaceSessionService(config.workspace_mapping())
        self.labels = config.labels()
        self.active_option_id: str | None = None
        self.session: WorkspaceSession | None = None
        self.agent: AgentTaskApplicationService | None = None
        self.source_adequacy_resolution: SourceAdequacyResolutionApplicationService | None = None
        self.intake_source_adequacy_resolution: IntakeSourceAdequacyResolutionApplicationService | None = None
        self.discovery = DiscoveryApplicationService()
        self.exchange: ExchangeApplicationService | None = None
        self.intake: DeterministicIntakeApplicationService | None = None
        self.trusted_parse: TrustedParseIntakeApplicationService | None = None
        self.reading: ReadingApplicationService | None = None
        self.organization: ResearchOrganizationApplicationService | None = None
        self.obsidian: ObsidianGeneratedViewsApplicationService | None = None
        self.obsidian_sync = ObsidianVaultSyncService()
        self.research_synthesis: ResearchSynthesisApplicationService | None = None
        self.screening: QuestionScreeningApplicationService | None = None
        self.tags: TagApplicationService | None = None
        self.projection: CatalogProjectionService | None = None
        self.query: CatalogQueryService | None = None
        self.operation = OperationCoordinator()
        self.pdf_launcher = pdf_launcher or ExternalReaderLauncher()
        self.pdf_handles = pdf_handles or PdfHandleRegistry()
        self.source_review_confirmations = source_review_confirmations or SourceReviewConfirmationRegistry()
        self.trusted_parse_leases = trusted_parse_leases or TrustedParseLeaseRegistry()
        self._catalog_status: dict[str, Any] | None = None
        self._agent_leases: dict[str, dict[str, Any]] = {}
        self._obsidian_leases: dict[str, ObsidianPreviewLease] = {}
        self._exchange_uploads: dict[str, ExchangeUploadLease] = {}
        self._exchange_previews: dict[str, ExchangePreviewLease] = {}
        self._exchange_downloads: dict[str, ExchangeDownloadLease] = {}
        self._active_obsidian_target_id: str | None = None
        self._monotonic_clock = monotonic_clock
        self._tasks: set[asyncio.Task[None]] = set()
        self._intake_filter_cursor_key = secrets.token_bytes(32)
        self.shutdown_requested = threading.Event()
        cleanup_abandoned_uploads(config.state_root)
        cleanup_abandoned_exchange_files(config.state_root)

    def list_workspaces(self) -> dict[str, Any]:
        result = self.workspace_service.list_options()
        return {
            **result,
            "workspaces": [
                {**workspace, "label": self.labels[workspace["option_id"]]}
                for workspace in result["workspaces"]
            ],
        }

    def replace_config(self, config: AppConfig) -> None:
        self._require_idle()
        if self.session is not None:
            raise AppOperationError("RKBAPP-CONFIG-ACTIVE", "Close the active workspace before applying a profile revision")
        self.config = config
        self.workspace_service = WorkspaceSessionService(config.workspace_mapping())
        self.labels = config.labels()

    def open_workspace(self, option_id: str) -> dict[str, Any]:
        self._require_idle()
        session = self.workspace_service.open(option_id)
        projection = CatalogProjectionService(session, self.config.state_root)
        query = CatalogQueryService(projection)
        status = query.bind_existing_projection()
        self.active_option_id = option_id
        self.session = session
        self.agent = AgentTaskApplicationService()
        self.source_adequacy_resolution = SourceAdequacyResolutionApplicationService()
        self.intake_source_adequacy_resolution = IntakeSourceAdequacyResolutionApplicationService()
        self.exchange = ExchangeApplicationService()
        self.intake = DeterministicIntakeApplicationService(
            parse_policy="trusted_supervised_parse"
        )
        self.trusted_parse = TrustedParseIntakeApplicationService()
        self.reading = ReadingApplicationService()
        self.organization = ResearchOrganizationApplicationService()
        self.obsidian = ObsidianGeneratedViewsApplicationService()
        self.research_synthesis = ResearchSynthesisApplicationService()
        self.screening = QuestionScreeningApplicationService()
        self.tags = TagApplicationService()
        self.projection = projection
        self.query = query
        self.operation = OperationCoordinator()
        self._agent_leases.clear()
        self.trusted_parse_leases.clear()
        obsidian_leases = getattr(self, "_obsidian_leases", None)
        if obsidian_leases is not None:
            obsidian_leases.clear()
        self._clear_exchange_leases()
        self._active_obsidian_target_id = None
        self.pdf_handles.clear()
        self.source_review_confirmations.clear()
        self._catalog_status = status
        return {
            "status": "success",
            "workspace": {**session.display(), "label": self.labels[option_id]},
            "catalog": status,
        }

    def catalog_status(self) -> dict[str, Any]:
        self._require_projection()
        if self._catalog_status is None:
            raise AppOperationError("RKBAPP-CATALOG-STATUS-UNAVAILABLE", "Catalog status is unavailable")
        return {**dict(self._catalog_status), "operation": self.operation.public()}

    def start_rebuild(self) -> dict[str, Any]:
        projection = self._require_projection()
        query = self._require_query()
        lease = self.operation.acquire("catalog_rebuild")
        self._track(asyncio.create_task(self._run_rebuild(lease, projection, query)))
        return {"status": "accepted", "operation": self.operation.public()}

    async def _run_rebuild(
        self,
        lease: OperationLease,
        projection: CatalogProjectionService,
        query: CatalogQueryService,
    ) -> None:
        try:
            result = await asyncio.to_thread(projection.rebuild)
            if projection is not self.projection:
                self.operation.fail(lease, "RKBAPP-STALE-OPERATION")
                return
            self._catalog_status = query.bind_projection_result(result)
            self.operation.complete(lease)
        except ResearchKBError as error:
            self.operation.fail(lease, error.diagnostic.code)
        except Exception:
            self.operation.fail(lease, "RKBAPP-INTERNAL")

    def intake_limits(self) -> dict[str, Any]:
        return self._require_intake().limits(self._require_session())

    def discovery_limits(self) -> dict[str, Any]:
        self._require_session()
        return self.discovery.limits()

    async def search_discovery(self, request: dict[str, Any]) -> dict[str, Any]:
        self._require_session()
        return await asyncio.to_thread(self.discovery.search, request)

    async def select_discovery(
        self,
        report: dict[str, Any],
        result_keys: list[str],
    ) -> dict[str, Any]:
        return await self._run_discovery_mutation(
            lambda: self.discovery.select(
                self._require_session(),
                report,
                result_keys,
                actor="user",
            )
        )

    def list_discovery_candidates(
        self,
        *,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        limit = min(self.discovery_limits()["max_page_size"], self.config.request_budgets.max_page_size)
        if not 1 <= page_size <= limit:
            raise AppOperationError(
                "RKBAPP-PAGE-LIMIT",
                "Discovery candidate page size exceeds the supported budget",
                status_code=400,
            )
        return self.discovery.list_candidates(
            self._require_session(),
            page_size=page_size,
            cursor=cursor,
        )

    def show_discovery_candidate(self, candidate_id: str) -> dict[str, Any]:
        report = self.discovery.show_candidate(self._require_session(), candidate_id)
        candidate = report["candidate"]
        return {
            "status": "success",
            "interface_version": report["interface_version"],
            "candidate": {
                key: candidate.get(key)
                for key in (
                    "candidate_id",
                    "result_key",
                    "title",
                    "authors",
                    "first_publication_date",
                    "journal_or_server",
                    "doi",
                    "paper_type",
                    "publication_types",
                    "abstract",
                    "matched_keywords",
                    "match_location",
                    "full_text_status",
                    "version_relationship",
                    "possible_duplicate_result_keys",
                    "target_question_ids",
                    "selection_status",
                    "source_status",
                    "acquisition_status",
                    "not_evidence",
                )
            },
            "persistent_writes": 0,
        }

    async def resolve_discovery_candidate(self, candidate_id: str) -> dict[str, Any]:
        session = self._require_session()
        return await asyncio.to_thread(self.discovery.resolve, session, candidate_id)

    async def acquire_discovery_candidate(self, candidate_id: str) -> dict[str, Any]:
        result = await self._run_discovery_mutation(
            lambda: self.discovery.acquire(
                self._require_session(),
                candidate_id,
                actor="user",
            )
        )
        return {
            key: result[key]
            for key in (
                "status",
                "interface_version",
                "result",
                "candidate_id",
                "provider",
                "content_size_bytes",
                "content_type",
                "persistent_writes",
                "event_id",
                "operation",
            )
        }

    def inspect_acquired_candidate(self, candidate_id: str) -> dict[str, Any]:
        report = self.discovery.inspect_acquired(self._require_session(), candidate_id)
        return {
            "status": "success",
            "interface_version": report["interface_version"],
            "candidate_id": report["candidate_id"],
            "registration": report["registration"],
            "persistent_writes": 0,
        }

    async def _run_discovery_mutation(
        self,
        invoke: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        lease = self.operation.acquire("discovery")
        try:
            result = await asyncio.to_thread(invoke)
            self.operation.complete(lease)
        except AppOperationError as error:
            self.operation.fail(lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(lease, "RKBAPP-INTERNAL")
            raise
        return {**result, "operation": self.operation.public()}

    def scan_inbox(self, *, max_entries: int, min_stable_age_seconds: int) -> dict[str, Any]:
        limits = self.intake_limits()
        if not 1 <= max_entries <= min(limits["max_scan_entries"], self.config.request_budgets.max_page_size):
            raise AppOperationError("RKBAPP-PAGE-LIMIT", "Inbox page size exceeds the supported budget", status_code=400)
        if not 0 <= min_stable_age_seconds <= 86400:
            raise AppOperationError("RKBAPP-STABILITY-WINDOW", "Inbox stability window is invalid", status_code=400)
        return self._require_intake().scan_inbox(
            self._require_session(),
            max_entries=max_entries,
            min_stable_age_seconds=min_stable_age_seconds,
        )

    def start_upload(self, upload: ManagedUpload, request: dict[str, Any]) -> dict[str, Any]:
        lease = self.operation.acquire("intake")

        def invoke() -> dict[str, Any]:
            with upload.open() as stream:
                core_request = {
                    **request,
                    "expected_sha256": upload.sha256,
                    "expected_size_bytes": upload.size_bytes,
                }
                return self._require_intake().start_upload(self._require_session(), stream, core_request)

        self._track(asyncio.create_task(self._run_intake(lease, invoke, upload)))
        return {"status": "accepted", "operation": self.operation.public()}

    def start_inbox(self, candidate_token: str, request: dict[str, Any]) -> dict[str, Any]:
        lease = self.operation.acquire("intake")
        self._track(
            asyncio.create_task(
                self._run_intake(
                    lease,
                    lambda: self._require_intake().start_inbox(
                        self._require_session(), candidate_token, request
                    ),
                )
            )
        )
        return {"status": "accepted", "operation": self.operation.public()}

    def resume_job(
        self,
        job_id: str,
        expected_state: dict[str, str],
        request: dict[str, Any],
    ) -> dict[str, Any]:
        lease = self.operation.acquire("intake")
        self.operation.set_job_id(lease, job_id)
        self._track(
            asyncio.create_task(
                self._run_intake(
                    lease,
                    lambda: self._require_intake().resume(
                        self._require_session(), job_id, expected_state, request
                    ),
                )
            )
        )
        return {"status": "accepted", "operation": self.operation.public()}

    def cancel_job(self, job_id: str, expected_state: dict[str, str]) -> dict[str, Any]:
        active_outcome = self.operation.request_cancel(job_id, expected_state)
        if active_outcome is not None:
            return {
                "status": "accepted",
                "cancel_outcome": active_outcome,
                "operation": self.operation.public(),
            }
        lease = self.operation.acquire("intake")
        self.operation.set_job_id(lease, job_id)
        self._track(
            asyncio.create_task(
                self._run_intake(
                    lease,
                    lambda: self._require_intake().cancel(
                        self._require_session(), job_id, expected_state
                    ),
                )
            )
        )
        return {"status": "accepted", "operation": self.operation.public()}

    def prepare_trusted_parse(
        self,
        *,
        browser_session_id: str,
        job_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        preparation = self._require_trusted_parse().prepare(
            self._require_session(),
            job_id,
            expected_state,
        )
        lease = self.trusted_parse_leases.issue(
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            preparation=preparation,
        )
        return {
            **preparation.public_projection(),
            "lease_token": lease.token,
        }

    def approve_trusted_parse(
        self,
        *,
        browser_session_id: str,
        job_id: str,
        lease_token: str,
        aggregate_preview_digest: str,
    ) -> dict[str, Any]:
        start = self.trusted_parse_leases.begin(
            lease_token,
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            job_id=job_id,
            aggregate_preview_digest=aggregate_preview_digest,
        )
        if start.outcome != "start":
            if start.lease.current_result is not None:
                return dict(start.lease.current_result)
            return {
                "status": "accepted",
                "duplicate": True,
                "operation": self.operation.public(),
            }
        try:
            operation_lease = self.operation.acquire("intake")
            self.operation.bind_cancellation(
                operation_lease,
                job_id=job_id,
                job_state_id=start.lease.job_state_id,
                job_state_digest=start.lease.job_state_digest,
            )
        except Exception:
            self.trusted_parse_leases.restore_prepared(lease_token)
            raise
        accepted = {"status": "accepted", "operation": self.operation.public()}
        self.trusted_parse_leases.set_current_result(lease_token, accepted)
        self._track(
            asyncio.create_task(
                self._run_trusted_parse(
                    operation_lease,
                    lease_token,
                    start.lease.preparation,
                    aggregate_preview_digest,
                )
            )
        )
        return accepted

    async def _run_trusted_parse(
        self,
        operation_lease: OperationLease,
        lease_token: str,
        preparation: Any,
        aggregate_preview_digest: str,
    ) -> None:
        schedule_rebuild = False

        def before_promotion() -> None:
            if not self.operation.begin_promotion(operation_lease):
                raise ResearchKBError(
                    Diagnostic(
                        OPERATION_CANCELLED,
                        "trusted-parse",
                        preparation.job_id,
                        "/cancel",
                        "trusted Parse was cancelled before promotion",
                    )
                )

        try:
            result = await asyncio.to_thread(
                self._require_trusted_parse().approve,
                self._require_session(),
                preparation,
                aggregate_preview_digest=aggregate_preview_digest,
                actor="user",
                cancel_check=lambda: self.operation.cancel_requested(operation_lease),
                before_promotion=before_promotion,
            )
            rendered = result.to_dict()
            schedule_rebuild = rendered.get("persistent_writes", 0) > 0
            if schedule_rebuild:
                self._mark_catalog_stale()
            self.operation.complete(operation_lease)
            current = {
                "status": "accepted",
                "trusted_parse_outcome": result.outcome,
                "operation": self.operation.public(),
            }
            self.trusted_parse_leases.complete(lease_token, current)
        except ResearchKBError as error:
            self._mark_catalog_stale()
            self.trusted_parse_leases.fail(lease_token, error.diagnostic.code)
            self.operation.fail(operation_lease, error.diagnostic.code)
        except Exception:
            self._mark_catalog_stale()
            self.trusted_parse_leases.fail(lease_token, "RKBAPP-INTERNAL")
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
        if schedule_rebuild:
            self._try_schedule_rebuild()

    async def _run_intake(
        self,
        lease: OperationLease,
        invoke: Callable[[], dict[str, Any]],
        upload: ManagedUpload | None = None,
    ) -> None:
        entered_core = False
        schedule_rebuild = False
        try:
            entered_core = True
            result = await asyncio.to_thread(invoke)
            pipeline = result.get("pipeline")
            if isinstance(pipeline, dict) and isinstance(pipeline.get("job_id"), str):
                self.operation.set_job_id(lease, pipeline["job_id"])
            schedule_rebuild = result.get("persistent_writes", 0) > 0
            if schedule_rebuild:
                self._mark_catalog_stale()
            self.operation.complete(lease)
        except ResearchKBError as error:
            if entered_core:
                self._mark_catalog_stale()
            self.operation.fail(lease, error.diagnostic.code)
        except Exception:
            if entered_core:
                self._mark_catalog_stale()
            self.operation.fail(lease, "RKBAPP-INTERNAL")
        finally:
            if upload is not None:
                upload.cleanup()
        if schedule_rebuild:
            self._try_schedule_rebuild()

    def list_jobs(
        self,
        *,
        page_size: int,
        cursor: str | None,
        requested_route: str | None = None,
        requested_depth: str | None = None,
    ) -> dict[str, Any]:
        limits = self.intake_limits()
        if not 1 <= page_size <= min(limits["max_job_page_size"], self.config.request_budgets.max_page_size):
            raise AppOperationError("RKBAPP-PAGE-LIMIT", "Job page size exceeds the supported budget", status_code=400)
        if requested_route is None and requested_depth is None:
            return self._require_intake().list_jobs(
                self._require_session(),
                page_size=page_size,
                cursor=cursor,
                catalog_query=self._operational_catalog_query(),
            )
        if (
            requested_route != _DETERMINISTIC_INTAKE_ROUTE
            or requested_depth != _DETERMINISTIC_INTAKE_DEPTH
        ):
            raise AppOperationError(
                "RKBAPP-INTAKE-FILTER",
                "仅支持确定性文献处理 Job 筛选条件",
                status_code=400,
            )
        return self._list_filtered_intake_jobs(page_size=page_size, cursor=cursor)

    def _list_filtered_intake_jobs(
        self,
        *,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        workspace_option_id = self._require_active_option()
        if cursor is None:
            mode = "catalog" if self._operational_catalog_query() is not None else "direct"
            inner_cursor = None
        else:
            decoded = self._decode_intake_filter_cursor(cursor, workspace_option_id)
            mode = decoded["mode"]
            inner_cursor = decoded["inner_cursor"]
        if mode == "catalog":
            return self._list_filtered_intake_jobs_catalog(
                page_size=page_size,
                inner_cursor=inner_cursor,
            )
        return self._list_filtered_intake_jobs_direct(
            page_size=page_size,
            inner_cursor=inner_cursor,
        )

    def _list_filtered_intake_jobs_catalog(
        self,
        *,
        page_size: int,
        inner_cursor: str | None,
    ) -> dict[str, Any]:
        query = self._operational_catalog_query()
        if query is None:
            raise AppOperationError(
                "RKBAPP-INTAKE-FILTER-PROJECTION",
                "筛选 Job 的 Catalog 投影不是当前状态",
            )
        try:
            page = query.search(
                query=_DETERMINISTIC_INTAKE_CATALOG_QUERY,
                item_kinds=("pipeline_job",),
                status_labels=("route:local_source",),
                page_size=page_size,
                cursor=inner_cursor,
            )
        except Exception as error:
            raise AppOperationError(
                "RKBAPP-INTAKE-FILTER-PROJECTION",
                "筛选 Job 的 Catalog 投影不可用",
            ) from error
        next_inner_cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if (
            not isinstance(page, dict)
            or page.get("projection_state") != "current"
            or not isinstance(page.get("items"), list)
            or (
                next_inner_cursor is not None
                and (not isinstance(next_inner_cursor, str) or not next_inner_cursor)
            )
        ):
            raise AppOperationError(
                "RKBAPP-INTAKE-FILTER-PROJECTION",
                "筛选 Job 的 Catalog 投影不是当前状态",
            )

        intake = self._require_intake()
        session = self._require_session()
        jobs: list[dict[str, Any]] = []
        for item in page["items"]:
            if not isinstance(item, dict) or item.get("item_kind") != "pipeline_job":
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DETAIL",
                    "筛选 Job 的 Catalog 投影不是当前状态",
                )
            item_id = item.get("item_id")
            if not isinstance(item_id, str):
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DETAIL",
                    "筛选 Job 的 Catalog 投影不是当前状态",
                )
            try:
                detail = query.detail(item_id)
            except Exception as error:
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DETAIL",
                    "筛选 Job 的 Catalog 详情不可用",
                ) from error
            record = detail.get("detail") if isinstance(detail, dict) else None
            catalog_item = detail.get("item") if isinstance(detail, dict) else None
            if (
                not isinstance(detail, dict)
                or detail.get("projection_state") != "current"
                or detail.get("current_record_status") != "current"
                or not isinstance(catalog_item, dict)
                or catalog_item.get("record_kind") != "pipeline-job-state"
                or not isinstance(record, dict)
                or record.get("requested_route") != _DETERMINISTIC_INTAKE_ROUTE
                or record.get("requested_depth") != _DETERMINISTIC_INTAKE_DEPTH
            ):
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DETAIL",
                    "筛选 Job 的 Catalog 详情不是当前的确定性 intake Job",
                )
            try:
                verified = intake.show_job(session, record["job_id"])
                pipeline = verified["pipeline"]
            except (KeyError, TypeError, ResearchKBError) as error:
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DETAIL",
                    "筛选 Job 不符合确定性 intake Job 合约",
                ) from error
            if (
                not isinstance(pipeline, dict)
                or pipeline.get("requested_route") != _DETERMINISTIC_INTAKE_ROUTE
                or pipeline.get("requested_depth") != _DETERMINISTIC_INTAKE_DEPTH
            ):
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DETAIL",
                    "筛选 Job 不符合确定性 intake Job 合约",
                )
            jobs.append(pipeline)

        return {
            "status": "success",
            "interface_version": self.intake_limits()["interface_version"],
            "jobs": jobs,
            "next_cursor": (
                self._encode_intake_filter_cursor(
                    mode="catalog",
                    inner_cursor=next_inner_cursor,
                )
                if next_inner_cursor is not None
                else None
            ),
            "projection_state": page["projection_state"],
            "persistent_writes": 0,
        }

    def _list_filtered_intake_jobs_direct(
        self,
        *,
        page_size: int,
        inner_cursor: str | None,
    ) -> dict[str, Any]:
        intake = self._require_intake()
        session = self._require_session()
        limits = self.intake_limits()
        max_page_size = min(
            limits["max_job_page_size"],
            self.config.request_budgets.max_page_size,
        )
        internal_page_size = min(max_page_size, max(page_size, 20))
        jobs: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()

        while len(jobs) < page_size:
            if inner_cursor is not None:
                if inner_cursor in seen_cursors:
                    raise AppOperationError(
                        "RKBAPP-INTAKE-FILTER-DIRECT",
                        "确定性 intake Job direct 分页游标未前进",
                    )
                seen_cursors.add(inner_cursor)
            try:
                page = intake.list_jobs(
                    session,
                    page_size=internal_page_size,
                    cursor=inner_cursor,
                    catalog_query=None,
                )
            except Exception as error:
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DIRECT",
                    "确定性 intake Job direct 列表不可用",
                ) from error
            next_core_cursor = page.get("next_cursor") if isinstance(page, dict) else None
            raw_jobs = page.get("jobs") if isinstance(page, dict) else None
            if (
                not isinstance(page, dict)
                or not isinstance(raw_jobs, list)
                or "next_cursor" not in page
                or (
                    next_core_cursor is not None
                    and (not isinstance(next_core_cursor, str) or not next_core_cursor)
                )
            ):
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DIRECT",
                    "确定性 intake Job direct 列表响应无效",
                )

            filtered: list[tuple[int, dict[str, Any]]] = []
            for index, item in enumerate(raw_jobs):
                if (
                    not isinstance(item, dict)
                    or item.get("requested_route") != _DETERMINISTIC_INTAKE_ROUTE
                    or item.get("requested_depth") != _DETERMINISTIC_INTAKE_DEPTH
                ):
                    continue
                job_id = item.get("job_id")
                if not isinstance(job_id, str) or not job_id:
                    raise AppOperationError(
                        "RKBAPP-INTAKE-FILTER-DIRECT",
                        "确定性 intake Job direct 列表包含无效 Job ID",
                    )
                filtered.append((index, item))

            remaining = page_size - len(jobs)
            if len(filtered) >= remaining:
                selected = filtered[:remaining]
                jobs.extend(item for _, item in selected)
                last_index, last_job = selected[-1]
                if last_index < len(raw_jobs) - 1:
                    inner_cursor = last_job["job_id"]
                else:
                    inner_cursor = next_core_cursor
                break

            jobs.extend(item for _, item in filtered)
            if next_core_cursor is None:
                inner_cursor = None
                break
            if next_core_cursor == inner_cursor:
                raise AppOperationError(
                    "RKBAPP-INTAKE-FILTER-DIRECT",
                    "确定性 intake Job direct 分页游标未前进",
                )
            inner_cursor = next_core_cursor

        projection_state = (
            self._catalog_status.get("projection_state")
            if isinstance(self._catalog_status, dict)
            else None
        )
        if not isinstance(projection_state, str):
            projection_state = "unknown"
        return {
            "status": "success",
            "interface_version": limits["interface_version"],
            "jobs": jobs,
            "next_cursor": (
                self._encode_intake_filter_cursor(
                    mode="direct",
                    inner_cursor=inner_cursor,
                )
                if inner_cursor is not None
                else None
            ),
            "projection_state": projection_state,
            "persistent_writes": 0,
        }

    def _encode_intake_filter_cursor(self, *, mode: str, inner_cursor: str) -> str:
        if (
            mode not in {"catalog", "direct"}
            or not isinstance(inner_cursor, str)
            or not inner_cursor
        ):
            raise AppOperationError(
                "RKBAPP-INTAKE-FILTER-DIRECT",
                "确定性 intake Job direct 游标无效",
            )
        payload = {
            "version": _INTAKE_FILTER_CURSOR_VERSION,
            "mode": mode,
            "workspace_option_id": self._require_active_option(),
            "inner_cursor": inner_cursor,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(
            self._intake_filter_cursor_key,
            canonical,
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(canonical + signature).decode("ascii").rstrip("=")

    def _decode_intake_filter_cursor(
        self,
        cursor: str,
        workspace_option_id: str,
    ) -> dict[str, str]:
        def invalid() -> AppOperationError:
            return AppOperationError(
                "RKBAPP-INTAKE-FILTER-CURSOR",
                "筛选游标无效、已篡改、跨工作区或模式不匹配",
                status_code=400,
            )

        if (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor) > _INTAKE_FILTER_CURSOR_MAX_LENGTH
            or any(character not in _INTAKE_FILTER_CURSOR_URLSAFE for character in cursor)
        ):
            raise invalid()
        padded = cursor + "=" * (-len(cursor) % 4)
        try:
            encoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        except (binascii.Error, ValueError, UnicodeEncodeError) as error:
            raise invalid() from error
        if base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=") != cursor:
            raise invalid()
        if len(encoded) <= hashlib.sha256().digest_size:
            raise invalid()
        canonical = encoded[:-hashlib.sha256().digest_size]
        signature = encoded[-hashlib.sha256().digest_size :]
        expected = hmac.new(self._intake_filter_cursor_key, canonical, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise invalid()
        try:
            payload = json.loads(canonical.decode("utf-8"))
            normalized = json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise invalid() from error
        if normalized != canonical or not isinstance(payload, dict):
            raise invalid()
        if set(payload) != {
            "version",
            "mode",
            "workspace_option_id",
            "inner_cursor",
        }:
            raise invalid()
        version = payload.get("version")
        mode = payload.get("mode")
        payload_workspace = payload.get("workspace_option_id")
        inner_cursor = payload.get("inner_cursor")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != _INTAKE_FILTER_CURSOR_VERSION
            or not isinstance(mode, str)
            or mode not in {"catalog", "direct"}
            or not isinstance(payload_workspace, str)
            or payload_workspace != workspace_option_id
            or not isinstance(inner_cursor, str)
            or not inner_cursor
        ):
            raise invalid()
        return {
            "mode": mode,
            "inner_cursor": inner_cursor,
        }

    def show_job(self, job_id: str) -> dict[str, Any]:
        return self._require_intake().show_job(self._require_session(), job_id)

    def agent_registry(self) -> dict[str, Any]:
        return self._require_agent().registry(self._require_session())

    def list_agent_tasks(self, *, page_size: int, cursor: str | None) -> dict[str, Any]:
        if not 1 <= page_size <= self.config.request_budgets.max_page_size:
            raise AppOperationError(
                "RKBAPP-PAGE-LIMIT",
                "Agent Task page size exceeds the supported budget",
                status_code=400,
            )
        return self._require_agent().list_tasks(
            self._require_session(),
            page_size=page_size,
            cursor=cursor,
            catalog_query=self._operational_catalog_query(),
        )

    def show_agent_task(self, task_id: str) -> dict[str, Any]:
        return self._require_agent().show_task(self._require_session(), task_id)

    def list_directions(self, *, page_size: int, cursor: str | None) -> dict[str, Any]:
        return self._require_organization().list_directions(
            self._require_session(),
            page_size=page_size,
            cursor=cursor,
        )

    def show_direction(self, target_id: str) -> dict[str, Any]:
        return self._require_organization().show_direction(self._require_session(), target_id)

    def list_field_map_entries(self, *, page_size: int, cursor: str | None) -> dict[str, Any]:
        return self._require_organization().list_field_map_entries(
            self._require_session(),
            page_size=page_size,
            cursor=cursor,
        )

    def show_field_map_entry(self, target_id: str) -> dict[str, Any]:
        return self._require_organization().show_field_map_entry(
            self._require_session(),
            target_id,
        )

    def list_questions(self, *, page_size: int, cursor: str | None) -> dict[str, Any]:
        return self._require_organization().list_questions(
            self._require_session(),
            page_size=page_size,
            cursor=cursor,
        )

    def show_question(self, target_id: str) -> dict[str, Any]:
        return self._require_organization().show_question(self._require_session(), target_id)

    def show_paper_organization_context(self, paper_id: str) -> dict[str, Any]:
        return self._require_organization().show_paper_context(
            self._require_session(),
            paper_id,
        )

    def exchange_capabilities(self) -> dict[str, Any]:
        result = self._require_exchange().limits(self._require_session())
        return {
            "status": "success",
            "bundle_format": result["bundle_format"],
            "selectors": result["selectors"],
            "source_inclusion_available": result["source_inclusion_available"],
            "import_available": result["import_available"],
            "safe_reader_profile": result["safe_reader_profile"],
            "browser_paths_accepted": False,
            "external_records_are_local_facts": False,
            "lease_ttl_seconds": self._EXCHANGE_LEASE_TTL_SECONDS,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def preview_exchange_export(
        self,
        *,
        browser_session_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        self._drop_expired_exchange_leases()
        core_request = {
            "scope": request["scope"],
            "include_sources": request["include_sources"],
        }
        if request.get("selector_id") is not None:
            core_request["selector_id"] = request["selector_id"]
        if request["include_sources"]:
            if not request["rights_asserted"]:
                raise AppOperationError(
                    "RKBAPP-EXCHANGE-RIGHTS",
                    "Source-inclusive export requires the rights assertion",
                    status_code=403,
                )
            core_request["rights_assertion"] = "user_asserts_redistribution_authorized"
        elif request["rights_asserted"]:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-RIGHTS",
                "Source-free export cannot carry a rights assertion",
                status_code=400,
            )
        preview = self._require_exchange().preview_export(self._require_session(), core_request)
        lease = self._create_exchange_preview(
            kind="export",
            browser_session_id=browser_session_id,
            request=core_request,
            preview=preview,
        )
        return {
            **self._public_export_preview(preview),
            "preview_token": lease.token,
            "preview_ttl_seconds": self._EXCHANGE_LEASE_TTL_SECONDS,
        }

    async def build_exchange_export(
        self,
        *,
        browser_session_id: str,
        preview_token: str,
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("exchange")
        output = create_exchange_output(self.config.state_root)
        try:
            lease = self._consume_exchange_preview(
                preview_token,
                kind="export",
                browser_session_id=browser_session_id,
            )
            preview = lease.preview
            build_request = {
                **lease.request,
                "expected_basis_digest": preview["basis_digest"],
                "export_id": preview["export_id"],
                "created_at": preview["created_at"],
            }
            result = await self._run_exchange_worker(
                self._require_exchange().build_export,
                self._require_session(),
                build_request,
                target=output.path,
                actor="user",
            )
            max_bytes = self.exchange_capabilities()["safe_reader_profile"]["max_archive_bytes"]
            artifact = output.finalize(max_archive_bytes=max_bytes)
            download = self._create_exchange_download(
                browser_session_id=browser_session_id,
                artifact=artifact,
                filename=f"research-kb-{lease.request['scope']}-{preview['export_id']}.rkb-exchange.zip",
            )
            self.operation.complete(operation_lease)
            return {
                "status": "success",
                "result": result["result"],
                "export_id": result["export_id"],
                "selection": result["selection"],
                "record_count": result["record_count"],
                "source_count": result["source_count"],
                "archive_sha256": result["archive_sha256"],
                "archive_bytes": result["archive_bytes"],
                "download_token": download.token,
                "download_filename": download.filename,
                "download_ttl_seconds": self._EXCHANGE_LEASE_TTL_SECONDS,
                "persistent_writes": result["persistent_writes"],
                "canonical_scientific_write": False,
            }
        except asyncio.CancelledError:
            output.cleanup()
            self.operation.complete(operation_lease)
            raise
        except AppOperationError as error:
            output.cleanup()
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            output.cleanup()
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            output.cleanup()
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise

    def register_exchange_upload(
        self,
        *,
        browser_session_id: str,
        upload: ManagedExchangeFile,
    ) -> dict[str, Any]:
        self._drop_expired_exchange_leases()
        if self._exchange_lease_count() >= self._MAX_EXCHANGE_LEASES:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-LEASE-LIMIT",
                "Too many Exchange operations are waiting",
                status_code=429,
            )
        token = secrets.token_urlsafe(32)
        lease = ExchangeUploadLease(
            token=token,
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            upload=upload,
            expires_at=self._monotonic_clock() + self._EXCHANGE_LEASE_TTL_SECONDS,
        )
        self._exchange_uploads[token] = lease
        return {
            "status": "success",
            "upload_token": token,
            "archive_bytes": upload.size_bytes,
            "upload_ttl_seconds": self._EXCHANGE_LEASE_TTL_SECONDS,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    async def preview_exchange_import(
        self,
        *,
        browser_session_id: str,
        upload_token: str,
    ) -> dict[str, Any]:
        upload = self._consume_exchange_upload(upload_token, browser_session_id=browser_session_id)
        try:
            upload.upload.validate()
            preview = await self._run_exchange_worker(
                self._require_exchange().preview_import,
                self._require_session(),
                archive=upload.upload.path,
            )
            public = self._public_import_preview(preview)
            if preview["compatibility"] != "supported":
                upload.upload.cleanup()
                return {**public, "preview_token": None, "preview_ttl_seconds": 0}
            lease = self._create_exchange_preview(
                kind="import",
                browser_session_id=browser_session_id,
                request={},
                preview=preview,
                upload=upload.upload,
                created_at=_utc_timestamp(),
            )
            return {
                **public,
                "preview_token": lease.token,
                "preview_ttl_seconds": self._EXCHANGE_LEASE_TTL_SECONDS,
            }
        except asyncio.CancelledError:
            upload.upload.cleanup()
            raise
        except Exception:
            upload.upload.cleanup()
            raise

    async def apply_exchange_import(
        self,
        *,
        browser_session_id: str,
        preview_token: str,
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("exchange")
        lease: ExchangePreviewLease | None = None
        try:
            lease = self._consume_exchange_preview(
                preview_token,
                kind="import",
                browser_session_id=browser_session_id,
            )
            if lease.upload is None:
                raise AppOperationError(
                    "RKBAPP-EXCHANGE-UPLOAD-MISSING",
                    "Exchange import preview has no bound upload",
                )
            lease.upload.validate()
            preview = lease.preview
            result = await self._run_exchange_worker(
                self._require_exchange().apply_import,
                self._require_session(),
                {
                    "import_id": preview["import_id"],
                    "expected_archive_sha256": preview["archive_sha256"],
                    "expected_basis_digest": preview["basis_digest"],
                    "created_at": lease.created_at,
                },
                archive=lease.upload.path,
                actor="user",
            )
            self.operation.complete(operation_lease)
            return {
                "status": "success",
                "result": result["result"],
                "import_id": result["import_id"],
                "origin_workspace_id": result["origin_workspace_id"],
                "record_count": result["record_count"],
                "source_count": result["source_count"],
                "trust_projection": result["trust_projection"],
                "persistent_writes": result["persistent_writes"],
                "canonical_scientific_write": False,
            }
        except asyncio.CancelledError:
            self.operation.complete(operation_lease)
            raise
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        finally:
            if lease is not None and lease.upload is not None:
                lease.upload.cleanup()

    def consume_exchange_download(
        self,
        *,
        browser_session_id: str,
        download_token: str,
    ) -> ExchangeDownloadLease:
        self._drop_expired_exchange_leases()
        lease = self._exchange_downloads.get(download_token)
        if lease is None:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-DOWNLOAD-STALE",
                "Exchange download is expired or already used",
            )
        if (
            lease.browser_session_id != browser_session_id
            or lease.workspace_option_id != self._require_active_option()
        ):
            raise AppOperationError(
                "RKBAPP-EXCHANGE-DOWNLOAD-BINDING",
                "Exchange download does not match the current session",
            )
        self._exchange_downloads.pop(download_token, None)
        return lease

    def list_exchange_imports(self) -> dict[str, Any]:
        return self._require_exchange().list_imports(self._require_session())

    def show_exchange_import(self, import_id: str) -> dict[str, Any]:
        return self._require_exchange().show_import(self._require_session(), import_id)

    def obsidian_status(self, *, page_size: int, cursor: str | None) -> dict[str, Any]:
        service = self._require_obsidian()
        session = self._require_session()
        limit = min(
            service.limits(session)["max_status_page_size"],
            self.config.request_budgets.max_page_size,
        )
        if not 1 <= page_size <= limit:
            raise AppOperationError(
                "RKBAPP-PAGE-LIMIT",
                "Obsidian view page size exceeds the supported budget",
                status_code=400,
            )
        return self._public_obsidian_status(
            service.status(session, page_size=page_size, cursor=cursor)
        )

    def obsidian_targets(self) -> dict[str, Any]:
        option_id = self._require_active_option()
        targets = self.config.obsidian_targets_for(option_id)
        return {
            "status": "success",
            "targets": [
                {"target_id": target.target_id, "label": target.label}
                for target in targets
            ],
            "preview_ttl_seconds": self._OBSIDIAN_PREVIEW_TTL_SECONDS,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def preview_obsidian_render(
        self,
        *,
        browser_session_id: str,
        optional_tables: list[str],
    ) -> dict[str, Any]:
        service = self._require_obsidian()
        session = self._require_session()
        preview = service.preview_render(session, optional_tables=optional_tables)
        expected_state = preview.get("expected_state")
        if not isinstance(expected_state, dict):
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-PREVIEW",
                "Core did not return a closed render preview",
            )
        self._invalidate_obsidian_leases(kind="render")
        lease = self._create_obsidian_lease(
            kind="render",
            browser_session_id=browser_session_id,
            target_id=None,
            optional_tables=tuple(optional_tables),
            expected_state=dict(expected_state),
        )
        return {
            **self._public_render_preview(preview),
            "preview_token": lease.token,
            "preview_ttl_seconds": self._OBSIDIAN_PREVIEW_TTL_SECONDS,
        }

    async def apply_obsidian_render(
        self,
        *,
        browser_session_id: str,
        preview_token: str,
        optional_tables: list[str],
        continuation: str,
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("obsidian")
        try:
            preview = self._consume_obsidian_lease(
                preview_token,
                kind="render",
                browser_session_id=browser_session_id,
                target_id=None,
                optional_tables=tuple(optional_tables),
            )
            result = await self._run_obsidian_worker(
                operation_lease,
                self._require_obsidian().render,
                self._require_session(),
                {
                    "optional_tables": list(preview.optional_tables),
                    "expected_state": dict(preview.expected_state or {}),
                    "discard_managed_edits": continuation == "discard_managed_edits",
                },
                actor="user",
            )
            self.operation.complete(operation_lease)
            return self._public_render_result(result)
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        finally:
            self._invalidate_obsidian_leases()

    def preview_obsidian_sync(
        self,
        *,
        browser_session_id: str,
        target_id: str,
    ) -> dict[str, Any]:
        target = self._require_obsidian_target(target_id)
        if self._active_obsidian_target_id != target_id:
            self._invalidate_obsidian_leases()
            self._active_obsidian_target_id = target_id
        source = self._collect_obsidian_source()
        preview = self.obsidian_sync.preview(
            target=target,
            workspace_option_id=self._require_active_option(),
            source=source,
        )
        expected_destination_state = preview.get("expected_destination_state")
        if not isinstance(expected_destination_state, str):
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-PREVIEW",
                "Obsidian target did not return a closed preview",
            )
        self._invalidate_obsidian_leases(kind="sync")
        lease = self._create_obsidian_lease(
            kind="sync",
            browser_session_id=browser_session_id,
            target_id=target_id,
            source=source,
            expected_destination_state=expected_destination_state,
        )
        return {
            **self._public_sync_preview(preview),
            "preview_token": lease.token,
            "preview_ttl_seconds": self._OBSIDIAN_PREVIEW_TTL_SECONDS,
        }

    async def apply_obsidian_sync(
        self,
        *,
        browser_session_id: str,
        target_id: str,
        preview_token: str,
        continuation: str,
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("obsidian")
        try:
            preview = self._consume_obsidian_lease(
                preview_token,
                kind="sync",
                browser_session_id=browser_session_id,
                target_id=target_id,
                optional_tables=(),
            )
            target = self._require_obsidian_target(target_id)
            source = await asyncio.to_thread(self._collect_obsidian_source)
            if source != preview.source:
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-STALE-PREVIEW",
                    "Generated Obsidian views changed after preview",
                )
            service = self._require_obsidian()
            session = self._require_session()
            result = await self._run_obsidian_worker(
                operation_lease,
                self.obsidian_sync.apply,
                target=target,
                workspace_option_id=self._require_active_option(),
                source=source,
                expected_destination_state=preview.expected_destination_state or "",
                continuation=continuation,
                stream_snapshot=lambda sink: service.stream_snapshot(
                    session,
                    expected_manifest_digest=source.manifest_digest,
                    sink=sink,
                ),
            )
            self.operation.complete(operation_lease)
            return self._public_sync_result(result)
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        finally:
            self._invalidate_obsidian_leases()

    async def _run_obsidian_worker(
        self,
        operation_lease: OperationLease,
        operation: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await worker
            except AppOperationError as error:
                self.operation.fail(operation_lease, error.code)
            except ResearchKBError as error:
                self.operation.fail(operation_lease, error.diagnostic.code)
            except Exception:
                self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            else:
                self.operation.complete(operation_lease)
            raise
        return result

    def research_synthesis_limits(self) -> dict[str, Any]:
        return self._require_research_synthesis().limits(self._require_session())

    def list_research_synthesis_candidates(
        self,
        *,
        question_id: str | None,
        candidate_type: str | None,
        freshness: str | None,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        service = self._require_research_synthesis()
        limit = min(
            service.limits(self._require_session())["max_page_size"],
            self.config.request_budgets.max_page_size,
        )
        if not 1 <= page_size <= limit:
            raise AppOperationError(
                "RKBAPP-PAGE-LIMIT",
                "Research Synthesis page size exceeds the supported budget",
                status_code=400,
            )
        return service.list_candidates(
            self._require_session(),
            question_id=question_id,
            candidate_type=candidate_type,
            freshness=freshness,
            page_size=page_size,
            cursor=cursor,
        )

    def show_research_synthesis_candidate(self, candidate_id: str) -> dict[str, Any]:
        return self._require_research_synthesis().show_candidate(
            self._require_session(),
            candidate_id,
        )

    def show_research_synthesis_question_context(self, question_id: str) -> dict[str, Any]:
        return self._require_research_synthesis().question_context(
            self._require_session(),
            question_id,
        )

    def screening_limits(self) -> dict[str, Any]:
        return self._require_screening().limits(self._require_session())

    def list_screening_criteria(
        self,
        *,
        question_id: str | None,
        include_archived: bool,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        return self._require_screening().list_criteria(
            self._require_session(),
            question_id=question_id,
            include_archived=include_archived,
            page_size=page_size,
            cursor=cursor,
        )

    def show_screening_criteria(self, criteria_id: str) -> dict[str, Any]:
        return self._require_screening().show_criteria(self._require_session(), criteria_id)

    def list_screening_decisions(
        self,
        *,
        question_id: str | None,
        paper_id: str | None,
        outcome: str | None,
        freshness: str | None,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        return self._require_screening().list_decisions(
            self._require_session(),
            question_id=question_id,
            paper_id=paper_id,
            outcome=outcome,
            freshness=freshness,
            page_size=page_size,
            cursor=cursor,
        )

    def show_screening_decision(self, decision_id: str) -> dict[str, Any]:
        return self._require_screening().show_decision(self._require_session(), decision_id)

    async def promote_screening_criteria(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_screening_mutation(
            lambda: self._require_screening().promote_criteria(
                self._require_session(),
                {**request, "receipt_id": self._new_screening_receipt_id()},
            )
        )

    async def promote_screening_decision(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_screening_mutation(
            lambda: self._require_screening().promote_decision(
                self._require_session(),
                {**request, "receipt_id": self._new_screening_receipt_id()},
            )
        )

    def list_tags(
        self,
        *,
        include_archived: bool,
        page_size: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        service = self._require_tags()
        session = self._require_session()
        limit = min(
            service.limits(session)["max_page_size"],
            self.config.request_budgets.max_page_size,
        )
        if not 1 <= page_size <= limit:
            raise AppOperationError(
                "RKBAPP-PAGE-LIMIT",
                "Tag page size exceeds the supported budget",
                status_code=400,
            )
        return service.list_tags(
            session,
            include_archived=include_archived,
            page_size=page_size,
            cursor=cursor,
        )

    def show_tag(self, tag_id: str) -> dict[str, Any]:
        return self._require_tags().show_tag(self._require_session(), tag_id)

    def list_target_tags(self, target_kind: str, target_id: str) -> dict[str, Any]:
        return self._require_tags().list_target_tags(
            self._require_session(),
            target_kind=target_kind,
            target_id=target_id,
        )

    async def promote_tag(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_tag_mutation(
            lambda: self._require_tags().promote_tag(
                self._require_session(),
                {**request, "receipt_id": self._new_tag_receipt_id()},
            )
        )

    async def set_tag_assignment(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_tag_mutation(
            lambda: self._require_tags().set_assignment(
                self._require_session(),
                {**request, "receipt_id": self._new_tag_receipt_id()},
            )
        )

    def inspect_agent_handoff(
        self,
        task_id: str,
        expected_state: dict[str, str],
        executor_id: str,
    ) -> dict[str, Any]:
        return self._require_agent().inspect_handoff(
            self._require_session(),
            task_id,
            expected_state,
            executor_id,
        )

    def preview_agent_result(self, task_id: str) -> dict[str, Any]:
        return self._require_agent().preview_result(self._require_session(), task_id)

    def prepare_knowledge_query_answer_egress(
        self,
        task_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        preview = self._require_agent().preview_result(self._require_session(), task_id)
        task = preview.get("task")
        if not isinstance(task, dict):
            raise AppOperationError("RKBAPP-EGRESS-REPORT", "Current task report is unavailable", status_code=409)
        self._require_current_task_state(task, task_id, expected_state)
        if task.get("task_kind") != "knowledge_query_report" or task.get("status") not in {"submitted", "approved"}:
            raise AppOperationError("RKBAPP-EGRESS-REPORT", "Current task is not a copyable Knowledge Query report", status_code=409)
        candidate = preview.get("candidate")
        if not isinstance(candidate, dict):
            raise AppOperationError("RKBAPP-EGRESS-REPORT", "Current Knowledge Query report is unavailable", status_code=409)
        answer_blocks = candidate.get("answer_blocks")
        if not isinstance(answer_blocks, list):
            raise AppOperationError("RKBAPP-EGRESS-REPORT", "Current Knowledge Query answer is unavailable", status_code=409)
        answer_text = "\n\n".join(
            block["text"]
            for block in answer_blocks
            if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]
        )
        if not answer_text:
            raise AppOperationError("RKBAPP-EGRESS-REPORT", "Current Knowledge Query answer is empty", status_code=409)
        classes = task.get("effective_content_classes")
        if not isinstance(classes, list) or not classes or any(not isinstance(item, str) or not item for item in classes):
            raise AppOperationError("RKBAPP-EGRESS-CLASS", "Current task content classes are unavailable", status_code=409)
        return {"task_id": task_id, "text": answer_text, "content_classes": list(classes)}

    def prepare_task_metadata_egress(
        self,
        task_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        detail = self._require_agent().show_task(self._require_session(), task_id)
        task = detail.get("current_task")
        if not isinstance(task, dict):
            raise AppOperationError("RKBAPP-EGRESS-METADATA", "Current task metadata is unavailable", status_code=409)
        self._require_current_task_state(task, task_id, expected_state)
        metadata = {
            "task_id": task["task_id"],
            "task_kind": task.get("task_kind"),
            "result_contract": task.get("result_contract"),
            "executor_id": task.get("executor_id"),
            "status": task.get("status"),
            "revision": task.get("revision"),
            "updated_at": task.get("updated_at"),
        }
        return {
            "task_id": task_id,
            "text": json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            "content_classes": ["metadata"],
        }

    @staticmethod
    def _require_current_task_state(
        task: dict[str, Any],
        task_id: str,
        expected_state: dict[str, str],
    ) -> None:
        if task.get("task_id") != task_id:
            raise AppOperationError("RKBAPP-EGRESS-STALE", "Current task identity does not match the request", status_code=409)
        if (
            task.get("state_id") != expected_state.get("state_id")
            or task.get("state_digest") != expected_state.get("state_digest")
        ):
            raise AppOperationError("RKBAPP-EGRESS-STALE", "Current task state changed; refresh before copying", status_code=409)

    async def create_agent_task(self, job_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().create_from_pipeline(
                self._require_session(),
                job_id,
                request,
            )
        )

    async def create_knowledge_query(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().create_knowledge_query(
                self._require_session(),
                request,
            )
        )

    async def create_organization_proposal(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().create_organization_proposal(
                self._require_session(),
                request,
            )
        )

    async def create_research_synthesis_proposal(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().create_research_synthesis_proposal(
                self._require_session(),
                request,
            )
        )

    async def create_screening_criteria_proposal(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().create_question_screening_criteria_proposal(
                self._require_session(),
                request,
            )
        )

    async def create_screening_decision_proposal(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().create_question_screening_decision_proposal(
                self._require_session(),
                request,
            )
        )

    async def prepare_agent_handoff(
        self,
        task_id: str,
        expected_state: dict[str, str],
        executor_id: str,
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            result = self._require_agent().prepare_handoff(
                self._require_session(),
                task_id,
                expected_state,
                executor_id,
            )
            self._agent_leases[task_id] = dict(result["lease"])
            return {key: value for key, value in result.items() if key != "lease"}

        result = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return result

    async def submit_agent_result(
        self,
        task_id: str,
        expected_state: dict[str, str],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            service = self._require_agent()
            session = self._require_session()
            lease = self._agent_leases.get(task_id)
            if lease is None:
                current = service.show_task(session, task_id)["current_task"]
                if current["status"] != "leased":
                    raise AppOperationError(
                        "RKBAPP-AGENT-HANDOFF-REQUIRED",
                        "Prepare the external Agent handoff before submitting a result",
                        status_code=409,
                    )
                recovered = service.prepare_handoff(
                    session,
                    task_id,
                    expected_state,
                    current["executor_id"],
                )
                lease = dict(recovered["lease"])
                self._agent_leases[task_id] = lease
            submitted = service.submit_result(
                session,
                task_id,
                expected_state,
                lease,
                result,
            )
            self._agent_leases.pop(task_id, None)
            return {key: value for key, value in submitted.items() if key != "staged_result"}

        submitted = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return submitted

    async def request_agent_revision(
        self,
        task_id: str,
        expected_state: dict[str, str],
        feedback: str,
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            result = self._require_agent().request_revision(
                self._require_session(),
                task_id,
                expected_state,
                feedback,
            )
            self._agent_leases.pop(task_id, None)
            return result

        result = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return result

    async def refresh_agent_task(
        self,
        task_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            service = self._require_agent()
            session = self._require_session()
            task = service.show_task(session, task_id)["current_task"]
            if task["task_kind"] == "primary_semantic_processing":
                result = service.refresh_primary_task(session, task_id, expected_state)
            elif task["task_kind"] == "review_semantic_processing":
                result = service.refresh_review_task(session, task_id, expected_state)
            else:
                raise AppOperationError(
                    "RKBAPP-AGENT-REFRESH-UNSUPPORTED",
                    "This Agent Task kind does not support input refresh",
                    status_code=409,
                )
            self._agent_leases.pop(task_id, None)
            return result

        result = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return result

    def source_adequacy_resolution_context(self, task_id: str) -> dict[str, Any]:
        return self._require_source_adequacy_resolution().show_context(
            self._require_session(),
            task_id,
        )

    def intake_source_adequacy_resolution_context(self, job_id: str) -> dict[str, Any]:
        return self._require_intake_source_adequacy_resolution().show_context(
            self._require_session(),
            job_id,
        )

    async def open_source_adequacy_review(
        self,
        browser_session_id: str,
        task_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("agent_task")

        def invoke() -> dict[str, Any]:
            service = self._require_source_adequacy_resolution()
            session = self._require_session()
            prepared = service.prepare_source_review(session, task_id, expected_state)
            with service.open_source_review(session, prepared.handle) as opened:
                launch = self.pdf_launcher.launch(opened.path)
            confirmation = self.source_review_confirmations.issue(
                browser_session_id=browser_session_id,
                workspace_option_id=self._require_active_option(),
                task_id=task_id,
                task_state_id=expected_state["state_id"],
                task_state_digest=expected_state["state_digest"],
                basis_profile_id=prepared.descriptor["basis_profile_id"],
                basis_profile_digest=prepared.handle.basis_profile_digest,
            )
            return {
                "status": "success",
                "task_id": task_id,
                "basis_profile_id": prepared.descriptor["basis_profile_id"],
                "reader": {"provider": launch.reader},
                "confirmation": confirmation,
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            }

        try:
            result = await asyncio.to_thread(invoke)
            self.operation.complete(operation_lease)
            return result
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise

        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise

    async def open_intake_source_adequacy_review(
        self,
        browser_session_id: str,
        job_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("intake")

        def invoke() -> dict[str, Any]:
            service = self._require_intake_source_adequacy_resolution()
            session = self._require_session()
            prepared = service.prepare_source_review(session, job_id, expected_state)
            with service.open_source_review(session, prepared.handle) as opened:
                launch = self.pdf_launcher.launch(opened.path)
            confirmation = self.source_review_confirmations.issue(
                browser_session_id=browser_session_id,
                workspace_option_id=self._require_active_option(),
                subject_kind="intake_job",
                subject_id=job_id,
                subject_state_id=expected_state["state_id"],
                subject_state_digest=expected_state["state_digest"],
                basis_profile_id=prepared.descriptor["basis_profile_id"],
                basis_profile_digest=prepared.handle.basis_profile_digest,
            )
            return {
                "status": "success",
                "job_id": job_id,
                "basis_profile_id": prepared.descriptor["basis_profile_id"],
                "reader": {"provider": launch.reader},
                "confirmation": confirmation,
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            }

        try:
            result = await asyncio.to_thread(invoke)
            self.operation.complete(operation_lease)
            return {**result, "operation": self.operation.public()}
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise

    async def decide_source_adequacy_resolution(
        self,
        browser_session_id: str,
        task_id: str,
        expected_state: dict[str, str],
        action: str,
        confirmation_id: str | None,
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            resolution = self._require_source_adequacy_resolution()
            session = self._require_session()
            context = resolution.show_context(session, task_id)
            if action == "accept_uncertainty":
                replayable = (
                    context["resolution_state"] in {"accepted_refresh_required", "not_required"}
                    and context.get("decision_action") == action
                )
                if not replayable:
                    if not confirmation_id:
                        raise AppOperationError(
                            "RKBAPP-SOURCE-REVIEW-REQUIRED",
                            "Open the source and confirm its reading order before acceptance",
                            status_code=409,
                        )
                    prepared = resolution.prepare_source_review(
                        session,
                        task_id,
                        expected_state,
                    )
                    self.source_review_confirmations.require(
                        confirmation_id,
                        browser_session_id=browser_session_id,
                        workspace_option_id=self._require_active_option(),
                        task_id=task_id,
                        task_state_id=expected_state["state_id"],
                        task_state_digest=expected_state["state_digest"],
                        basis_profile_id=prepared.descriptor["basis_profile_id"],
                        basis_profile_digest=prepared.handle.basis_profile_digest,
                    )
                decision = resolution.decide(
                    session,
                    task_id,
                    expected_state,
                    context["basis_profile_id"],
                    action,
                    "reading_order_reviewed",
                )
            elif action == "remediation_required":
                if confirmation_id is not None:
                    raise AppOperationError(
                        "RKBAPP-SOURCE-REVIEW-CONFIRMATION",
                        "Remediation does not accept a source review confirmation",
                        status_code=400,
                    )
                decision = resolution.decide(
                    session,
                    task_id,
                    expected_state,
                    context["basis_profile_id"],
                    action,
                )
            else:
                raise AppOperationError(
                    "RKBAPP-SOURCE-ADEQUACY-ACTION",
                    "The Source Adequacy action is not supported",
                    status_code=400,
                )
            self.source_review_confirmations.invalidate_task(task_id)
            agent = self._require_agent()
            task_kind = context["task"]["task_kind"]
            if task_kind == "primary_semantic_processing":
                refreshed = agent.refresh_primary_task(session, task_id, expected_state)
            elif task_kind == "review_semantic_processing":
                refreshed = agent.refresh_review_task(session, task_id, expected_state)
            else:
                raise AppOperationError(
                    "RKBAPP-AGENT-REFRESH-UNSUPPORTED",
                    "This Agent Task kind does not support input refresh",
                    status_code=409,
                )
            self._agent_leases.pop(task_id, None)
            return {
                **refreshed,
                "resolution": decision,
                "persistent_writes": decision["persistent_writes"] + refreshed["persistent_writes"],
                "canonical_scientific_write": False,
            }

        result = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return result

    async def decide_intake_source_adequacy_resolution(
        self,
        browser_session_id: str,
        job_id: str,
        expected_state: dict[str, str],
        action: str,
        confirmation_id: str | None,
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            service = self._require_intake_source_adequacy_resolution()
            session = self._require_session()
            context = service.show_context(session, job_id)
            resolution_state = context.get("resolution_state")
            decision_action = context.get("decision_action")
            replayable = (
                action == "accept_uncertainty"
                and decision_action == action
                and resolution_state
                in {
                    "accepted_continuation_required",
                    "continuation_in_progress",
                    "continued",
                }
            ) or (
                action == "remediation_required"
                and decision_action == action
                and resolution_state == "remediation_required"
            )
            if action == "accept_uncertainty":
                if not replayable:
                    if not confirmation_id:
                        raise AppOperationError(
                            "RKBAPP-SOURCE-REVIEW-REQUIRED",
                            "Open the source and confirm its basic reading before acceptance",
                            status_code=409,
                        )
                    prepared = service.prepare_source_review(session, job_id, expected_state)
                    self.source_review_confirmations.require(
                        confirmation_id,
                        browser_session_id=browser_session_id,
                        workspace_option_id=self._require_active_option(),
                        subject_kind="intake_job",
                        subject_id=job_id,
                        subject_state_id=expected_state["state_id"],
                        subject_state_digest=expected_state["state_digest"],
                        basis_profile_id=prepared.descriptor["basis_profile_id"],
                        basis_profile_digest=prepared.handle.basis_profile_digest,
                    )
                    self.source_review_confirmations.consume(confirmation_id)
                attestation = "basic_source_reviewed"
            elif action == "remediation_required":
                if confirmation_id is not None:
                    raise AppOperationError(
                        "RKBAPP-SOURCE-REVIEW-CONFIRMATION",
                        "Remediation does not accept a source review confirmation",
                        status_code=400,
                    )
                attestation = None
            else:
                raise AppOperationError(
                    "RKBAPP-SOURCE-ADEQUACY-ACTION",
                    "The Source Adequacy action is not supported",
                    status_code=400,
                )

            decision = service.decide_and_continue(
                session,
                job_id,
                expected_state,
                action,
                attestation,
            )
            self.source_review_confirmations.invalidate_subject("intake_job", job_id)
            return {**decision, "canonical_scientific_write": False}

        result = await self._run_intake_source_adequacy_mutation(invoke)
        self.source_review_confirmations.invalidate_subject("intake_job", job_id)
        return result

    async def reject_agent_result(
        self,
        task_id: str,
        expected_state: dict[str, str],
        reason_code: str,
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            result = self._require_agent().reject_result(
                self._require_session(),
                task_id,
                expected_state,
                reason_code,
            )
            self._agent_leases.pop(task_id, None)
            return result

        result = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return result

    async def approve_agent_result(
        self,
        task_id: str,
        expected_state: dict[str, str],
    ) -> dict[str, Any]:
        def invoke() -> dict[str, Any]:
            service = self._require_agent()
            session = self._require_session()
            task_kind = service.show_task(session, task_id)["current_task"]["task_kind"]
            if task_kind == "document_route_resolution":
                result = service.approve_route_result(session, task_id, expected_state)
            elif task_kind == "primary_semantic_processing":
                result = service.approve_primary_result(session, task_id, expected_state)
            elif task_kind == "review_semantic_processing":
                result = service.approve_review_result(session, task_id, expected_state)
            else:
                raise AppOperationError(
                    "RKBAPP-AGENT-APPROVAL-UNSUPPORTED",
                    "This Agent Task kind cannot be approved by the App",
                    status_code=409,
                )
            self._agent_leases.pop(task_id, None)
            return result

        result = await self._run_agent_mutation(invoke)
        self.source_review_confirmations.invalidate_task(task_id)
        return result

    async def accept_knowledge_query_report(
        self,
        task_id: str,
        expected_state: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().accept_report(
                self._require_session(),
                task_id,
                expected_state,
            )
        )

    async def approve_organization_result(
        self,
        task_id: str,
        expected_state: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().approve_organization_result(
                self._require_session(),
                task_id,
                expected_state,
            )
        )

    async def approve_research_synthesis_result(
        self,
        task_id: str,
        expected_state: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().approve_research_synthesis_result(
                self._require_session(),
                task_id,
                expected_state,
            )
        )

    async def approve_screening_result(
        self,
        task_id: str,
        expected_state: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._run_agent_mutation(
            lambda: self._require_agent().approve_question_screening_result(
                self._require_session(),
                task_id,
                expected_state,
            )
        )

    async def _run_agent_mutation(self, invoke: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        operation_lease = self.operation.acquire("agent_task")
        entered_core = False
        schedule_rebuild = False
        try:
            entered_core = True
            result = await asyncio.to_thread(invoke)
            schedule_rebuild = result.get("persistent_writes", 0) > 0
            if schedule_rebuild:
                self._mark_catalog_stale()
            self.operation.complete(operation_lease)
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            if entered_core:
                self._mark_catalog_stale()
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            if entered_core:
                self._mark_catalog_stale()
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        if schedule_rebuild:
            self._try_schedule_rebuild()
        return {**result, "operation": self.operation.public()}

    async def _run_intake_source_adequacy_mutation(
        self,
        invoke: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        operation_lease = self.operation.acquire("intake")
        entered_core = False
        schedule_rebuild = False
        try:
            entered_core = True
            result = await asyncio.to_thread(invoke)
            schedule_rebuild = result.get("persistent_writes", 0) > 0
            if schedule_rebuild:
                self._mark_catalog_stale()
            self.operation.complete(operation_lease)
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            if entered_core:
                self._mark_catalog_stale()
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            if entered_core:
                self._mark_catalog_stale()
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        if schedule_rebuild:
            self._try_schedule_rebuild()
        return {**result, "operation": self.operation.public()}

    async def _run_tag_mutation(self, invoke: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        operation_lease = self.operation.acquire("tag")
        schedule_rebuild = False
        try:
            result = await asyncio.to_thread(invoke)
            schedule_rebuild = result.get("persistent_writes", 0) > 0
            if schedule_rebuild:
                self._mark_catalog_stale()
            self.operation.complete(operation_lease)
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        if schedule_rebuild:
            self._try_schedule_rebuild()
        return {**result, "operation": self.operation.public()}

    async def _run_screening_mutation(self, invoke: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        operation_lease = self.operation.acquire("screening")
        schedule_rebuild = False
        try:
            result = await asyncio.to_thread(invoke)
            schedule_rebuild = result.get("persistent_writes", 0) > 0
            if schedule_rebuild:
                self._mark_catalog_stale()
            self.operation.complete(operation_lease)
        except AppOperationError as error:
            self.operation.fail(operation_lease, error.code)
            raise
        except ResearchKBError as error:
            self.operation.fail(operation_lease, error.diagnostic.code)
            raise
        except Exception:
            self.operation.fail(operation_lease, "RKBAPP-INTERNAL")
            raise
        if schedule_rebuild:
            self._try_schedule_rebuild()
        return {**result, "operation": self.operation.public()}

    @staticmethod
    def _new_tag_receipt_id() -> str:
        return f"app-tag-{uuid.uuid4().hex}"

    @staticmethod
    def _new_screening_receipt_id() -> str:
        return f"app-screening-{uuid.uuid4().hex}"

    def search_catalog(self, **filters: Any) -> dict[str, Any]:
        result = self._require_query().search(**filters)
        if self._catalog_status is not None:
            result["projection_state"] = self._catalog_status["projection_state"]
        return result

    def catalog_detail(self, item_id: str) -> dict[str, Any]:
        result = self._require_query().detail(item_id)
        if self._catalog_status is not None:
            result["projection_state"] = self._catalog_status["projection_state"]
        return result

    def show_reading_paper(self, paper_id: str) -> dict[str, Any]:
        return self._require_reading().show_paper(self._require_session(), paper_id)

    def compare_reading_papers(self, paper_ids: list[str]) -> dict[str, Any]:
        return self._require_reading().compare_papers(self._require_session(), paper_ids)

    def trace_reading_evidence(self, evidence_id: str) -> dict[str, Any]:
        return self._require_reading().trace_evidence(self._require_session(), evidence_id)

    def issue_evidence_pdf_handle(
        self,
        browser_session_id: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        prepared = self._require_reading().prepare_evidence_source(
            self._require_session(),
            evidence_id,
        )
        return self.pdf_handles.issue(
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            core_handle=prepared.handle,
            descriptor=prepared.descriptor,
        )

    def open_evidence_pdf(self, browser_session_id: str, handle_id: str):
        entry = self.pdf_handles.require(
            handle_id,
            browser_session_id,
            self._require_active_option(),
        )
        return self._require_reading().open_evidence_source(
            self._require_session(),
            entry.core_handle,
        )

    def open_evidence_in_external_reader(
        self,
        browser_session_id: str,
        handle_id: str,
    ) -> dict[str, Any]:
        with self.open_evidence_pdf(browser_session_id, handle_id) as opened:
            launched = self.pdf_launcher.launch(opened.path)
            return {
                "status": "success",
                "reader": launched.reader,
                "page_targeting": launched.page_targeting,
                "pdf_page": opened.pdf_page,
                "locator": opened.locator,
            }

    def capabilities(self) -> dict[str, Any]:
        core = CapabilityService().show()
        operational_acceptance = self._operational_acceptance_facts(core)
        return {
            "status": "success",
            "app": {
                "version": __version__,
                "surface": "p11-operational-workspace",
                "canonical_scientific_writes": "user_approved_only",
                "deterministic_intake": True,
                "embedded_agent_runtime": False,
                "workspace_selected": self.active_option_id is not None,
                "operational_acceptance": operational_acceptance,
            },
            "core": core,
            "catalog": CatalogCapabilityService().show(),
        }

    def health(self) -> dict[str, Any]:
        projection_state = "not_selected"
        if self.projection is not None and self._catalog_status is not None:
            projection_state = self._catalog_status["projection_state"]
        operational_acceptance = {
            **self._operational_acceptance_facts(CapabilityService().show()),
            "projection_rebuildable": True,
        }
        return {
            "status": "success",
            "process_ready": True,
            "core_compatible": True,
            "workspace_selected": self.active_option_id is not None,
            "projection_state": projection_state,
            "operational_acceptance": operational_acceptance,
            "operation": self.operation.public(),
        }

    def request_shutdown(self) -> dict[str, str]:
        if self.operation.is_busy() and not self.operation.request_shutdown_cancel():
            self._require_idle()
        self._agent_leases.clear()
        self._obsidian_leases.clear()
        self._clear_exchange_leases()
        self.pdf_handles.clear()
        self.source_review_confirmations.clear()
        self.shutdown_requested.set()
        return {"status": "accepted"}

    async def close(self) -> None:
        operation = getattr(self, "operation", None)
        if operation is not None:
            operation.request_shutdown_cancel()
        pdf_handles = getattr(self, "pdf_handles", None)
        if pdf_handles is not None:
            pdf_handles.clear()
        source_review_confirmations = getattr(self, "source_review_confirmations", None)
        if source_review_confirmations is not None:
            source_review_confirmations.clear()
        obsidian_leases = getattr(self, "_obsidian_leases", None)
        if obsidian_leases is not None:
            obsidian_leases.clear()
        exchange_uploads = getattr(self, "_exchange_uploads", None)
        if exchange_uploads is not None:
            self._clear_exchange_leases()
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        trusted_parse_leases = getattr(self, "trusted_parse_leases", None)
        if trusted_parse_leases is not None:
            trusted_parse_leases.clear()

    def _collect_obsidian_source(self) -> SourceProjection:
        service = self._require_obsidian()
        session = self._require_session()
        page_size = min(
            service.limits(session)["max_status_page_size"],
            self.config.request_budgets.max_page_size,
        )
        first = service.status(session, page_size=page_size, cursor=None)
        entries = list(first.get("entries", []))
        cursor = first.get("next_cursor")
        binding = self._obsidian_status_binding(first)
        while cursor is not None:
            if len(entries) >= MAX_SYNC_FILES:
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-SOURCE-LIMIT",
                    "Generated Obsidian view inventory exceeds the synchronization budget",
                )
            page = service.status(session, page_size=page_size, cursor=cursor)
            if self._obsidian_status_binding(page) != binding:
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-STALE-PREVIEW",
                    "Generated Obsidian views changed during inventory pagination",
                )
            page_entries = page.get("entries")
            if not isinstance(page_entries, list) or not page_entries:
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-SOURCE-PAGE",
                    "Generated Obsidian view inventory pagination is incomplete",
                )
            entries.extend(page_entries)
            next_cursor = page.get("next_cursor")
            if next_cursor == cursor:
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-SOURCE-PAGE",
                    "Generated Obsidian view inventory cursor did not advance",
                )
            cursor = next_cursor
        return source_projection_from_status(first, entries)

    @staticmethod
    def _obsidian_status_binding(status: dict[str, Any]) -> tuple[Any, ...]:
        return (
            status.get("projection_state"),
            status.get("integrity_state"),
            status.get("generation_id"),
            status.get("manifest_digest"),
            status.get("source_watermark"),
            tuple(status.get("optional_tables", [])),
            status.get("file_count"),
            status.get("current_count"),
            status.get("stale_count"),
        )

    def _create_obsidian_lease(
        self,
        *,
        kind: str,
        browser_session_id: str,
        target_id: str | None,
        optional_tables: tuple[str, ...] = (),
        expected_state: dict[str, Any] | None = None,
        source: SourceProjection | None = None,
        expected_destination_state: str | None = None,
    ) -> ObsidianPreviewLease:
        self._drop_expired_obsidian_leases()
        if len(self._obsidian_leases) >= self._MAX_OBSIDIAN_PREVIEW_LEASES:
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-PREVIEW-LIMIT",
                "Too many Obsidian previews are active",
                status_code=429,
            )
        token = secrets.token_urlsafe(32)
        lease = ObsidianPreviewLease(
            token=token,
            kind=kind,
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            target_id=target_id,
            optional_tables=optional_tables,
            expected_state=expected_state,
            source=source,
            expected_destination_state=expected_destination_state,
            expires_at=self._monotonic_clock() + self._OBSIDIAN_PREVIEW_TTL_SECONDS,
        )
        self._obsidian_leases[token] = lease
        return lease

    def _consume_obsidian_lease(
        self,
        token: str,
        *,
        kind: str,
        browser_session_id: str,
        target_id: str | None,
        optional_tables: tuple[str, ...],
    ) -> ObsidianPreviewLease:
        self._drop_expired_obsidian_leases()
        lease = self._obsidian_leases.get(token)
        if lease is None:
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-PREVIEW-STALE",
                "Obsidian preview is expired or already used",
            )
        if (
            lease.kind != kind
            or lease.browser_session_id != browser_session_id
            or lease.workspace_option_id != self._require_active_option()
            or lease.target_id != target_id
            or lease.optional_tables != optional_tables
        ):
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-PREVIEW-BINDING",
                "Obsidian preview does not match the current request",
            )
        self._obsidian_leases.pop(token, None)
        return lease

    def _drop_expired_obsidian_leases(self) -> None:
        now = self._monotonic_clock()
        expired = [
            token
            for token, lease in self._obsidian_leases.items()
            if lease.expires_at <= now
        ]
        for token in expired:
            self._obsidian_leases.pop(token, None)

    def _invalidate_obsidian_leases(self, *, kind: str | None = None) -> None:
        if kind is None:
            self._obsidian_leases.clear()
            return
        for token in [
            token for token, lease in self._obsidian_leases.items() if lease.kind == kind
        ]:
            self._obsidian_leases.pop(token, None)

    def _require_obsidian_target(self, target_id: str):
        target = self.config.obsidian_target_mapping().get(target_id)
        if target is None or target.workspace_option_id != self._require_active_option():
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-TARGET-NOT-FOUND",
                "Configured Obsidian target does not exist",
                status_code=404,
            )
        return target

    @staticmethod
    def _public_obsidian_status(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": status.get("status", "success"),
            "projection_state": status.get("projection_state"),
            "integrity_state": status.get("integrity_state"),
            "generation_id": status.get("generation_id"),
            "optional_tables": list(status.get("optional_tables", [])),
            "file_count": status.get("file_count", 0),
            "current_count": status.get("current_count", 0),
            "stale_count": status.get("stale_count", 0),
            "edited_paths": list(status.get("edited_paths", [])),
            "edited_paths_truncated": bool(status.get("edited_paths_truncated", False)),
            "entries": [
                {
                    "logical_path": item.get("logical_path"),
                    "view_kind": item.get("view_kind"),
                    "view_id": item.get("view_id"),
                    "freshness": item.get("freshness"),
                    "freshness_reasons": list(item.get("freshness_reasons", [])),
                    "rendered_at": item.get("rendered_at"),
                }
                for item in status.get("entries", [])
            ],
            "next_cursor": status.get("next_cursor"),
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    @staticmethod
    def _public_render_preview(preview: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "projection_state",
            "integrity_state",
            "generation_id",
            "optional_tables",
            "proposed_file_count",
            "changed_file_count",
            "removed_file_count",
            "changed_paths",
            "changed_paths_truncated",
            "removed_paths",
            "removed_paths_truncated",
            "edited_paths",
            "edited_paths_truncated",
        )
        return {
            "status": "success",
            **{key: preview.get(key) for key in keys},
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    @staticmethod
    def _public_render_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": result.get("status", "success"),
            "result": result.get("result"),
            "generation_id": result.get("generation_id"),
            "file_count": result.get("file_count", 0),
            "changed_file_count": result.get("changed_file_count", 0),
            "removed_file_count": result.get("removed_file_count", 0),
            "persistent_writes": result.get("persistent_writes", 0),
            "canonical_scientific_write": False,
        }

    @staticmethod
    def _public_sync_preview(preview: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "target_id",
            "target_label",
            "source_generation_id",
            "source_file_count",
            "source_byte_count",
            "destination_state",
            "create_count",
            "update_count",
            "no_change_count",
            "remove_count",
            "edited_count",
            "missing_count",
            "unknown_count",
            "collision_count",
            "changed_paths",
            "changed_paths_truncated",
            "conflict_paths",
            "conflict_paths_truncated",
        )
        return {
            "status": "success",
            **{key: preview.get(key) for key in keys},
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    @staticmethod
    def _public_sync_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "result": result.get("result"),
            "target_id": result.get("target_id"),
            "source_generation_id": result.get("source_generation_id"),
            "file_count": result.get("file_count", 0),
            "byte_count": result.get("byte_count", 0),
            "continuation": result.get("continuation"),
            "personal_copy": result.get("personal_copy"),
            "persistent_writes": 1 if result.get("result") == "committed" else 0,
            "canonical_scientific_write": False,
        }

    def _mark_catalog_stale(self) -> None:
        if self._catalog_status is None:
            return
        if self.query is not None:
            self.query.mark_stale()
        self._catalog_status = {
            **self._catalog_status,
            "projection_state": "stale",
            "freshness_verification": "upstream_write",
            "current_source_watermark": None,
        }

    def _operational_catalog_query(self) -> CatalogQueryService | None:
        if (
            self.query is not None
            and self._catalog_status is not None
            and self._catalog_status.get("projection_state") == "current"
        ):
            return self.query
        return None

    @staticmethod
    def _operational_acceptance_facts(core: dict[str, Any]) -> dict[str, bool]:
        features = core.get("features", {})
        return {
            "backup_restore": features.get("backup_restore") is True,
            "operational_maintenance": features.get("operational_maintenance") is True,
            "lazy_stale_maintenance": features.get("lazy_stale_maintenance") is True,
        }

    def _try_schedule_rebuild(self) -> None:
        try:
            self.start_rebuild()
        except AppOperationError:
            pass

    async def _run_exchange_worker(
        self,
        operation: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise

    def _create_exchange_preview(
        self,
        *,
        kind: str,
        browser_session_id: str,
        request: dict[str, Any],
        preview: dict[str, Any],
        upload: ManagedExchangeFile | None = None,
        created_at: str | None = None,
    ) -> ExchangePreviewLease:
        self._drop_expired_exchange_leases()
        if self._exchange_lease_count() >= self._MAX_EXCHANGE_LEASES:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-LEASE-LIMIT",
                "Too many Exchange operations are waiting",
                status_code=429,
            )
        token = secrets.token_urlsafe(32)
        lease = ExchangePreviewLease(
            token=token,
            kind=kind,
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            request=dict(request),
            preview=dict(preview),
            upload=upload,
            created_at=created_at,
            expires_at=self._monotonic_clock() + self._EXCHANGE_LEASE_TTL_SECONDS,
        )
        self._exchange_previews[token] = lease
        return lease

    def _consume_exchange_preview(
        self,
        token: str,
        *,
        kind: str,
        browser_session_id: str,
    ) -> ExchangePreviewLease:
        self._drop_expired_exchange_leases()
        lease = self._exchange_previews.get(token)
        if lease is None:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-PREVIEW-STALE",
                "Exchange preview is expired or already used",
            )
        if (
            lease.kind != kind
            or lease.browser_session_id != browser_session_id
            or lease.workspace_option_id != self._require_active_option()
        ):
            raise AppOperationError(
                "RKBAPP-EXCHANGE-PREVIEW-BINDING",
                "Exchange preview does not match the current request",
            )
        self._exchange_previews.pop(token, None)
        return lease

    def _consume_exchange_upload(
        self,
        token: str,
        *,
        browser_session_id: str,
    ) -> ExchangeUploadLease:
        self._drop_expired_exchange_leases()
        lease = self._exchange_uploads.get(token)
        if lease is None:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-UPLOAD-STALE",
                "Exchange upload is expired or already used",
            )
        if (
            lease.browser_session_id != browser_session_id
            or lease.workspace_option_id != self._require_active_option()
        ):
            raise AppOperationError(
                "RKBAPP-EXCHANGE-UPLOAD-BINDING",
                "Exchange upload does not match the current request",
            )
        self._exchange_uploads.pop(token, None)
        return lease

    def _create_exchange_download(
        self,
        *,
        browser_session_id: str,
        artifact: ManagedExchangeFile,
        filename: str,
    ) -> ExchangeDownloadLease:
        self._drop_expired_exchange_leases()
        if self._exchange_lease_count() >= self._MAX_EXCHANGE_LEASES:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-LEASE-LIMIT",
                "Too many Exchange operations are waiting",
                status_code=429,
            )
        token = secrets.token_urlsafe(32)
        lease = ExchangeDownloadLease(
            token=token,
            browser_session_id=browser_session_id,
            workspace_option_id=self._require_active_option(),
            artifact=artifact,
            filename=filename,
            expires_at=self._monotonic_clock() + self._EXCHANGE_LEASE_TTL_SECONDS,
        )
        self._exchange_downloads[token] = lease
        return lease

    def _exchange_lease_count(self) -> int:
        return len(self._exchange_uploads) + len(self._exchange_previews) + len(self._exchange_downloads)

    def _drop_expired_exchange_leases(self) -> None:
        now = self._monotonic_clock()
        for token, lease in list(self._exchange_uploads.items()):
            if lease.expires_at <= now:
                self._exchange_uploads.pop(token, None)
                lease.upload.cleanup()
        for token, lease in list(self._exchange_previews.items()):
            if lease.expires_at <= now:
                self._exchange_previews.pop(token, None)
                if lease.upload is not None:
                    lease.upload.cleanup()
        for token, lease in list(self._exchange_downloads.items()):
            if lease.expires_at <= now:
                self._exchange_downloads.pop(token, None)
                lease.artifact.cleanup()

    def _clear_exchange_leases(self) -> None:
        artifacts: dict[str, ManagedExchangeFile] = {}
        for lease in self._exchange_uploads.values():
            artifacts[lease.upload.operation_id] = lease.upload
        for lease in self._exchange_previews.values():
            if lease.upload is not None:
                artifacts[lease.upload.operation_id] = lease.upload
        for lease in self._exchange_downloads.values():
            artifacts[lease.artifact.operation_id] = lease.artifact
        self._exchange_uploads.clear()
        self._exchange_previews.clear()
        self._exchange_downloads.clear()
        for artifact in artifacts.values():
            artifact.cleanup()

    @staticmethod
    def _public_export_preview(preview: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "bundle_format": preview.get("bundle_format"),
            "selection": preview.get("selection"),
            "record_count": preview.get("record_count", 0),
            "record_kind_counts": preview.get("record_kind_counts", {}),
            "structured_bytes": preview.get("structured_bytes", 0),
            "estimated_archive_bytes": preview.get("estimated_archive_bytes", 0),
            "source_count": preview.get("source_count", 0),
            "pdf_count": preview.get("pdf_count", 0),
            "missing_source_count": preview.get("missing_source_count", 0),
            "rights_status": preview.get("rights_status"),
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    @staticmethod
    def _public_import_preview(preview: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "success",
            "compatibility": preview.get("compatibility"),
            "safe_reader_profile_id": preview.get("safe_reader_profile_id"),
            "archive_bytes": preview.get("archive_bytes", 0),
            "canonical_serialization": preview.get("canonical_serialization"),
            "import_id": preview.get("import_id"),
            "existing_import_id": preview.get("existing_import_id"),
            "origin_workspace_id": preview.get("origin_workspace_id"),
            "selection": preview.get("selection"),
            "record_count": preview.get("record_count", 0),
            "record_kind_counts": preview.get("record_kind_counts", {}),
            "source_count": preview.get("source_count", 0),
            "include_sources": preview.get("include_sources", False),
            "rights_assertion": preview.get("rights_assertion"),
            "trust_projection": preview.get("trust_projection"),
            "conflict_counts": preview.get("conflict_counts", {}),
            "conflicts": preview.get("conflicts", []),
            "conflicts_truncated": preview.get("conflicts_truncated", False),
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def _track(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _require_session(self) -> WorkspaceSession:
        if self.session is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.session

    def _require_exchange(self) -> ExchangeApplicationService:
        if self.exchange is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.exchange

    def _require_active_option(self) -> str:
        if self.active_option_id is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.active_option_id

    def _require_intake(self) -> DeterministicIntakeApplicationService:
        if self.intake is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.intake

    def _require_trusted_parse(self) -> TrustedParseIntakeApplicationService:
        if self.trusted_parse is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.trusted_parse

    def _require_agent(self) -> AgentTaskApplicationService:
        if self.agent is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.agent

    def _require_source_adequacy_resolution(self) -> SourceAdequacyResolutionApplicationService:
        if self.source_adequacy_resolution is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.source_adequacy_resolution

    def _require_intake_source_adequacy_resolution(
        self,
    ) -> IntakeSourceAdequacyResolutionApplicationService:
        if self.intake_source_adequacy_resolution is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.intake_source_adequacy_resolution

    def _require_reading(self) -> ReadingApplicationService:
        if self.reading is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.reading

    def _require_organization(self) -> ResearchOrganizationApplicationService:
        if self.organization is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.organization

    def _require_obsidian(self) -> ObsidianGeneratedViewsApplicationService:
        if self.obsidian is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.obsidian

    def _require_research_synthesis(self) -> ResearchSynthesisApplicationService:
        if self.research_synthesis is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.research_synthesis

    def _require_screening(self) -> QuestionScreeningApplicationService:
        if self.screening is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.screening

    def _require_tags(self) -> TagApplicationService:
        if self.tags is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.tags

    def _require_projection(self) -> CatalogProjectionService:
        if self.projection is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.projection

    def _require_query(self) -> CatalogQueryService:
        if self.query is None:
            raise AppOperationError("RKBAPP-WORKSPACE-REQUIRED", "Select a configured workspace first")
        return self.query

    def _require_idle(self) -> None:
        if self.operation.is_busy():
            raise AppOperationError("RKBAPP-OPERATION-BUSY", "A workspace operation is still running")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


__all__ = [
    "AppOperationError",
    "AppRuntime",
    "ObsidianPreviewLease",
    "OperationCoordinator",
    "OperationLease",
]
