from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Cookie, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from research_kb.errors import ResearchKBError

from research_kb_app import __version__
from research_kb_app.compatibility import CoreCompatibility
from research_kb_app.config import AppConfig
from research_kb_app.errors import AppOperationError, public_core_error
from research_kb_app.egress import EgressPolicyService
from research_kb_app.exchange_custody import ManagedExchangeFile, stream_exchange_upload
from research_kb_app.multipart import parse_multipart_stream
from research_kb_app.pdf_access import (
    PDF_STREAM_CHUNK_BYTES,
    RangeNotSatisfiable,
    ResolvedByteRange,
    resolve_byte_range,
)
from research_kb_app.runtime import AppRuntime
from research_kb_app.security import (
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthenticationError,
    BrowserSession,
    SessionManager,
)
from research_kb_app.setup_runtime import SetupRuntime


PATH_SHAPED_ID = re.compile(r"(?:[\\/:]|\.\.)")
AGENT_RESULT_BODY_LIMIT = 2 * 1024 * 1024
AGENT_RESULT_PATH = re.compile(r"^/api/agent/tasks/[^/]+/submit$")
DISCOVERY_SELECTION_BODY_LIMIT = 1024 * 1024
DISCOVERY_SELECTION_PATH = "/api/discovery/select"
EXCHANGE_ARCHIVE_MEDIA_TYPE = "application/vnd.research-kb.exchange+zip"
EXCHANGE_UPLOAD_PATH = "/api/exchange/import/upload"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BootstrapRequest(StrictModel):
    startup_token: str = Field(min_length=32, max_length=256)


class WorkspaceOpenRequest(StrictModel):
    option_id: str = Field(min_length=1, max_length=64)


class EmptyRequest(StrictModel):
    pass


class BibliographyRequest(StrictModel):
    title: str | None = Field(default=None, max_length=2048)
    authors: list[str] = Field(default_factory=list, max_length=256)
    year: int | None = Field(default=None, ge=0, le=9999)
    doi: str | None = Field(default=None, max_length=512)


class IntakeSemanticRequest(StrictModel):
    requested_operation: Literal["basic_paper_card", "basic_review_memory"]
    document_route: Literal["primary", "review"] | None
    route_reason: Literal["mixed_document"] | None
    bibliography: BibliographyRequest = Field(default_factory=BibliographyRequest)


class IntakeStartRequest(IntakeSemanticRequest):
    idempotency_key: str = Field(min_length=1, max_length=128)


class InboxStartRequest(IntakeStartRequest):
    candidate_token: str = Field(min_length=1, max_length=128)
    min_stable_age_seconds: int = Field(default=5, ge=0, le=86400)


class JobResumeRequest(IntakeSemanticRequest):
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobCancelRequest(StrictModel):
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrustedParsePrepareRequest(StrictModel):
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrustedParseApproveRequest(StrictModel):
    lease_token: str = Field(pattern=r"^trusted_parse_[A-Za-z0-9_-]{32,96}$")
    aggregate_preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExpectedAgentStateRequest(StrictModel):
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentTaskCreateRequest(StrictModel):
    paper_id: str = Field(min_length=1, max_length=128)
    task_kind: Literal[
        "document_route_resolution",
        "primary_semantic_processing",
        "review_semantic_processing",
    ]
    executor_id: Literal["codex_cli", "claude_code_cli"]
    approved_content_classes: list[str] = Field(min_length=1, max_length=16)
    idempotency_key: str = Field(min_length=1, max_length=128)


class AgentHandoffRequest(ExpectedAgentStateRequest):
    executor_id: Literal["codex_cli", "claude_code_cli"]


class AgentTaskPackageExportRequest(AgentHandoffRequest):
    selection_lease_id: str = Field(pattern=r"^selection_[0-9a-f]{48}$")


class AgentSubmitRequest(ExpectedAgentStateRequest):
    result: dict[str, Any]


class AgentRevisionRequest(ExpectedAgentStateRequest):
    feedback: str = Field(min_length=1, max_length=4000)


class AgentRejectRequest(ExpectedAgentStateRequest):
    reason_code: Literal["user_rejected"] = "user_rejected"


class SourceAdequacyResolutionDecisionRequest(ExpectedAgentStateRequest):
    action: Literal["accept_uncertainty", "remediation_required"]
    confirmation_id: str | None = Field(default=None, min_length=32, max_length=128)


class KnowledgeQueryCreateRequest(StrictModel):
    query_type: Literal[
        "single_paper_explanation",
        "seven_section_overview",
        "methods",
        "selected_paper_comparison",
        "trend_problem_discussion",
        "evidence_find",
    ]
    query_text: str = Field(min_length=1, max_length=2000)
    paper_ids: list[str] = Field(min_length=1, max_length=4)
    include_review_background: bool
    include_routing_context: bool
    executor_id: Literal["codex_cli", "claude_code_cli"]
    approved_content_classes: list[str] = Field(min_length=1, max_length=16)
    idempotency_key: str = Field(min_length=1, max_length=200)


class OrganizationProposalCreateRequest(StrictModel):
    target_kind: Literal["direction", "field_map_entry", "question"]
    target_id: str | None = Field(default=None, max_length=128)
    proposal_goal: str = Field(min_length=1, max_length=2000)
    paper_ids: list[str] = Field(min_length=1, max_length=25)
    include_review_background: bool
    executor_id: Literal["codex_cli", "claude_code_cli"]
    approved_content_classes: list[str] = Field(min_length=1, max_length=16)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ResearchSynthesisProposalCreateRequest(StrictModel):
    question_id: str = Field(min_length=1, max_length=128)
    candidate_type: Literal["synthesis", "review_angle", "insight", "cross_view"]
    maintenance_intent: Literal["append", "replace"]
    target_candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    maintenance_goal: str = Field(min_length=1, max_length=2000)
    include_review_background: bool
    executor_id: Literal["codex_cli", "claude_code_cli"]
    approved_content_classes: list[str] = Field(min_length=1, max_length=16)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ScreeningCriterionRequest(StrictModel):
    criterion_id: str | None = Field(default=None, min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=1000)


class ScreeningCriteriaPromoteRequest(StrictModel):
    criteria_id: str | None = Field(default=None, min_length=1, max_length=128)
    question_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=4000)
    inclusion_criteria: list[ScreeningCriterionRequest] = Field(default_factory=list, max_length=100)
    exclusion_criteria: list[ScreeningCriterionRequest] = Field(default_factory=list, max_length=100)
    notes: str = Field(default="", max_length=4000)
    status: Literal["active", "archived"] = "active"
    expected_revision_id: str | None = Field(default=None, min_length=1, max_length=128)


class ScreeningDispositionRequest(StrictModel):
    criterion_id: str = Field(min_length=1, max_length=128)
    disposition: Literal["met", "not_met", "not_applicable", "uncertain"]
    rationale: str = Field(min_length=1, max_length=2000)


class ScreeningDecisionPromoteRequest(StrictModel):
    decision_id: str | None = Field(default=None, min_length=1, max_length=128)
    question_id: str = Field(min_length=1, max_length=128)
    paper_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["included", "excluded"]
    criteria_revision_id: str = Field(min_length=1, max_length=128)
    criteria_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    criterion_dispositions: list[ScreeningDispositionRequest] = Field(min_length=1, max_length=200)
    basis_scope: Literal["metadata", "available_abstract", "paper_card", "user_full_text_review", "mixed"]
    rationale: str = Field(min_length=1, max_length=4000)
    known_limitations: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list, max_length=50)
    expected_revision_id: str | None = Field(default=None, min_length=1, max_length=128)


class ScreeningCriteriaProposalCreateRequest(StrictModel):
    question_id: str = Field(min_length=1, max_length=128)
    criteria_id: str | None = Field(default=None, min_length=1, max_length=128)
    proposal_goal: str = Field(min_length=1, max_length=2000)
    executor_id: Literal["codex_cli", "claude_code_cli"]
    approved_content_classes: list[str] = Field(min_length=1, max_length=16)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ScreeningDecisionProposalCreateRequest(StrictModel):
    question_id: str = Field(min_length=1, max_length=128)
    paper_id: str = Field(min_length=1, max_length=128)
    basis_scope: Literal["metadata", "paper_card", "mixed"]
    include_paper_card: bool
    executor_id: Literal["codex_cli", "claude_code_cli"]
    approved_content_classes: list[str] = Field(min_length=1, max_length=16)
    idempotency_key: str = Field(min_length=1, max_length=200)


class TagPromoteRequest(StrictModel):
    tag_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    aliases: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = Field(
        default=None,
        max_length=25,
    )
    status: Literal["active", "archived"] | None = None
    expected_revision_id: str | None = Field(default=None, min_length=1, max_length=128)


class TagAssignmentRequest(StrictModel):
    tag_id: str = Field(min_length=1, max_length=128)
    target_kind: Literal["paper", "direction", "field_map_entry", "question"]
    target_id: str = Field(min_length=1, max_length=128)
    state: Literal["assigned", "removed"]
    expected_revision_id: str | None = Field(default=None, min_length=1, max_length=128)


class ReadingCompareRequest(StrictModel):
    paper_ids: list[str] = Field(min_length=2, max_length=4)


class DiscoverySearchRequest(StrictModel):
    request_version: Literal["1.0"] = "1.0"
    date_from: date
    date_until: date
    title_keywords: list[str] = Field(default_factory=list, max_length=20)
    abstract_keywords: list[str] = Field(default_factory=list, max_length=20)
    keyword_mode: Literal["any", "all"]
    include_preprints: bool
    max_results: int = Field(ge=1, le=15)


class DiscoverySelectionRequest(StrictModel):
    report: dict[str, Any]
    result_keys: list[str] = Field(min_length=1, max_length=15)


class ObsidianRenderPreviewRequest(StrictModel):
    optional_tables: list[Literal["library_summary", "question_coverage"]] = Field(
        default_factory=list,
        max_length=2,
    )


class ObsidianRenderApplyRequest(ObsidianRenderPreviewRequest):
    preview_token: str = Field(min_length=32, max_length=128)
    continuation: Literal["render", "discard_managed_edits"]


class ObsidianSyncPreviewRequest(StrictModel):
    target_id: str = Field(min_length=1, max_length=128)


class ObsidianSyncApplyRequest(ObsidianSyncPreviewRequest):
    preview_token: str = Field(min_length=32, max_length=128)
    continuation: Literal[
        "sync",
        "discard_managed_edits",
        "export_personal_copy_then_sync",
    ]


class ExchangeExportPreviewRequest(StrictModel):
    scope: Literal["paper", "question", "direction", "workspace"]
    selector_id: str | None = Field(default=None, min_length=1, max_length=128)
    include_sources: bool = False
    rights_asserted: bool = False


class ExchangeTokenRequest(StrictModel):
    preview_token: str = Field(min_length=32, max_length=128)


class ExchangeUploadTokenRequest(StrictModel):
    upload_token: str = Field(min_length=32, max_length=128)


class SetupFolderSelectionRequest(StrictModel):
    purpose: Literal[
        "workspace_parent",
        "existing_workspace_config",
        "source_root",
        "local_inbox",
        "obsidian_vault",
        "task_package_destination",
        "backup_destination",
    ]
    allow_new_child: bool = False
    initial_location_id: Literal["home", "documents", "local_app_data"] | None = None


class SetupSourceRootRequest(StrictModel):
    root_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    selection_lease_id: str = Field(pattern=r"^selection_[0-9a-f]{48}$")


class SetupPrepareWorkspaceRequest(StrictModel):
    workspace_parent_lease_id: str = Field(pattern=r"^selection_[0-9a-f]{48}$")
    source_roots: list[SetupSourceRootRequest] = Field(min_length=1, max_length=32)
    local_inbox_lease_id: str = Field(pattern=r"^selection_[0-9a-f]{48}$")
    workspace_name: str = Field(min_length=1, max_length=80)
    workspace_label: str = Field(min_length=1, max_length=80)
    idempotency_key: str = Field(min_length=1, max_length=128)


class SetupCommitWorkspaceRequest(StrictModel):
    proposal_token: str = Field(pattern=r"^setup_[0-9a-f]{48}$")
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SetupAdoptionRequest(StrictModel):
    action: Literal["preview", "commit"]
    selection_lease_id: str | None = Field(default=None, pattern=r"^selection_[0-9a-f]{48}$")
    adoption_token: str | None = Field(default=None, pattern=r"^adoption_[0-9a-f]{48}$")
    preview_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    label: str | None = Field(default=None, min_length=1, max_length=80)


class SetupRecoveryActionRequest(StrictModel):
    action: Literal[
        "select_profile_revision",
        "resume_workspace_setup",
        "discard_workspace_staging",
        "restart_workspace_setup",
    ]
    revision_id: str | None = Field(default=None, pattern=r"^profile-rev-[0-9a-f]{32}$")
    operation_id: str | None = Field(
        default=None,
        pattern=r"^operation_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )


class ClipboardHandoffCopyRequest(StrictModel):
    action: Literal["agent_handoff"]
    task_id: str = Field(min_length=1, max_length=128)
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    executor_id: Literal["codex_cli", "claude_code_cli"]


class ClipboardKnowledgeQueryCopyRequest(StrictModel):
    action: Literal["knowledge_query_answer"]
    task_id: str = Field(min_length=1, max_length=128)
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ClipboardMetadataCopyRequest(StrictModel):
    action: Literal["metadata_only"]
    task_id: str = Field(min_length=1, max_length=128)
    expected_state_id: str = Field(min_length=1, max_length=128)
    expected_state_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    metadata_disclosure_accepted: bool = False


ClipboardCopyRequest = Annotated[
    ClipboardHandoffCopyRequest | ClipboardKnowledgeQueryCopyRequest | ClipboardMetadataCopyRequest,
    Field(discriminator="action"),
]


def create_app(
    config: AppConfig,
    compatibility: CoreCompatibility,
    *,
    startup_token: str,
    expected_host: str,
    expected_origin: str,
    setup_runtime: SetupRuntime | None = None,
    egress_policy: EgressPolicyService | None = None,
) -> FastAPI:
    sessions = SessionManager(startup_token)
    runtime = AppRuntime(config, compatibility)
    if setup_runtime is not None:
        setup_runtime.set_profile_committed_callback(runtime.replace_config)
    egress = egress_policy or EgressPolicyService()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            try:
                await runtime.close()
            finally:
                close_egress = getattr(egress, "close", None)
                if close_egress is not None:
                    close_egress()
                if setup_runtime is not None:
                    setup_runtime.clear()
                sessions.clear()

    app = FastAPI(
        title="Research KB App",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.sessions = sessions
    app.state.expected_origin = expected_origin
    app.state.setup_runtime = setup_runtime
    app.state.egress_policy = egress

    @app.middleware("http")
    async def request_security(request: Request, call_next):
        if request.headers.get("host", "").lower() != expected_host.lower():
            return _error(400, "RKBAPP-HOST", "Request Host is not accepted")
        if len(request.scope.get("query_string", b"")) > config.request_budgets.max_query_bytes:
            return _error(414, "RKBAPP-QUERY-LIMIT", "Query string exceeds the configured budget")
        if request.method == "POST":
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if request.headers.get("origin") != expected_origin:
                return _error(403, "RKBAPP-ORIGIN", "Request Origin is not accepted")
            intake_upload_request = request.url.path == "/api/intake/upload"
            exchange_upload_request = request.url.path == EXCHANGE_UPLOAD_PATH
            upload_request = intake_upload_request or exchange_upload_request
            agent_result_request = AGENT_RESULT_PATH.fullmatch(request.url.path) is not None
            if agent_result_request:
                body_limit = AGENT_RESULT_BODY_LIMIT
            elif request.url.path == DISCOVERY_SELECTION_PATH:
                body_limit = DISCOVERY_SELECTION_BODY_LIMIT
            else:
                body_limit = config.request_budgets.max_body_bytes
            if intake_upload_request:
                expected_content_type = "multipart/form-data"
            elif exchange_upload_request:
                expected_content_type = EXCHANGE_ARCHIVE_MEDIA_TYPE
            else:
                expected_content_type = "application/json"
            if content_type != expected_content_type:
                return _error(
                    415,
                    "RKBAPP-CONTENT-TYPE",
                    "Upload content type does not match the accepted contract"
                    if upload_request
                    else "POST requests require JSON",
                )
            content_length = request.headers.get("content-length")
            if exchange_upload_request and content_length is None:
                return _error(
                    411,
                    "RKBAPP-CONTENT-LENGTH",
                    "Exchange upload requires Content-Length",
                )
            if content_length is not None:
                try:
                    parsed_length = int(content_length)
                    if parsed_length < 0:
                        return _error(400, "RKBAPP-CONTENT-LENGTH", "Content-Length is invalid")
                    if not upload_request and parsed_length > body_limit:
                        return _error(413, "RKBAPP-BODY-LIMIT", "Request body exceeds the configured budget")
                except ValueError:
                    return _error(400, "RKBAPP-CONTENT-LENGTH", "Content-Length is invalid")
            if not upload_request:
                body = await request.body()
                if len(body) > body_limit:
                    return _error(413, "RKBAPP-BODY-LIMIT", "Request body exceeds the configured budget")
        try:
            response = await asyncio.wait_for(
                call_next(request),
                timeout=config.request_budgets.request_timeout_seconds,
            )
        except TimeoutError:
            response = _error(504, "RKBAPP-TIMEOUT", "Request exceeded the configured time budget")
        _apply_security_headers(response)
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_: Request, error: RequestValidationError):
        fields = [
            {
                "location": [str(part) for part in item.get("loc", ()) if part != "body"],
                "type": item.get("type", "validation_error"),
            }
            for item in error.errors()
        ]
        return _error(422, "RKBAPP-VALIDATION", "Request validation failed", details=fields)

    @app.exception_handler(AuthenticationError)
    async def authentication_handler(_: Request, error: AuthenticationError):
        return _error(401, "RKBAPP-AUTH", str(error))

    @app.exception_handler(AppOperationError)
    async def operation_handler(_: Request, error: AppOperationError):
        return _error(error.status_code, error.code, str(error))

    @app.exception_handler(RangeNotSatisfiable)
    async def range_handler(_: Request, error: RangeNotSatisfiable):
        response = _error(error.status_code, error.code, str(error))
        response.headers["Content-Range"] = f"bytes */{error.total_size}"
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ResearchKBError)
    async def core_error_handler(_: Request, error: ResearchKBError):
        status_code, code, message = public_core_error(error)
        return _error(status_code, code, message)

    @app.exception_handler(Exception)
    async def internal_error_handler(_: Request, __: Exception):
        return _error(500, "RKBAPP-INTERNAL", "The request could not be completed")

    @app.get("/api/runtime")
    async def show_runtime() -> dict[str, Any]:
        return {
            "status": "success",
            "app_version": __version__,
            "surface": "p11-operational-workspace",
            "bootstrap_required": True,
            "core": compatibility.public_facts(),
        }

    @app.post("/api/session/bootstrap")
    async def bootstrap_session(payload: BootstrapRequest, response: Response) -> dict[str, str]:
        session = sessions.bootstrap(payload.startup_token)
        response.set_cookie(
            SESSION_COOKIE,
            session.session_id,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )
        return {"status": "success"}

    @app.get("/api/session/csrf")
    async def show_csrf(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, str]:
        session = sessions.require(session_id)
        return {"status": "success", "csrf_token": session.csrf_token}

    @app.get("/api/workspaces")
    async def list_workspaces(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.list_workspaces()

    @app.get("/api/setup/status")
    async def setup_status(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if setup_runtime is None:
            return {
                "status": "success",
                "interface_version": "research-kb-app-setup@1.0",
                "mode": "explicit_config",
                "recovery_available": False,
            }
        return setup_runtime.status()

    @app.post("/api/setup/select-folder")
    async def setup_select_folder(
        payload: SetupFolderSelectionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if setup_runtime is None:
            raise AppOperationError("RKBAPP-SETUP-MODE", "Folder setup is unavailable in explicit-config mode")
        return await asyncio.to_thread(
            setup_runtime.select_folder,
            browser_session_id=session.session_id,
            purpose=payload.purpose,
            allow_new_child=payload.allow_new_child,
            initial_location_id=payload.initial_location_id,
        )

    @app.post("/api/setup/prepare-workspace")
    async def setup_prepare_workspace(
        payload: SetupPrepareWorkspaceRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if setup_runtime is None:
            raise AppOperationError("RKBAPP-SETUP-MODE", "Workspace setup is unavailable in explicit-config mode")
        return await asyncio.to_thread(
            setup_runtime.prepare_workspace,
            browser_session_id=session.session_id,
            workspace_parent_lease_id=payload.workspace_parent_lease_id,
            source_roots=[(item.root_id, item.selection_lease_id) for item in payload.source_roots],
            local_inbox_lease_id=payload.local_inbox_lease_id,
            workspace_name=payload.workspace_name,
            workspace_label=payload.workspace_label,
            idempotency_key=payload.idempotency_key,
        )

    @app.post("/api/setup/commit-workspace")
    async def setup_commit_workspace(
        payload: SetupCommitWorkspaceRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if setup_runtime is None:
            raise AppOperationError("RKBAPP-SETUP-MODE", "Workspace setup is unavailable in explicit-config mode")
        return await asyncio.to_thread(
            setup_runtime.commit_workspace,
            browser_session_id=session.session_id,
            proposal_token=payload.proposal_token,
            preview_digest=payload.preview_digest,
        )

    @app.post("/api/setup/adopt-workspace")
    async def setup_adopt_workspace(
        payload: SetupAdoptionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if setup_runtime is None:
            raise AppOperationError("RKBAPP-SETUP-MODE", "Workspace adoption is unavailable in explicit-config mode")
        if payload.action == "preview":
            if payload.selection_lease_id is None:
                raise AppOperationError("RKBAPP-VALIDATION", "Adoption selection is required", status_code=422)
            return await asyncio.to_thread(
                setup_runtime.preview_adoption,
                browser_session_id=session.session_id,
                selection_lease_id=payload.selection_lease_id,
            )
        if payload.adoption_token is None or payload.preview_digest is None or payload.label is None:
            raise AppOperationError("RKBAPP-VALIDATION", "Adoption approval fields are required", status_code=422)
        return await asyncio.to_thread(
            setup_runtime.commit_adoption,
            browser_session_id=session.session_id,
            adoption_token=payload.adoption_token,
            preview_digest=payload.preview_digest,
            label=payload.label,
        )

    @app.get("/api/setup/recovery")
    async def setup_recovery(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if setup_runtime is None:
            raise AppOperationError("RKBAPP-SETUP-MODE", "Managed setup recovery is unavailable in explicit-config mode")
        return setup_runtime.recovery()

    @app.post("/api/setup/recovery/action")
    async def setup_recovery_action(
        payload: SetupRecoveryActionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if setup_runtime is None:
            raise AppOperationError("RKBAPP-SETUP-MODE", "Managed setup recovery is unavailable in explicit-config mode")
        if payload.action == "select_profile_revision":
            if payload.revision_id is None or payload.operation_id is not None:
                raise AppOperationError("RKBAPP-VALIDATION", "Profile recovery fields are invalid", status_code=422)
            return await asyncio.to_thread(setup_runtime.recover_profile, payload.revision_id)
        if payload.operation_id is None or payload.revision_id is not None:
            raise AppOperationError("RKBAPP-VALIDATION", "Workspace recovery fields are invalid", status_code=422)
        return await asyncio.to_thread(setup_runtime.recover_workspace, payload.operation_id, payload.action)

    @app.get("/api/egress/policy")
    async def show_egress_policy(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return egress.show()

    @app.post("/api/egress/clipboard")
    async def copy_to_clipboard(
        payload: ClipboardCopyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.task_id)
        expected = {
            "state_id": payload.expected_state_id,
            "state_digest": payload.expected_state_digest,
        }
        if isinstance(payload, ClipboardHandoffCopyRequest):
            handoff_result = await runtime.prepare_agent_handoff(
                payload.task_id,
                expected,
                payload.executor_id,
            )
            handoff = handoff_result.get("handoff")
            if not isinstance(handoff, dict) or handoff.get("task_id") != payload.task_id:
                raise AppOperationError("RKBAPP-EGRESS-HANDOFF", "Current Agent handoff is unavailable", status_code=409)
            classes = handoff.get("effective_content_classes")
            if not isinstance(classes, list) or not classes:
                raise AppOperationError("RKBAPP-EGRESS-CLASS", "Current handoff content classes are unavailable", status_code=409)
            return await asyncio.to_thread(
                egress.copy_text,
                json.dumps(handoff, ensure_ascii=False, indent=2) + "\n",
                content_classes=classes,
                metadata_disclosure_accepted=True,
                user_action="explicit_handoff_copy",
            )
        if isinstance(payload, ClipboardKnowledgeQueryCopyRequest):
            derived = runtime.prepare_knowledge_query_answer_egress(payload.task_id, expected)
            return await asyncio.to_thread(
                egress.copy_text,
                derived["text"],
                content_classes=derived["content_classes"],
                metadata_disclosure_accepted=True,
                user_action="explicit_query_answer_copy",
            )
        if not payload.metadata_disclosure_accepted:
            raise AppOperationError(
                "RKBAPP-CLIPBOARD-DISCLOSURE",
                "Metadata clipboard disclosure must be accepted",
            )
        derived = runtime.prepare_task_metadata_egress(payload.task_id, expected)
        return await asyncio.to_thread(
            egress.copy_text,
            derived["text"],
            content_classes=derived["content_classes"],
            metadata_disclosure_accepted=True,
            user_action="explicit_metadata_copy",
        )

    @app.post("/api/egress/agent-task-package/{task_id}")
    async def export_agent_task_package(
        task_id: str,
        payload: AgentTaskPackageExportRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        if setup_runtime is None:
            raise AppOperationError(
                "RKBAPP-EGRESS-PACKAGE-MODE",
                "Agent task package export requires a managed product profile",
            )
        handoff_result = await runtime.prepare_agent_handoff(
            task_id,
            _agent_expected(payload),
            payload.executor_id,
        )
        return await asyncio.to_thread(
            setup_runtime.export_agent_task_package,
            browser_session_id=session.session_id,
            selection_lease_id=payload.selection_lease_id,
            handoff=handoff_result["handoff"],
            egress=egress,
        )

    @app.post("/api/workspaces/open")
    async def open_workspace(
        payload: WorkspaceOpenRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.option_id)
        return runtime.open_workspace(payload.option_id)

    @app.get("/api/catalog/status")
    async def catalog_status(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.catalog_status()

    @app.post("/api/catalog/rebuild", status_code=202)
    async def rebuild_catalog(
        _: EmptyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        return runtime.start_rebuild()

    @app.get("/api/discovery/limits")
    async def discovery_limits(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.discovery_limits()

    @app.post("/api/discovery/search")
    async def discovery_search(
        payload: DiscoverySearchRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        return await runtime.search_discovery(payload.model_dump(mode="json"))

    @app.post("/api/discovery/select")
    async def discovery_select(
        payload: DiscoverySelectionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        return await runtime.select_discovery(payload.report, payload.result_keys)

    @app.get("/api/discovery/candidates")
    async def discovery_candidates(
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: Annotated[str | None, Query(max_length=128)] = None,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if cursor is not None:
            _reject_path_shaped(cursor)
        return runtime.list_discovery_candidates(page_size=page_size, cursor=cursor)

    @app.get("/api/discovery/candidates/{candidate_id}")
    async def discovery_candidate(
        candidate_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(candidate_id)
        return runtime.show_discovery_candidate(candidate_id)

    @app.post("/api/discovery/candidates/{candidate_id}/resolve")
    async def discovery_resolve(
        candidate_id: str,
        _: EmptyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(candidate_id)
        return await runtime.resolve_discovery_candidate(candidate_id)

    @app.post("/api/discovery/candidates/{candidate_id}/acquire")
    async def discovery_acquire(
        candidate_id: str,
        _: EmptyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(candidate_id)
        return await runtime.acquire_discovery_candidate(candidate_id)

    @app.get("/api/discovery/candidates/{candidate_id}/intake-handoff")
    async def discovery_intake_handoff(
        candidate_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(candidate_id)
        return runtime.inspect_acquired_candidate(candidate_id)

    @app.get("/api/intake/inbox")
    async def scan_intake_inbox(
        max_entries: int = Query(default=20, ge=1),
        min_stable_age_seconds: int = Query(default=5, ge=0, le=86400),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.scan_inbox(
            max_entries=max_entries,
            min_stable_age_seconds=min_stable_age_seconds,
        )

    @app.post("/api/intake/upload", status_code=202)
    async def start_upload(
        request: Request,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        limits = runtime.intake_limits()
        parsed = await parse_multipart_stream(
            request.stream(),
            content_type=request.headers.get("content-type", ""),
            state_root=config.state_root,
            max_pdf_bytes=limits["max_pdf_bytes"],
        )
        try:
            payload = IntakeStartRequest.model_validate(parsed.metadata)
        except ValidationError as error:
            parsed.upload.cleanup()
            raise AppOperationError(
                "RKBAPP-VALIDATION",
                "Upload metadata validation failed",
                status_code=422,
            ) from error
        try:
            return runtime.start_upload(parsed.upload, payload.model_dump())
        except Exception:
            parsed.upload.cleanup()
            raise

    @app.post("/api/intake/inbox/start", status_code=202)
    async def start_inbox(
        payload: InboxStartRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        return runtime.start_inbox(
            payload.candidate_token,
            payload.model_dump(exclude={"candidate_token"}),
        )

    @app.get("/api/intake/jobs")
    async def list_intake_jobs(
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=4096),
        requested_route: str | None = Query(default=None, max_length=64),
        requested_depth: str | None = Query(default=None, max_length=64),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.list_jobs(
            page_size=page_size,
            cursor=cursor,
            requested_route=requested_route,
            requested_depth=requested_depth,
        )

    @app.get("/api/intake/jobs/{job_id}")
    async def show_intake_job(
        job_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(job_id)
        return runtime.show_job(job_id)

    @app.post("/api/intake/jobs/{job_id}/resume", status_code=202)
    async def resume_intake_job(
        job_id: str,
        payload: JobResumeRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        values = payload.model_dump()
        expected = {
            "state_id": values.pop("expected_state_id"),
            "state_digest": values.pop("expected_state_digest"),
        }
        return runtime.resume_job(job_id, expected, values)

    @app.post("/api/intake/jobs/{job_id}/cancel", status_code=202)
    async def cancel_intake_job(
        job_id: str,
        payload: JobCancelRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        return runtime.cancel_job(
            job_id,
            {
                "state_id": payload.expected_state_id,
                "state_digest": payload.expected_state_digest,
            },
        )

    @app.post("/api/intake/jobs/{job_id}/trusted-parse/prepare")
    async def prepare_trusted_parse(
        job_id: str,
        payload: TrustedParsePrepareRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        return await asyncio.to_thread(
            runtime.prepare_trusted_parse,
            browser_session_id=session.session_id,
            job_id=job_id,
            expected_state={
                "state_id": payload.expected_state_id,
                "state_digest": payload.expected_state_digest,
            },
        )

    @app.post("/api/intake/jobs/{job_id}/trusted-parse/approve", status_code=202)
    async def approve_trusted_parse(
        job_id: str,
        payload: TrustedParseApproveRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        return runtime.approve_trusted_parse(
            browser_session_id=session.session_id,
            job_id=job_id,
            lease_token=payload.lease_token,
            aggregate_preview_digest=payload.aggregate_preview_digest,
        )

    @app.get("/api/catalog/items")
    async def list_catalog_items(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
        query: str = Query(default="", max_length=512),
        item_kinds: list[str] = Query(default=[]),
        paper_id: str | None = Query(default=None, max_length=128),
        question_id: str | None = Query(default=None, max_length=128),
        tag_id: str | None = Query(default=None, max_length=128),
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=4096),
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if page_size > config.request_budgets.max_page_size:
            raise AppOperationError(
                "RKBAPP-PAGE-LIMIT",
                "Requested page size exceeds the configured budget",
                status_code=400,
            )
        for value in (*item_kinds, paper_id, question_id, tag_id):
            if value is not None:
                _reject_path_shaped(value)
        return runtime.search_catalog(
            query=query,
            item_kinds=tuple(item_kinds),
            paper_id=paper_id,
            question_id=question_id,
            tag_id=tag_id,
            page_size=page_size,
            cursor=cursor,
        )

    @app.get("/api/catalog/items/{item_id}")
    async def show_catalog_item(
        item_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(item_id)
        return runtime.catalog_detail(item_id)

    @app.get("/api/reading/papers/{paper_id}")
    async def show_reading_paper(
        paper_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(paper_id)
        return runtime.show_reading_paper(paper_id)

    @app.post("/api/reading/compare")
    async def compare_reading_papers(
        payload: ReadingCompareRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        for paper_id in payload.paper_ids:
            _reject_path_shaped(paper_id)
        return runtime.compare_reading_papers(payload.paper_ids)

    @app.get("/api/reading/evidence/{evidence_id}")
    async def trace_reading_evidence(
        evidence_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(evidence_id)
        return runtime.trace_reading_evidence(evidence_id)

    @app.post("/api/reading/evidence/{evidence_id}/source-handle")
    async def issue_evidence_pdf_handle(
        evidence_id: str,
        _: EmptyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(evidence_id)
        return runtime.issue_evidence_pdf_handle(session.session_id, evidence_id)

    @app.get("/api/reading/pdf/{handle_id}")
    async def stream_evidence_pdf(
        handle_id: str,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> StreamingResponse:
        _reject_path_shaped(handle_id)
        opened = runtime.open_evidence_pdf(session.session_id, handle_id)
        try:
            byte_range = resolve_byte_range(range_header, opened.size_bytes)
        except Exception:
            opened.close()
            raise
        return StreamingResponse(
            _stream_opened_pdf(opened, byte_range),
            status_code=byte_range.status_code,
            media_type="application/pdf",
            headers=_pdf_headers(byte_range),
        )

    @app.head("/api/reading/pdf/{handle_id}")
    async def head_evidence_pdf(
        handle_id: str,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        range_header: Annotated[str | None, Header(alias="Range")] = None,
    ) -> Response:
        _reject_path_shaped(handle_id)
        opened = runtime.open_evidence_pdf(session.session_id, handle_id)
        try:
            byte_range = resolve_byte_range(range_header, opened.size_bytes)
        finally:
            opened.close()
        return Response(
            status_code=byte_range.status_code,
            media_type="application/pdf",
            headers=_pdf_headers(byte_range),
        )

    @app.post("/api/reading/pdf/{handle_id}/open")
    async def open_evidence_pdf_externally(
        handle_id: str,
        _: EmptyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(handle_id)
        return runtime.open_evidence_in_external_reader(session.session_id, handle_id)

    @app.get("/api/agent/registry")
    async def show_agent_registry(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.agent_registry()

    @app.get("/api/agent/tasks")
    async def list_agent_tasks(
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=128),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if cursor is not None:
            _reject_path_shaped(cursor)
        return runtime.list_agent_tasks(page_size=page_size, cursor=cursor)

    @app.get("/api/agent/tasks/{task_id}")
    async def show_agent_task(
        task_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(task_id)
        return runtime.show_agent_task(task_id)

    @app.get("/api/organization/directions")
    async def list_directions(
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=128),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if cursor is not None:
            _reject_path_shaped(cursor)
        return runtime.list_directions(page_size=page_size, cursor=cursor)

    @app.get("/api/organization/directions/{target_id}")
    async def show_direction(
        target_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(target_id)
        return runtime.show_direction(target_id)

    @app.get("/api/organization/field-map-entries")
    async def list_field_map_entries(
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=128),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if cursor is not None:
            _reject_path_shaped(cursor)
        return runtime.list_field_map_entries(page_size=page_size, cursor=cursor)

    @app.get("/api/organization/field-map-entries/{target_id}")
    async def show_field_map_entry(
        target_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(target_id)
        return runtime.show_field_map_entry(target_id)

    @app.get("/api/organization/questions")
    async def list_organization_questions(
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=128),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if cursor is not None:
            _reject_path_shaped(cursor)
        return runtime.list_questions(page_size=page_size, cursor=cursor)

    @app.get("/api/organization/questions/{target_id}")
    async def show_organization_question(
        target_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(target_id)
        return runtime.show_question(target_id)

    @app.get("/api/organization/papers/{paper_id}/context")
    async def show_paper_organization_context(
        paper_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(paper_id)
        return runtime.show_paper_organization_context(paper_id)

    @app.get("/api/tags")
    async def list_tags(
        include_archived: bool = Query(default=False),
        page_size: int = Query(default=20, ge=1),
        cursor: str | None = Query(default=None, max_length=128),
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        if cursor is not None:
            _reject_path_shaped(cursor)
        return runtime.list_tags(
            include_archived=include_archived,
            page_size=page_size,
            cursor=cursor,
        )

    @app.get("/api/tags/{tag_id}")
    async def show_tag(
        tag_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(tag_id)
        return runtime.show_tag(tag_id)

    @app.get("/api/tag-targets/{target_kind}/{target_id}")
    async def list_target_tags(
        target_kind: Literal["paper", "direction", "field_map_entry", "question"],
        target_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(target_id)
        return runtime.list_target_tags(target_kind, target_id)

    @app.post("/api/tags/promote")
    async def promote_tag(
        payload: TagPromoteRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump(exclude_none=True)
        for field in ("tag_id", "expected_revision_id"):
            if field in request:
                _reject_path_shaped(request[field])
        return await runtime.promote_tag(request)

    @app.post("/api/tag-assignments")
    async def set_tag_assignment(
        payload: TagAssignmentRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump(exclude_none=True)
        for field in ("tag_id", "target_id", "expected_revision_id"):
            if field in request:
                _reject_path_shaped(request[field])
        return await runtime.set_tag_assignment(request)

    @app.post("/api/organization/proposals")
    async def create_organization_proposal(
        payload: OrganizationProposalCreateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if payload.target_id is not None:
            _reject_path_shaped(payload.target_id)
        for paper_id in payload.paper_ids:
            _reject_path_shaped(paper_id)
        for content_class in payload.approved_content_classes:
            _reject_path_shaped(content_class)
        return await runtime.create_organization_proposal(payload.model_dump())

    @app.post("/api/organization/proposals/{task_id}/approve")
    async def approve_organization_proposal(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.approve_organization_result(task_id, _agent_expected(payload))

    @app.get("/api/research-synthesis/limits")
    async def research_synthesis_limits(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.research_synthesis_limits()

    @app.get("/api/research-synthesis/candidates")
    async def list_research_synthesis_candidates(
        question_id: Annotated[str | None, Query(max_length=128)] = None,
        candidate_type: Annotated[
            Literal["synthesis", "review_angle", "insight", "cross_view"] | None,
            Query(),
        ] = None,
        freshness: Annotated[Literal["current", "stale"] | None, Query()] = None,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(max_length=128)] = None,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        for value in (question_id, cursor):
            if value is not None:
                _reject_path_shaped(value)
        return runtime.list_research_synthesis_candidates(
            question_id=question_id,
            candidate_type=candidate_type,
            freshness=freshness,
            page_size=page_size,
            cursor=cursor,
        )

    @app.get("/api/research-synthesis/candidates/{candidate_id}")
    async def show_research_synthesis_candidate(
        candidate_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(candidate_id)
        return runtime.show_research_synthesis_candidate(candidate_id)

    @app.get("/api/research-synthesis/questions/{question_id}/context")
    async def show_research_synthesis_question_context(
        question_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(question_id)
        return runtime.show_research_synthesis_question_context(question_id)

    @app.post("/api/research-synthesis/proposals")
    async def create_research_synthesis_proposal(
        payload: ResearchSynthesisProposalCreateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump()
        for value in (request["question_id"], request["target_candidate_id"]):
            if value is not None:
                _reject_path_shaped(value)
        for content_class in request["approved_content_classes"]:
            _reject_path_shaped(content_class)
        return await runtime.create_research_synthesis_proposal(request)

    @app.post("/api/research-synthesis/proposals/{task_id}/approve")
    async def approve_research_synthesis_proposal(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.approve_research_synthesis_result(task_id, _agent_expected(payload))

    @app.get("/api/exchange/capabilities")
    async def exchange_capabilities(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.exchange_capabilities()

    @app.post("/api/exchange/export/preview")
    async def preview_exchange_export(
        payload: ExchangeExportPreviewRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        if payload.selector_id is not None:
            _reject_path_shaped(payload.selector_id)
        return runtime.preview_exchange_export(
            browser_session_id=session.session_id,
            request=payload.model_dump(),
        )

    @app.post("/api/exchange/export/build")
    async def build_exchange_export(
        payload: ExchangeTokenRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.preview_token)
        return await runtime.build_exchange_export(
            browser_session_id=session.session_id,
            preview_token=payload.preview_token,
        )

    @app.get("/api/exchange/export/download/{download_token}")
    async def download_exchange_export(
        download_token: str,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
    ) -> StreamingResponse:
        _reject_path_shaped(download_token)
        lease = runtime.consume_exchange_download(
            browser_session_id=session.session_id,
            download_token=download_token,
        )
        return StreamingResponse(
            _stream_exchange_archive(lease.artifact),
            media_type=EXCHANGE_ARCHIVE_MEDIA_TYPE,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{lease.filename}"',
                "Content-Length": str(lease.artifact.size_bytes),
            },
        )

    @app.post(EXCHANGE_UPLOAD_PATH)
    async def upload_exchange_import(
        request: Request,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        try:
            declared_length = int(request.headers["content-length"])
        except (KeyError, ValueError) as error:
            raise AppOperationError(
                "RKBAPP-CONTENT-LENGTH",
                "Exchange upload requires a valid Content-Length",
                status_code=411,
            ) from error
        max_archive_bytes = runtime.exchange_capabilities()["safe_reader_profile"]["max_archive_bytes"]
        upload = await stream_exchange_upload(
            request.stream(),
            state_root=config.state_root,
            max_archive_bytes=max_archive_bytes,
            declared_length=declared_length,
        )
        try:
            return runtime.register_exchange_upload(
                browser_session_id=session.session_id,
                upload=upload,
            )
        except Exception:
            upload.cleanup()
            raise

    @app.post("/api/exchange/import/preview")
    async def preview_exchange_import(
        payload: ExchangeUploadTokenRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.upload_token)
        return await runtime.preview_exchange_import(
            browser_session_id=session.session_id,
            upload_token=payload.upload_token,
        )

    @app.post("/api/exchange/import/apply")
    async def apply_exchange_import(
        payload: ExchangeTokenRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.preview_token)
        return await runtime.apply_exchange_import(
            browser_session_id=session.session_id,
            preview_token=payload.preview_token,
        )

    @app.get("/api/exchange/imports")
    async def list_exchange_imports(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.list_exchange_imports()

    @app.get("/api/exchange/imports/{import_id}")
    async def show_exchange_import(
        import_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(import_id)
        return runtime.show_exchange_import(import_id)

    @app.get("/api/obsidian/status")
    async def obsidian_status(
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.obsidian_status(page_size=page_size, cursor=cursor)

    @app.get("/api/obsidian/targets")
    async def obsidian_targets(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.obsidian_targets()

    @app.post("/api/obsidian/render/preview")
    async def preview_obsidian_render(
        payload: ObsidianRenderPreviewRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        return runtime.preview_obsidian_render(
            browser_session_id=session.session_id,
            optional_tables=payload.optional_tables,
        )

    @app.post("/api/obsidian/render/apply")
    async def apply_obsidian_render(
        payload: ObsidianRenderApplyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        return await runtime.apply_obsidian_render(
            browser_session_id=session.session_id,
            preview_token=payload.preview_token,
            optional_tables=payload.optional_tables,
            continuation=payload.continuation,
        )

    @app.post("/api/obsidian/sync/preview")
    async def preview_obsidian_sync(
        payload: ObsidianSyncPreviewRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.target_id)
        return runtime.preview_obsidian_sync(
            browser_session_id=session.session_id,
            target_id=payload.target_id,
        )

    @app.post("/api/obsidian/sync/apply")
    async def apply_obsidian_sync(
        payload: ObsidianSyncApplyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(payload.target_id)
        return await runtime.apply_obsidian_sync(
            browser_session_id=session.session_id,
            target_id=payload.target_id,
            preview_token=payload.preview_token,
            continuation=payload.continuation,
        )

    @app.get("/api/screening/limits")
    async def screening_limits(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.screening_limits()

    @app.get("/api/screening/criteria")
    async def list_screening_criteria(
        question_id: Annotated[str | None, Query(max_length=128)] = None,
        include_archived: bool = False,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(max_length=128)] = None,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        for value in (question_id, cursor):
            if value is not None:
                _reject_path_shaped(value)
        return runtime.list_screening_criteria(
            question_id=question_id,
            include_archived=include_archived,
            page_size=page_size,
            cursor=cursor,
        )

    @app.get("/api/screening/criteria/{criteria_id}")
    async def show_screening_criteria(
        criteria_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(criteria_id)
        return runtime.show_screening_criteria(criteria_id)

    @app.post("/api/screening/criteria")
    async def promote_screening_criteria(
        payload: ScreeningCriteriaPromoteRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump(exclude_none=True)
        for field in ("criteria_id", "question_id", "expected_revision_id"):
            if field in request:
                _reject_path_shaped(request[field])
        for item in (*request["inclusion_criteria"], *request["exclusion_criteria"]):
            if "criterion_id" in item:
                _reject_path_shaped(item["criterion_id"])
        return await runtime.promote_screening_criteria(request)

    @app.get("/api/screening/decisions")
    async def list_screening_decisions(
        question_id: Annotated[str | None, Query(max_length=128)] = None,
        paper_id: Annotated[str | None, Query(max_length=128)] = None,
        outcome: Annotated[Literal["included", "excluded"] | None, Query()] = None,
        freshness: Annotated[str | None, Query(max_length=64)] = None,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(max_length=128)] = None,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        for value in (question_id, paper_id, freshness, cursor):
            if value is not None:
                _reject_path_shaped(value)
        return runtime.list_screening_decisions(
            question_id=question_id,
            paper_id=paper_id,
            outcome=outcome,
            freshness=freshness,
            page_size=page_size,
            cursor=cursor,
        )

    @app.get("/api/screening/decisions/{decision_id}")
    async def show_screening_decision(
        decision_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(decision_id)
        return runtime.show_screening_decision(decision_id)

    @app.post("/api/screening/decisions")
    async def promote_screening_decision(
        payload: ScreeningDecisionPromoteRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump(exclude_none=True)
        for field in ("decision_id", "question_id", "paper_id", "criteria_revision_id", "expected_revision_id"):
            if field in request:
                _reject_path_shaped(request[field])
        for item in request["criterion_dispositions"]:
            _reject_path_shaped(item["criterion_id"])
        return await runtime.promote_screening_decision(request)

    @app.post("/api/screening/proposals/criteria")
    async def create_screening_criteria_proposal(
        payload: ScreeningCriteriaProposalCreateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump()
        for field in ("question_id", "criteria_id"):
            if request[field] is not None:
                _reject_path_shaped(request[field])
        for content_class in request["approved_content_classes"]:
            _reject_path_shaped(content_class)
        return await runtime.create_screening_criteria_proposal(request)

    @app.post("/api/screening/proposals/decisions")
    async def create_screening_decision_proposal(
        payload: ScreeningDecisionProposalCreateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        request = payload.model_dump()
        for field in ("question_id", "paper_id"):
            _reject_path_shaped(request[field])
        for content_class in request["approved_content_classes"]:
            _reject_path_shaped(content_class)
        return await runtime.create_screening_decision_proposal(request)

    @app.post("/api/screening/proposals/{task_id}/approve")
    async def approve_screening_proposal(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.approve_screening_result(task_id, _agent_expected(payload))

    @app.post("/api/knowledge-queries")
    async def create_knowledge_query(
        payload: KnowledgeQueryCreateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        for paper_id in payload.paper_ids:
            _reject_path_shaped(paper_id)
        for content_class in payload.approved_content_classes:
            _reject_path_shaped(content_class)
        return await runtime.create_knowledge_query(payload.model_dump())

    @app.post("/api/knowledge-queries/{task_id}/accept-report")
    async def accept_knowledge_query_report(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.accept_knowledge_query_report(
            task_id,
            _agent_expected(payload),
        )

    @app.post("/api/intake/jobs/{job_id}/agent-tasks")
    async def create_agent_task(
        job_id: str,
        payload: AgentTaskCreateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        _reject_path_shaped(payload.paper_id)
        for content_class in payload.approved_content_classes:
            _reject_path_shaped(content_class)
        return await runtime.create_agent_task(job_id, payload.model_dump())

    @app.post("/api/agent/tasks/{task_id}/inspect-handoff")
    async def inspect_agent_handoff(
        task_id: str,
        payload: AgentHandoffRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return runtime.inspect_agent_handoff(
            task_id,
            _agent_expected(payload),
            payload.executor_id,
        )

    @app.post("/api/agent/tasks/{task_id}/handoff")
    async def prepare_agent_handoff(
        task_id: str,
        payload: AgentHandoffRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.prepare_agent_handoff(
            task_id,
            _agent_expected(payload),
            payload.executor_id,
        )

    @app.post("/api/agent/tasks/{task_id}/submit")
    async def submit_agent_result(
        task_id: str,
        payload: AgentSubmitRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.submit_agent_result(
            task_id,
            _agent_expected(payload),
            payload.result,
        )

    @app.get("/api/agent/tasks/{task_id}/preview")
    async def preview_agent_result(
        task_id: str,
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        _reject_path_shaped(task_id)
        return runtime.preview_agent_result(task_id)

    @app.post("/api/agent/tasks/{task_id}/request-revision")
    async def request_agent_revision(
        task_id: str,
        payload: AgentRevisionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.request_agent_revision(
            task_id,
            _agent_expected(payload),
            payload.feedback,
        )

    @app.post("/api/agent/tasks/{task_id}/refresh")
    async def refresh_agent_task(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.refresh_agent_task(task_id, _agent_expected(payload))

    @app.get("/api/agent/tasks/{task_id}/source-adequacy-resolution")
    async def source_adequacy_resolution_context(
        task_id: str,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
    ) -> dict[str, Any]:
        _reject_path_shaped(task_id)
        return runtime.source_adequacy_resolution_context(task_id)

    @app.post("/api/agent/tasks/{task_id}/source-adequacy-resolution/open")
    async def open_source_adequacy_resolution(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.open_source_adequacy_review(
            session.session_id,
            task_id,
            _agent_expected(payload),
        )

    @app.post("/api/agent/tasks/{task_id}/source-adequacy-resolution/decide")
    async def decide_source_adequacy_resolution(
        task_id: str,
        payload: SourceAdequacyResolutionDecisionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.decide_source_adequacy_resolution(
            session.session_id,
            task_id,
            _agent_expected(payload),
            payload.action,
            payload.confirmation_id,
        )

    @app.get("/api/intake/jobs/{job_id}/source-adequacy-resolution")
    async def intake_source_adequacy_resolution_context(
        job_id: str,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
    ) -> dict[str, Any]:
        _reject_path_shaped(job_id)
        return runtime.intake_source_adequacy_resolution_context(job_id)

    @app.post("/api/intake/jobs/{job_id}/source-adequacy-resolution/open")
    async def open_intake_source_adequacy_resolution(
        job_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        return await runtime.open_intake_source_adequacy_review(
            session.session_id,
            job_id,
            _agent_expected(payload),
        )

    @app.post("/api/intake/jobs/{job_id}/source-adequacy-resolution/decide")
    async def decide_intake_source_adequacy_resolution(
        job_id: str,
        payload: SourceAdequacyResolutionDecisionRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(job_id)
        return await runtime.decide_intake_source_adequacy_resolution(
            session.session_id,
            job_id,
            _agent_expected(payload),
            payload.action,
            payload.confirmation_id,
        )

    @app.post("/api/agent/tasks/{task_id}/reject")
    async def reject_agent_result(
        task_id: str,
        payload: AgentRejectRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.reject_agent_result(
            task_id,
            _agent_expected(payload),
            payload.reason_code,
        )

    @app.post("/api/agent/tasks/{task_id}/approve")
    async def approve_agent_result(
        task_id: str,
        payload: ExpectedAgentStateRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, Any]:
        sessions.require_csrf(session, csrf)
        _reject_path_shaped(task_id)
        return await runtime.approve_agent_result(task_id, _agent_expected(payload))

    @app.get("/api/capabilities")
    async def show_capabilities(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.capabilities()

    @app.get("/api/health")
    async def show_health(
        session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> dict[str, Any]:
        sessions.require(session_id)
        return runtime.health()

    @app.post("/api/shutdown")
    async def shutdown(
        _: EmptyRequest,
        session: BrowserSession = Depends(_SessionDependency(sessions)),
        csrf: Annotated[str | None, Header(alias=CSRF_HEADER)] = None,
    ) -> dict[str, str]:
        sessions.require_csrf(session, csrf)
        result = runtime.request_shutdown()
        sessions.clear()
        return result

    assets = Path(config.frontend_root) / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(Path(config.frontend_root) / "index.html")

    @app.get("/favicon.ico", status_code=204)
    async def favicon() -> Response:
        return Response(status_code=204)

    return app


def _stream_opened_pdf(opened: Any, byte_range: ResolvedByteRange):
    try:
        opened.stream.seek(byte_range.start)
        remaining = byte_range.length
        while remaining:
            chunk = opened.stream.read(min(PDF_STREAM_CHUNK_BYTES, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        opened.close()


async def _stream_exchange_archive(artifact: ManagedExchangeFile):
    try:
        with artifact.open() as handle:
            while block := await asyncio.to_thread(handle.read, 1024 * 1024):
                yield block
    finally:
        artifact.cleanup()


def _pdf_headers(byte_range: ResolvedByteRange) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Disposition": 'inline; filename="evidence.pdf"',
        "Content-Length": str(byte_range.length),
        "Cross-Origin-Resource-Policy": "same-origin",
    }
    if byte_range.content_range is not None:
        headers["Content-Range"] = byte_range.content_range
    return headers


class _SessionDependency:
    def __init__(self, sessions: SessionManager):
        self.sessions = sessions

    def __call__(self, request: Request) -> BrowserSession:
        return self.sessions.require(request.cookies.get(SESSION_COOKIE))


def _reject_path_shaped(value: str) -> None:
    if PATH_SHAPED_ID.search(value):
        raise AppOperationError("RKBAPP-ID", "Browser identifiers must not contain path syntax", status_code=400)


def _agent_expected(payload: ExpectedAgentStateRequest) -> dict[str, str]:
    return {
        "state_id": payload.expected_state_id,
        "state_digest": payload.expected_state_digest,
    }


def _apply_security_headers(response: Response) -> None:
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {
        "status": "error",
        "diagnostic": {"code": code, "message": message},
    }
    if details:
        content["diagnostic"]["details"] = details
    return JSONResponse(status_code=status_code, content=content)


__all__ = ["create_app"]
