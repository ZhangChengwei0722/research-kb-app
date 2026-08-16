from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Sequence

from research_kb.services import WorkspaceAdoptionApplicationService
from research_kb.services.workspace_adoption import WorkspaceAdoptionInspection
from research_kb.services.workspace_materialization import WorkspaceMaterializationApplicationService
from research_kb.workspace_materialization import (
    ExternalSourceRoot,
    ROOT_SECURITY_POLICY,
    WorkspaceMaterializationProposal,
    WorkspaceMaterializationReceipt,
    WorkspaceMaterializationRequest,
)

from research_kb_app.config import AppConfig, ObsidianTarget, RequestBudgets, WorkspaceOption
from research_kb_app.egress import EgressPolicyService
from research_kb_app.errors import AppOperationError
from research_kb_app.folder_helper import FolderHelperService
from research_kb_app.product_profile import ManagedProductProfileStore, ProfileCommit
from research_kb_app.selection_leases import SelectionLeaseRegistry
from research_kb_app.setup_recovery import SetupRecoveryStore
from research_kb_app.windows_security import WindowsNamedMutexFactory, WindowsRootSecurityService


SETUP_INTERFACE = "research-kb-app-setup@1.0"
SETUP_PROPOSAL_SECONDS = 900
DEFAULT_REQUEST_BUDGETS = RequestBudgets(1024 * 1024, 16 * 1024, 100, 120.0)
_WORKSPACE_OPTION_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class SetupProposalLease:
    token: str
    browser_session_id: str
    profile_id: str
    proposal: WorkspaceMaterializationProposal
    selection_ids: tuple[str, ...]
    expected_profile_digest: str | None
    expires_at: float
    state: str = "prepared"
    result: dict[str, Any] | None = None


class SetupRuntime:
    def __init__(
        self,
        *,
        profile_store: ManagedProductProfileStore,
        frontend_root: Path,
        root_security: WindowsRootSecurityService | None = None,
        folder_helper: FolderHelperService | None = None,
        selections: SelectionLeaseRegistry | None = None,
        materializer: WorkspaceMaterializationApplicationService | None = None,
        adoption_inspector: WorkspaceAdoptionApplicationService | None = None,
        writer_mutex: WindowsNamedMutexFactory | None = None,
        recovery_store: SetupRecoveryStore | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        on_profile_committed: Callable[[AppConfig], None] | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.profile_id = profile_store.profile_id
        self.frontend_root = Path(frontend_root).resolve(strict=True)
        self.root_security = root_security or WindowsRootSecurityService()
        self.folder_helper = folder_helper or FolderHelperService()
        self.selections = selections or SelectionLeaseRegistry(monotonic_clock=monotonic_clock)
        self.materializer = materializer or WorkspaceMaterializationApplicationService()
        self.adoption_inspector = adoption_inspector or WorkspaceAdoptionApplicationService()
        self.writer_mutex = writer_mutex or WindowsNamedMutexFactory()
        self.recovery_store = recovery_store or SetupRecoveryStore(profile_store.runtime_root / "setup-recovery")
        self._clock = monotonic_clock
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._on_profile_committed = on_profile_committed
        self._lock = threading.RLock()
        self._proposals: dict[str, SetupProposalLease] = {}
        self._adoptions: dict[str, dict[str, Any]] = {}

    def set_profile_committed_callback(self, callback: Callable[[AppConfig], None]) -> None:
        self._on_profile_committed = callback

    def status(self) -> dict[str, Any]:
        recovery = self.profile_store.inspect_recovery()
        setup_recovery = self._setup_recovery_items()
        if setup_recovery:
            mode = "recovery"
        elif recovery.state == "current":
            mode = "ready"
        elif recovery.state == "current_missing" and not recovery.recoverable_revision_ids:
            mode = "first_run"
        else:
            mode = "recovery"
        return {
            "status": "success",
            "interface_version": SETUP_INTERFACE,
            "mode": mode,
            "profile_id": self.profile_id,
            "current_revision_id": recovery.current_revision_id,
            "recovery_available": bool(recovery.recoverable_revision_ids or setup_recovery),
        }

    def select_folder(
        self,
        *,
        browser_session_id: str,
        purpose: str,
        allow_new_child: bool,
        initial_location_id: str | None,
    ) -> dict[str, Any]:
        helper = self.folder_helper.select(
            purpose=purpose,
            allow_existing=True,
            allow_new_child=allow_new_child,
            initial_location_id=initial_location_id,
        )
        if helper.status == "cancelled" or helper.path is None:
            return {"status": "cancelled", "interface_version": SETUP_INTERFACE}
        attestation = self.root_security.inspect(helper.path)
        capabilities = _attestation_capabilities(attestation)
        if purpose == "source_root":
            accepted = bool(capabilities["local"] and capabilities["reparse_free"])
        else:
            accepted = bool(capabilities["accepted"])
        if not accepted:
            raise AppOperationError("RKBAPP-SETUP-ROOT", "Selected folder does not satisfy this setup purpose")
        lease = self.selections.issue(
            browser_session_id=browser_session_id,
            profile_id=self.profile_id,
            purpose=purpose,
            path=helper.path,
            display_label=helper.path.name or helper.path.drive,
            capability_facts=capabilities,
            security_basis_digest=_attestation_digest(attestation),
        )
        return {"status": "success", "interface_version": SETUP_INTERFACE, "selection": lease.public(now=self._clock())}

    def prepare_workspace(
        self,
        *,
        browser_session_id: str,
        workspace_parent_lease_id: str,
        source_roots: Sequence[tuple[str, str]],
        local_inbox_lease_id: str,
        workspace_name: str,
        workspace_label: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not source_roots or len(source_roots) > 32:
            raise AppOperationError("RKBAPP-SETUP-SOURCES", "Setup source roots are missing or exceed the limit", status_code=400)
        claims = []
        try:
            parent = self.selections.claim(
                workspace_parent_lease_id,
                browser_session_id=browser_session_id,
                profile_id=self.profile_id,
                purpose="workspace_parent",
            )
            claims.append(parent)
            external: list[ExternalSourceRoot] = []
            for root_id, lease_id in source_roots:
                lease = self.selections.claim(
                    lease_id,
                    browser_session_id=browser_session_id,
                    profile_id=self.profile_id,
                    purpose="source_root",
                )
                claims.append(lease)
                external.append(ExternalSourceRoot(root_id, lease.path))
            inbox = self.selections.claim(
                local_inbox_lease_id,
                browser_session_id=browser_session_id,
                profile_id=self.profile_id,
                purpose="local_inbox",
            )
            claims.append(inbox)
            request = WorkspaceMaterializationRequest(
                workspace_parent=parent.path,
                workspace_name=workspace_name,
                workspace_label=workspace_label,
                source_roots=tuple(external),
                local_inbox=inbox.path,
                idempotency_key=idempotency_key,
                expires_at=self._server_proposal_expiry(),
            )
            proposal = self.materializer.prepare(request, self.root_security)
        finally:
            for claim in claims:
                self.selections.finish(claim.lease_id, succeeded=False)
        current = self.profile_store.load_current(missing_ok=True)
        token = f"setup_{secrets.token_hex(24)}"
        lease = SetupProposalLease(
            token=token,
            browser_session_id=browser_session_id,
            profile_id=self.profile_id,
            proposal=proposal,
            selection_ids=tuple(claim.lease_id for claim in claims),
            expected_profile_digest=None if current is None else current["record_digest"],
            expires_at=self._clock() + SETUP_PROPOSAL_SECONDS,
        )
        self.recovery_store.append(
            proposal,
            state="prepared",
            expected_profile_digest=lease.expected_profile_digest,
        )
        with self._lock:
            self._proposals[token] = lease
        return {
            "status": "success",
            "interface_version": SETUP_INTERFACE,
            "proposal_token": token,
            "preview": dict(proposal.preview),
            "preview_digest": proposal.preview_digest,
        }

    def commit_workspace(
        self,
        *,
        browser_session_id: str,
        proposal_token: str,
        preview_digest: str,
    ) -> dict[str, Any]:
        with self._lock:
            lease = self._require_proposal(proposal_token, browser_session_id)
            if lease.state == "completed" and lease.result is not None:
                return dict(lease.result)
            self._proposals[proposal_token] = replace(lease, state="committing")
        claims = []
        purposes = ["workspace_parent", *(["source_root"] * len(lease.proposal.request.source_roots)), "local_inbox"]
        try:
            for selection_id, purpose in zip(lease.selection_ids, purposes, strict=True):
                claims.append(
                    self.selections.claim(
                        selection_id,
                        browser_session_id=browser_session_id,
                        profile_id=self.profile_id,
                        purpose=purpose,
                    )
                )
            self.recovery_store.append(
                lease.proposal,
                state="commit_started",
                expected_profile_digest=lease.expected_profile_digest,
            )
            receipt = self.materializer.commit(
                lease.proposal,
                preview_digest=preview_digest,
                actor="user",
                root_security_controller=self.root_security,
                writer_mutex=self.writer_mutex,
            )
            self.recovery_store.append(
                lease.proposal,
                state="core_committed",
                expected_profile_digest=lease.expected_profile_digest,
                core_receipt=receipt,
            )
            profile = self._commit_workspace_reference(lease, receipt.workspace_id)
            self.recovery_store.append(
                lease.proposal,
                state="profile_committed",
                expected_profile_digest=lease.expected_profile_digest,
                core_receipt=receipt,
            )
            result = {
                "status": "success",
                "interface_version": SETUP_INTERFACE,
                "result": receipt.result,
                "workspace_id": receipt.workspace_id,
                "profile_revision_id": profile.revision_id,
                "restart_required": self._apply_current_profile(),
            }
            for claim in claims:
                self.selections.finish(claim.lease_id, succeeded=True)
            with self._lock:
                self._proposals[proposal_token] = replace(lease, state="completed", result=result)
            return result
        except Exception:
            for claim in claims:
                try:
                    self.selections.finish(claim.lease_id, succeeded=False)
                except AppOperationError:
                    pass
            with self._lock:
                self._proposals[proposal_token] = replace(lease, state="prepared")
            raise

    def preview_adoption(self, *, browser_session_id: str, selection_lease_id: str) -> dict[str, Any]:
        selection = self.selections.claim(
            selection_lease_id,
            browser_session_id=browser_session_id,
            profile_id=self.profile_id,
            purpose="existing_workspace_config",
        )
        try:
            config_path = selection.path / "workspace.yaml"
            inspection = self.adoption_inspector.inspect(config_path)
            self._validate_adoption_inspection(inspection)
            root_security_basis_digest = self._adoption_root_security_digest(inspection)
            current = self.profile_store.load_current(missing_ok=True)
            expected_profile_digest = None if current is None else current["record_digest"]
            digest = _digest(
                {
                    "selection_id": selection_lease_id,
                    "workspace_id": inspection.descriptor["workspace"]["workspace_id"],
                    "adoption_basis_digest": inspection.basis_digest,
                    "expected_profile_digest": expected_profile_digest,
                }
            )
            token = f"adoption_{secrets.token_hex(24)}"
            proposal = {
                "token": token,
                "browser_session_id": browser_session_id,
                "selection_id": selection_lease_id,
                "config_path": config_path,
                "workspace_id": inspection.descriptor["workspace"]["workspace_id"],
                "adoption_basis_digest": inspection.basis_digest,
                "root_security_basis_digest": root_security_basis_digest,
                "expected_profile_digest": expected_profile_digest,
                "preview_digest": digest,
                "expires_at": self._clock() + SETUP_PROPOSAL_SECONDS,
                "state": "prepared",
                "result": None,
            }
            with self._lock:
                self._adoptions[token] = proposal
            return {
                "status": "success",
                "interface_version": SETUP_INTERFACE,
                "adoption_token": token,
                "preview": {**inspection.descriptor, "action": "add_profile_reference_only"},
                "preview_digest": digest,
            }
        finally:
            self.selections.finish(selection_lease_id, succeeded=False)

    def commit_adoption(
        self,
        *,
        browser_session_id: str,
        adoption_token: str,
        preview_digest: str,
        label: str,
    ) -> dict[str, Any]:
        with self._lock:
            proposal = self._adoptions.get(adoption_token)
        if proposal is None or proposal.get("browser_session_id") != browser_session_id:
            raise AppOperationError("RKBAPP-ADOPT-STALE", "Existing workspace adoption preview is stale")
        if proposal["expires_at"] <= self._clock() or proposal["preview_digest"] != preview_digest:
            raise AppOperationError("RKBAPP-ADOPT-STALE", "Existing workspace adoption preview changed or expired")
        if proposal.get("state") == "completed" and isinstance(proposal.get("result"), dict):
            return dict(proposal["result"])
        current = self.profile_store.load_current(missing_ok=True)
        actual_profile_digest = None if current is None else current["record_digest"]
        if actual_profile_digest != proposal["expected_profile_digest"]:
            raise AppOperationError("RKBAPP-ADOPT-STALE", "Managed profile changed after workspace adoption preview")
        selection = self.selections.claim(
            proposal["selection_id"],
            browser_session_id=browser_session_id,
            profile_id=self.profile_id,
            purpose="existing_workspace_config",
        )
        try:
            self._require_selection_security_current(selection)
            if selection.path / "workspace.yaml" != proposal["config_path"]:
                raise AppOperationError("RKBAPP-ADOPT-STALE", "Existing workspace selection changed")
            inspection = self.adoption_inspector.inspect(proposal["config_path"])
            self._validate_adoption_inspection(inspection)
            if inspection.basis_digest != proposal["adoption_basis_digest"]:
                raise AppOperationError("RKBAPP-ADOPT-STALE", "Existing workspace changed after adoption preview")
            if self._adoption_root_security_digest(inspection) != proposal["root_security_basis_digest"]:
                raise AppOperationError("RKBAPP-ADOPT-STALE", "Existing workspace security changed after adoption preview")
            options = _workspace_options(current)
            option = WorkspaceOption(_workspace_option_id(proposal["workspace_id"]), label, proposal["config_path"])
            options = [item for item in options if item.option_id != option.option_id] + [option]
            obsidian_targets, request_budgets = _profile_settings(current)
            commit = self.profile_store.commit(
                workspaces=options,
                obsidian_targets=obsidian_targets,
                request_budgets=request_budgets,
                expected_current_digest=actual_profile_digest,
            )
            try:
                final_inspection = self.adoption_inspector.inspect(proposal["config_path"])
                self._validate_adoption_inspection(final_inspection)
                final_current = (
                    final_inspection.basis_digest == proposal["adoption_basis_digest"]
                    and self._adoption_root_security_digest(final_inspection)
                    == proposal["root_security_basis_digest"]
                )
            except Exception as error:
                self._rollback_failed_adoption_commit(commit, current)
                raise AppOperationError(
                    "RKBAPP-ADOPT-STALE",
                    "Existing workspace changed at the profile commit boundary",
                ) from error
            if not final_current:
                self._rollback_failed_adoption_commit(commit, current)
                raise AppOperationError(
                    "RKBAPP-ADOPT-STALE",
                    "Existing workspace changed at the profile commit boundary",
                )
            self.selections.finish(selection.lease_id, succeeded=True)
            result = {
                "status": "success",
                "interface_version": SETUP_INTERFACE,
                "workspace_id": proposal["workspace_id"],
                "profile_revision_id": commit.revision_id,
                "restart_required": self._apply_current_profile(),
            }
            with self._lock:
                proposal["state"] = "completed"
                proposal["result"] = dict(result)
            return result
        except Exception:
            self.selections.finish(selection.lease_id, succeeded=False)
            raise

    def recovery(self) -> dict[str, Any]:
        recovery = self.profile_store.inspect_recovery()
        return {
            "status": "success",
            "interface_version": SETUP_INTERFACE,
            "profile_state": recovery.state,
            "current_revision_id": recovery.current_revision_id,
            "recoverable_revision_ids": list(recovery.recoverable_revision_ids),
            "workspace_setup_operations": self._setup_recovery_items(),
        }

    def export_agent_task_package(
        self,
        *,
        browser_session_id: str,
        selection_lease_id: str,
        handoff: dict[str, Any],
        egress: EgressPolicyService,
    ) -> dict[str, Any]:
        selection = self.selections.claim(
            selection_lease_id,
            browser_session_id=browser_session_id,
            profile_id=self.profile_id,
            purpose="task_package_destination",
        )
        try:
            self._require_selection_security_current(selection)
        except Exception:
            self.selections.finish(selection.lease_id, succeeded=False, invalidate=True)
            raise
        try:
            result = egress.export_task_package(selection.path, handoff)
            self.selections.finish(selection.lease_id, succeeded=True)
            return result
        except Exception:
            self.selections.finish(selection.lease_id, succeeded=False)
            raise

    def recover_profile(self, revision_id: str) -> dict[str, Any]:
        commit = self.profile_store.select_recovery_revision(revision_id)
        return {
            "status": "success",
            "interface_version": SETUP_INTERFACE,
            "profile_revision_id": commit.revision_id,
            "restart_required": self._apply_current_profile(),
        }

    def recover_workspace(self, operation_id: str, action: str) -> dict[str, Any]:
        entry = self.recovery_store.latest(operation_id)
        assert entry is not None
        proposal = entry.proposal
        recovery = self.materializer.inspect_recovery(
            proposal.request.workspace_parent,
            proposal.operation_id,
            self.root_security,
        )
        if action == "restart_workspace_setup" and recovery.state == "absent":
            self.recovery_store.append(
                proposal,
                state="recovery_discarded",
                expected_profile_digest=entry.expected_profile_digest,
            )
            return {
                "status": "success",
                "interface_version": SETUP_INTERFACE,
                "operation_id": operation_id,
                "result": "restart_required",
                "restart_required": True,
            }
        if action == "discard_workspace_staging":
            core_result = self.materializer.recover(
                proposal,
                action="discard_unchanged_owned_staging",
                actor="user",
                root_security_controller=self.root_security,
                writer_mutex=self.writer_mutex,
            )
            self.recovery_store.append(
                proposal,
                state="recovery_discarded",
                expected_profile_digest=entry.expected_profile_digest,
            )
            return {
                "status": "success",
                "interface_version": SETUP_INTERFACE,
                "operation_id": operation_id,
                "result": core_result.state,
                "restart_required": True,
            }
        if action != "resume_workspace_setup":
            raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery action is invalid", status_code=400)
        resumable = {
            "no_change",
            "resume_matching_published_generation",
            "complete_matching_receipt_journal",
        }
        core_action = next((item for item in recovery.actions if item in resumable), None)
        if core_action is None:
            raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup is not resumable in its current state")
        core_result = self.materializer.recover(
            proposal,
            action=core_action,
            actor="user",
            root_security_controller=self.root_security,
            writer_mutex=self.writer_mutex,
        )
        if not isinstance(core_result, WorkspaceMaterializationReceipt):
            raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery did not produce a receipt")
        self.recovery_store.append(
            proposal,
            state="core_committed",
            expected_profile_digest=entry.expected_profile_digest,
            core_receipt=core_result,
        )
        lease = SetupProposalLease(
            token="recovery",
            browser_session_id="recovery",
            profile_id=self.profile_id,
            proposal=proposal,
            selection_ids=(),
            expected_profile_digest=entry.expected_profile_digest,
            expires_at=0,
        )
        profile = self._commit_workspace_reference(lease, core_result.workspace_id)
        self.recovery_store.append(
            proposal,
            state="profile_committed",
            expected_profile_digest=entry.expected_profile_digest,
            core_receipt=core_result,
        )
        return {
            "status": "success",
            "interface_version": SETUP_INTERFACE,
            "operation_id": operation_id,
            "result": core_result.result,
            "profile_revision_id": profile.revision_id,
            "restart_required": self._apply_current_profile(),
        }

    def clear(self) -> None:
        self.selections.clear()
        self.folder_helper.close()
        with self._lock:
            self._proposals.clear()
            self._adoptions.clear()

    def _apply_current_profile(self) -> bool:
        if self._on_profile_committed is None:
            return True
        try:
            self._on_profile_committed(self.profile_store.to_app_config(frontend_root=self.frontend_root))
        except Exception:
            return True
        return False

    def _require_proposal(self, token: str, browser_session_id: str) -> SetupProposalLease:
        lease = self._proposals.get(token)
        valid = (
            isinstance(lease, SetupProposalLease)
            and lease.browser_session_id == browser_session_id
            and lease.profile_id == self.profile_id
            and lease.expires_at > self._clock()
            and lease.state in {"prepared", "completed"}
        )
        if not valid:
            raise AppOperationError("RKBAPP-SETUP-PROPOSAL", "Workspace setup proposal is stale or mismatched")
        return lease

    def _commit_workspace_reference(self, lease: SetupProposalLease, workspace_id: str) -> ProfileCommit:
        current = self.profile_store.load_current(missing_ok=True)
        actual_current = None if current is None else current["record_digest"]
        expected_config = (lease.proposal.target / "workspace.yaml").resolve(strict=True)
        option_id = _workspace_option_id(workspace_id)
        if current is not None:
            matching = [item for item in _workspace_options(current) if item.option_id == option_id]
            if matching:
                if len(matching) != 1 or matching[0].config_path.resolve(strict=True) != expected_config:
                    raise AppOperationError("RKBAPP-PROFILE-STALE", "Managed profile workspace identity conflicts")
                return ProfileCommit(
                    self.profile_id,
                    current["revision_id"],
                    current["revision"],
                    current["record_digest"],
                    "no_change",
                )
        if actual_current != lease.expected_profile_digest:
            raise AppOperationError("RKBAPP-PROFILE-STALE", "Managed profile changed after workspace preview")
        options = _workspace_options(current)
        option = WorkspaceOption(option_id, lease.proposal.request.workspace_label, expected_config)
        options = [item for item in options if item.option_id != option_id] + [option]
        obsidian_targets, request_budgets = _profile_settings(current)
        return self.profile_store.commit(
            workspaces=options,
            obsidian_targets=obsidian_targets,
            request_budgets=request_budgets,
            expected_current_digest=actual_current,
        )

    def _validate_adoption_inspection(self, inspection: WorkspaceAdoptionInspection) -> None:
        if not inspection.descriptor["admissible"]:
            raise AppOperationError(
                "RKBAPP-ADOPT-VALIDATION",
                "Existing workspace requires recovery or Guardian remediation",
            )
        for path in inspection.writable_roots.paths():
            if not self.root_security.capabilities(path)["accepted"]:
                raise AppOperationError(
                    "RKBAPP-ADOPT-ROOT",
                    "Existing workspace storage does not satisfy the beta policy",
                )
        for source_root in inspection.source_roots:
            capabilities = self.root_security.capabilities(source_root.path)
            if not (capabilities["local"] and capabilities["reparse_free"]):
                raise AppOperationError(
                    "RKBAPP-ADOPT-ROOT",
                    "Existing workspace source storage does not satisfy the beta policy",
                )

    def _require_selection_security_current(self, selection: object) -> None:
        expected = getattr(selection, "security_basis_digest", None)
        path = getattr(selection, "path", None)
        if not isinstance(expected, str) or not isinstance(path, Path):
            raise AppOperationError("RKBAPP-SELECTION-SECURITY", "Folder selection security identity is unavailable")
        try:
            current = _attestation_digest(self.root_security.inspect(path))
        except Exception as error:
            raise AppOperationError("RKBAPP-SELECTION-SECURITY", "Folder selection security could not be revalidated") from error
        if not secrets.compare_digest(current, expected):
            raise AppOperationError("RKBAPP-SELECTION-SECURITY", "Folder selection security changed after selection")

    def _adoption_root_security_digest(self, inspection: WorkspaceAdoptionInspection) -> str:
        roots = [
            {"kind": "writable", "identity": _attestation_basis(self.root_security.inspect(path))}
            for path in inspection.writable_roots.paths()
        ]
        roots.extend(
            {
                "kind": "source",
                "root_id": source_root.root_id,
                "identity": _attestation_basis(self.root_security.inspect(source_root.path)),
            }
            for source_root in inspection.source_roots
        )
        return _digest(roots)

    def _rollback_failed_adoption_commit(
        self,
        commit: ProfileCommit,
        previous: dict[str, Any] | None,
    ) -> None:
        if commit.result != "created":
            return
        predecessor = None if previous is None else previous["revision_id"]
        try:
            self.profile_store.rollback_current_if_matches(
                commit,
                predecessor_revision_id=predecessor,
            )
        except Exception as error:
            raise AppOperationError(
                "RKBAPP-ADOPT-ROLLBACK",
                "Unsafe workspace adoption could not restore the prior profile pointer",
            ) from error

    def _server_proposal_expiry(self) -> datetime:
        current = self._wall_clock()
        if current.tzinfo is None:
            raise AppOperationError("RKBAPP-SETUP-CLOCK", "Setup clock must be timezone-aware")
        return current.astimezone(UTC) + timedelta(seconds=SETUP_PROPOSAL_SECONDS)

    def _setup_recovery_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for entry in self.recovery_store.list_current():
            if entry.state not in {"commit_started", "core_committed"}:
                continue
            proposal = entry.proposal
            recovery = self.materializer.inspect_recovery(
                proposal.request.workspace_parent,
                proposal.operation_id,
                self.root_security,
            )
            if recovery.state == "absent":
                actions = ["restart_workspace_setup"]
            elif "discard_unchanged_owned_staging" in recovery.actions:
                actions = ["discard_workspace_staging"]
            elif any(
                action in recovery.actions
                for action in ("no_change", "resume_matching_published_generation", "complete_matching_receipt_journal")
            ):
                actions = ["resume_workspace_setup"]
            else:
                actions = []
            items.append(
                {
                    "operation_id": proposal.operation_id,
                    "workspace_label": proposal.request.workspace_label,
                    "state": recovery.state,
                    "actions": actions,
                }
            )
        return items


def _workspace_options(record: dict[str, Any] | None) -> list[WorkspaceOption]:
    if record is None:
        return []
    return [
        WorkspaceOption(item["option_id"], item["label"], Path(item["config_path"]))
        for item in record["workspaces"]
    ]


def _profile_settings(
    record: dict[str, Any] | None,
) -> tuple[list[ObsidianTarget], RequestBudgets]:
    if record is None:
        return [], DEFAULT_REQUEST_BUDGETS
    targets = [
        ObsidianTarget(
            item["target_id"],
            item["label"],
            item["workspace_option_id"],
            Path(item["vault_root"]),
            PurePosixPath(item["managed_subtree"]),
            PurePosixPath(item["personal_notes_subtree"]),
        )
        for item in record["obsidian_targets"]
    ]
    return targets, RequestBudgets(**record["request_budgets"])


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _attestation_basis(attestation: object) -> dict[str, Any]:
    return {
        "path_identity": getattr(attestation, "path_identity"),
        "volume_id": getattr(attestation, "volume_id"),
        "filesystem": getattr(attestation, "filesystem"),
        "local": getattr(attestation, "local"),
        "reparse_free": getattr(attestation, "reparse_free"),
        "acl_policy_id": getattr(attestation, "acl_policy_id"),
        "acl_secure": getattr(attestation, "acl_secure"),
    }


def _attestation_digest(attestation: object) -> str:
    return _digest(_attestation_basis(attestation))


def _attestation_capabilities(attestation: object) -> dict[str, Any]:
    basis = _attestation_basis(attestation)
    return {
        "filesystem": basis["filesystem"],
        "local": basis["local"],
        "reparse_free": basis["reparse_free"],
        "acl_policy_id": basis["acl_policy_id"],
        "acl_secure": basis["acl_secure"],
        "accepted": (
            str(basis["filesystem"]).upper() == "NTFS"
            and basis["local"] is True
            and basis["reparse_free"] is True
            and basis["acl_policy_id"] == ROOT_SECURITY_POLICY
            and basis["acl_secure"] is True
            and bool(basis["volume_id"])
        ),
    }


def _workspace_option_id(workspace_id: str) -> str:
    option_id = workspace_id.replace("_", "-").lower()
    if not _WORKSPACE_OPTION_ID.fullmatch(option_id):
        raise AppOperationError("RKBAPP-PROFILE-WORKSPACE", "Workspace identity cannot be represented as an App option")
    return option_id


__all__ = ["DEFAULT_REQUEST_BUDGETS", "SETUP_INTERFACE", "SetupRuntime"]
