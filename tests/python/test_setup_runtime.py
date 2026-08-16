from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from pathlib import PurePosixPath

import pytest
from research_kb.services.workspace_materialization import WorkspaceMaterializationApplicationService
from research_kb.services.workspace_adoption import WorkspaceAdoptionInspection
from research_kb.services.workspace_storage import WorkspaceStorageRoots
from research_kb.workspace_validation import SourceRootBinding

from research_kb_app.config import ObsidianTarget, RequestBudgets, WorkspaceOption
from research_kb_app.folder_helper import FolderHelperResult, FolderHelperService
from research_kb_app.errors import AppOperationError
from research_kb_app.product_profile import ManagedProductProfileStore
from research_kb_app.setup_runtime import SetupRuntime
from research_kb_app.windows_security import WindowsNamedMutexFactory, WindowsRootFacts, WindowsRootSecurityService


NOW = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)


class SequentialHelper:
    def __init__(self, paths):
        self.paths = iter(paths)

    def select(self, **_kwargs):
        return FolderHelperResult("selected", next(self.paths))

    def close(self):
        pass


class SequentialAdoptionInspector:
    def __init__(self, inspections):
        self.inspections = iter(inspections)

    def inspect(self, _config_path):
        return next(self.inspections)


def _adoption_inspection(root: Path, basis: str) -> WorkspaceAdoptionInspection:
    writable = root / "writable"
    knowledge = root / "knowledge"
    inbox = root / "inbox"
    source = root / "source"
    for path in (writable, knowledge, inbox, source):
        path.mkdir(parents=True, exist_ok=True)
    return WorkspaceAdoptionInspection(
        descriptor={
            "application_service_interface_version": "1.21",
            "workspace": {
                "workspace_id": "workspace_11111111-1111-4111-8111-111111111111",
                "domain_profile_id": "generic-research@1.0",
                "domain_name": "Generic Research",
                "domain_version": "1.0",
            },
            "guardian": {"status": "success", "finding_count": 0, "error_count": 0, "warning_count": 0},
            "transaction_recovery": {"status": "current", "action_count": 0},
            "admissible": True,
            "adoption_basis_digest": basis,
        },
        writable_roots=WorkspaceStorageRoots(writable, knowledge, inbox),
        source_roots=(SourceRootBinding("source", source, True),),
        basis_digest=basis,
    )


def test_workspace_setup_is_preview_bound_and_persists_profile_only_after_core_commit(tmp_path: Path) -> None:
    parent = tmp_path / "managed"
    sources = tmp_path / "sources"
    inbox = sources / "inbox"
    profile_root = tmp_path / "profile"
    frontend = tmp_path / "frontend"
    for path in (parent, inbox, profile_root, frontend):
        path.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    root_security = WindowsRootSecurityService(
        facts_probe=lambda _path: WindowsRootFacts("volume-c", "NTFS", True, True, True, True),
        acl_setter=lambda _path: None,
    )
    profile = ManagedProductProfileStore(profile_root, "default", clock=lambda: NOW)
    helper = SequentialHelper([parent, sources, inbox])
    runtime = SetupRuntime(
        profile_store=profile,
        frontend_root=frontend,
        root_security=root_security,
        folder_helper=helper,  # type: ignore[arg-type]
        materializer=WorkspaceMaterializationApplicationService(clock=lambda: NOW),
        writer_mutex=lambda _key: __import__("contextlib").nullcontext(),  # type: ignore[arg-type]
        monotonic_clock=lambda: 100.0,
        wall_clock=lambda: NOW,
    )

    parent_selection = runtime.select_folder(
        browser_session_id="browser-a", purpose="workspace_parent", allow_new_child=True, initial_location_id=None
    )["selection"]
    source_selection = runtime.select_folder(
        browser_session_id="browser-a", purpose="source_root", allow_new_child=False, initial_location_id=None
    )["selection"]
    inbox_selection = runtime.select_folder(
        browser_session_id="browser-a", purpose="local_inbox", allow_new_child=False, initial_location_id=None
    )["selection"]
    prepared = runtime.prepare_workspace(
        browser_session_id="browser-a",
        workspace_parent_lease_id=parent_selection["lease_id"],
        source_roots=[("local-sources", source_selection["lease_id"])],
        local_inbox_lease_id=inbox_selection["lease_id"],
        workspace_name="synthetic-workspace",
        workspace_label="Synthetic Workspace",
        idempotency_key="setup-0001",
    )

    assert not (parent / "synthetic-workspace").exists()
    assert profile.load_current(missing_ok=True) is None
    assert str(parent) not in str(prepared)
    prepared_anchor = runtime.recovery_store.list_current()[0]
    assert prepared_anchor.state == "prepared"
    assert prepared_anchor.proposal.preview_digest == prepared["preview_digest"]
    assert prepared_anchor.proposal.request.expires_at == NOW + timedelta(minutes=15)

    committed = runtime.commit_workspace(
        browser_session_id="browser-a",
        proposal_token=prepared["proposal_token"],
        preview_digest=prepared["preview_digest"],
    )

    assert committed["status"] == "success"
    assert (parent / "synthetic-workspace" / "workspace.yaml").is_file()
    current = profile.load_current()
    assert current is not None
    assert current["workspaces"][0]["config_path"].endswith("synthetic-workspace\\workspace.yaml")
    assert runtime.recovery_store.list_current()[0].state == "profile_committed"
    latest_receipt = sorted(runtime.recovery_store.root.glob("operation_*.json"))[-1]
    tampered = json.loads(latest_receipt.read_text(encoding="utf-8"))
    tampered["state"] = "commit_started"
    latest_receipt.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(AppOperationError, match="digest"):
        runtime.recovery_store.list_current()


def test_workspace_setup_recovers_profile_reference_after_process_restart(tmp_path: Path) -> None:
    parent = tmp_path / "managed"
    sources = tmp_path / "sources"
    inbox = sources / "inbox"
    profile_root = tmp_path / "profile"
    frontend = tmp_path / "frontend"
    for path in (parent, inbox, profile_root, frontend):
        path.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    root_security = WindowsRootSecurityService(
        facts_probe=lambda _path: WindowsRootFacts("volume-c", "NTFS", True, True, True, True),
        acl_setter=lambda _path: None,
    )
    profile = ManagedProductProfileStore(profile_root, "default", clock=lambda: NOW)
    helper = SequentialHelper([parent, sources, inbox])

    def crash_after_core_receipt(phase: str) -> None:
        if phase == "receipt_written":
            raise RuntimeError("synthetic app process crash")

    runtime = SetupRuntime(
        profile_store=profile,
        frontend_root=frontend,
        root_security=root_security,
        folder_helper=helper,  # type: ignore[arg-type]
        materializer=WorkspaceMaterializationApplicationService(
            clock=lambda: NOW,
            phase_hook=crash_after_core_receipt,
        ),
        writer_mutex=lambda _key: __import__("contextlib").nullcontext(),  # type: ignore[arg-type]
        monotonic_clock=lambda: 100.0,
        wall_clock=lambda: NOW,
    )
    parent_selection = runtime.select_folder(
        browser_session_id="browser-a", purpose="workspace_parent", allow_new_child=True, initial_location_id=None
    )["selection"]
    source_selection = runtime.select_folder(
        browser_session_id="browser-a", purpose="source_root", allow_new_child=False, initial_location_id=None
    )["selection"]
    inbox_selection = runtime.select_folder(
        browser_session_id="browser-a", purpose="local_inbox", allow_new_child=False, initial_location_id=None
    )["selection"]
    prepared = runtime.prepare_workspace(
        browser_session_id="browser-a",
        workspace_parent_lease_id=parent_selection["lease_id"],
        source_roots=[("local-sources", source_selection["lease_id"])],
        local_inbox_lease_id=inbox_selection["lease_id"],
        workspace_name="restart-workspace",
        workspace_label="Restart Workspace",
        idempotency_key="setup-restart-0001",
    )
    with pytest.raises(RuntimeError, match="process crash"):
        runtime.commit_workspace(
            browser_session_id="browser-a",
            proposal_token=prepared["proposal_token"],
            preview_digest=prepared["preview_digest"],
        )
    assert (parent / "restart-workspace" / "workspace.yaml").is_file()
    assert profile.load_current(missing_ok=True) is None

    restarted = SetupRuntime(
        profile_store=profile,
        frontend_root=frontend,
        root_security=root_security,
        materializer=WorkspaceMaterializationApplicationService(clock=lambda: NOW + timedelta(hours=1)),
        writer_mutex=lambda _key: __import__("contextlib").nullcontext(),  # type: ignore[arg-type]
        monotonic_clock=lambda: 10_000.0,
    )
    recovery = restarted.recovery()["workspace_setup_operations"]
    assert recovery == [
        {
            "operation_id": recovery[0]["operation_id"],
            "workspace_label": "Restart Workspace",
            "state": "complete",
            "actions": ["resume_workspace_setup"],
        }
    ]

    result = restarted.recover_workspace(recovery[0]["operation_id"], "resume_workspace_setup")

    assert result["status"] == "success"
    assert result["restart_required"] is True
    current = profile.load_current()
    assert current is not None
    assert current["workspaces"][0]["label"] == "Restart Workspace"
    assert restarted.recovery_store.list_current()[0].state == "profile_committed"


def test_adoption_revalidates_basis_preserves_profile_settings_and_exact_retry(tmp_path: Path) -> None:
    existing_config = tmp_path / "existing" / "workspace.yaml"
    existing_config.parent.mkdir()
    existing_config.write_text("synthetic\n", encoding="utf-8")
    new_workspace = tmp_path / "adopt"
    new_workspace.mkdir()
    (new_workspace / "workspace.yaml").write_text("synthetic\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    profile_root = tmp_path / "profile"
    frontend = tmp_path / "frontend"
    profile_root.mkdir()
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    profile = ManagedProductProfileStore(profile_root, "default", clock=lambda: NOW)
    budgets = RequestBudgets(2 * 1024 * 1024, 8192, 75, 45.0)
    target = ObsidianTarget(
        "obsidian-main",
        "Notes",
        "existing",
        vault,
        PurePosixPath("Managed"),
        PurePosixPath("Personal"),
    )
    profile.commit(
        workspaces=[WorkspaceOption("existing", "Existing", existing_config)],
        obsidian_targets=[target],
        request_budgets=budgets,
        expected_current_digest=None,
    )
    root_security = WindowsRootSecurityService(
        facts_probe=lambda _path: WindowsRootFacts("volume-c", "NTFS", True, True, True, True),
        acl_setter=lambda _path: None,
    )
    inspection = _adoption_inspection(tmp_path / "inspection", "a" * 64)
    runtime = SetupRuntime(
        profile_store=profile,
        frontend_root=frontend,
        root_security=root_security,
        folder_helper=SequentialHelper([new_workspace]),  # type: ignore[arg-type]
        adoption_inspector=SequentialAdoptionInspector([inspection, inspection, inspection]),  # type: ignore[arg-type]
        monotonic_clock=lambda: 100.0,
    )
    selected = runtime.select_folder(
        browser_session_id="browser-a",
        purpose="existing_workspace_config",
        allow_new_child=False,
        initial_location_id=None,
    )["selection"]
    preview = runtime.preview_adoption(
        browser_session_id="browser-a",
        selection_lease_id=selected["lease_id"],
    )

    first = runtime.commit_adoption(
        browser_session_id="browser-a",
        adoption_token=preview["adoption_token"],
        preview_digest=preview["preview_digest"],
        label="Adopted",
    )
    second = runtime.commit_adoption(
        browser_session_id="browser-a",
        adoption_token=preview["adoption_token"],
        preview_digest=preview["preview_digest"],
        label="Adopted",
    )

    assert second == first
    current = profile.load_current()
    assert current is not None
    assert current["request_budgets"] == {
        "max_body_bytes": budgets.max_body_bytes,
        "max_query_bytes": budgets.max_query_bytes,
        "max_page_size": budgets.max_page_size,
        "request_timeout_seconds": budgets.request_timeout_seconds,
    }
    assert current["obsidian_targets"][0]["target_id"] == "obsidian-main"
    assert {item["option_id"] for item in current["workspaces"]} == {
        "existing",
        "workspace-11111111-1111-4111-8111-111111111111",
    }


def test_adoption_fails_closed_when_core_basis_changes_after_preview(tmp_path: Path) -> None:
    selected_root = tmp_path / "adopt"
    selected_root.mkdir()
    (selected_root / "workspace.yaml").write_text("synthetic\n", encoding="utf-8")
    profile_root = tmp_path / "profile"
    frontend = tmp_path / "frontend"
    profile_root.mkdir()
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    root_security = WindowsRootSecurityService(
        facts_probe=lambda _path: WindowsRootFacts("volume-c", "NTFS", True, True, True, True),
        acl_setter=lambda _path: None,
    )
    runtime = SetupRuntime(
        profile_store=ManagedProductProfileStore(profile_root, "default", clock=lambda: NOW),
        frontend_root=frontend,
        root_security=root_security,
        folder_helper=SequentialHelper([selected_root]),  # type: ignore[arg-type]
        adoption_inspector=SequentialAdoptionInspector(
            [_adoption_inspection(tmp_path / "first", "a" * 64), _adoption_inspection(tmp_path / "second", "b" * 64)]
        ),  # type: ignore[arg-type]
        monotonic_clock=lambda: 100.0,
    )
    selected = runtime.select_folder(
        browser_session_id="browser-a",
        purpose="existing_workspace_config",
        allow_new_child=False,
        initial_location_id=None,
    )["selection"]
    preview = runtime.preview_adoption(browser_session_id="browser-a", selection_lease_id=selected["lease_id"])

    with pytest.raises(AppOperationError) as caught:
        runtime.commit_adoption(
            browser_session_id="browser-a",
            adoption_token=preview["adoption_token"],
            preview_digest=preview["preview_digest"],
            label="Adopted",
        )

    assert caught.value.code == "RKBAPP-ADOPT-STALE"


def test_task_package_destination_security_drift_is_invalidated_before_export(tmp_path: Path) -> None:
    destination = tmp_path / "packages"
    profile_root = tmp_path / "profile"
    frontend = tmp_path / "frontend"
    for path in (destination, profile_root, frontend):
        path.mkdir()
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    facts = iter(
        [
            WindowsRootFacts("volume-c", "NTFS", True, True, True, True),
            WindowsRootFacts("volume-d", "NTFS", True, True, True, True),
        ]
    )
    runtime = SetupRuntime(
        profile_store=ManagedProductProfileStore(profile_root, "default", clock=lambda: NOW),
        frontend_root=frontend,
        root_security=WindowsRootSecurityService(facts_probe=lambda _path: next(facts), acl_setter=lambda _path: None),
        folder_helper=SequentialHelper([destination]),  # type: ignore[arg-type]
        monotonic_clock=lambda: 100.0,
    )
    selected = runtime.select_folder(
        browser_session_id="browser-a",
        purpose="task_package_destination",
        allow_new_child=False,
        initial_location_id=None,
    )["selection"]

    class RejectExport:
        def export_task_package(self, *_args, **_kwargs):
            raise AssertionError("export must not run after destination identity drift")

    with pytest.raises(AppOperationError) as caught:
        runtime.export_agent_task_package(
            browser_session_id="browser-a",
            selection_lease_id=selected["lease_id"],
            handoff={"task_id": "agenttask_1234"},
            egress=RejectExport(),  # type: ignore[arg-type]
        )

    assert caught.value.code == "RKBAPP-SELECTION-SECURITY"
    assert list(destination.iterdir()) == []


def test_adoption_post_commit_drift_restores_prior_profile_pointer(tmp_path: Path) -> None:
    existing_config = tmp_path / "existing" / "workspace.yaml"
    existing_config.parent.mkdir()
    existing_config.write_text("synthetic\n", encoding="utf-8")
    selected_root = tmp_path / "adopt"
    selected_root.mkdir()
    (selected_root / "workspace.yaml").write_text("synthetic\n", encoding="utf-8")
    profile_root = tmp_path / "profile"
    frontend = tmp_path / "frontend"
    profile_root.mkdir()
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    profile = ManagedProductProfileStore(profile_root, "default", clock=lambda: NOW)
    previous = profile.commit(
        workspaces=[WorkspaceOption("existing", "Existing", existing_config)],
        obsidian_targets=[],
        request_budgets=RequestBudgets(4096, 1024, 50, 30.0),
        expected_current_digest=None,
    )
    stable = _adoption_inspection(tmp_path / "stable", "a" * 64)
    drifted = _adoption_inspection(tmp_path / "drifted", "b" * 64)
    runtime = SetupRuntime(
        profile_store=profile,
        frontend_root=frontend,
        root_security=WindowsRootSecurityService(
            facts_probe=lambda _path: WindowsRootFacts("volume-c", "NTFS", True, True, True, True),
            acl_setter=lambda _path: None,
        ),
        folder_helper=SequentialHelper([selected_root]),  # type: ignore[arg-type]
        adoption_inspector=SequentialAdoptionInspector([stable, stable, drifted]),  # type: ignore[arg-type]
        monotonic_clock=lambda: 100.0,
    )
    selected = runtime.select_folder(
        browser_session_id="browser-a",
        purpose="existing_workspace_config",
        allow_new_child=False,
        initial_location_id=None,
    )["selection"]
    preview = runtime.preview_adoption(browser_session_id="browser-a", selection_lease_id=selected["lease_id"])

    with pytest.raises(AppOperationError) as caught:
        runtime.commit_adoption(
            browser_session_id="browser-a",
            adoption_token=preview["adoption_token"],
            preview_digest=preview["preview_digest"],
            label="Adopted",
        )

    assert caught.value.code == "RKBAPP-ADOPT-STALE"
    assert profile.load_current()["revision_id"] == previous.revision_id
    assert len(tuple(profile.config_root.glob("profile-rev-*.json"))) == 2
