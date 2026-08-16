from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import pytest

from research_kb_app.config import RequestBudgets, WorkspaceOption
from research_kb_app.errors import AppOperationError
from research_kb.workspace_materialization import ROOT_SECURITY_POLICY, RootSecurityAttestation, path_identity
from research_kb_app.product_profile import ManagedProductProfileStore, ensure_managed_profile_root


BUDGETS = RequestBudgets(16384, 2048, 100, 30.0)


def _workspace(tmp_path: Path, option_id: str = "synthetic") -> WorkspaceOption:
    root = tmp_path / option_id
    root.mkdir(exist_ok=True)
    config = root / "workspace.yaml"
    config.write_text("synthetic\n", encoding="utf-8")
    return WorkspaceOption(option_id, "Synthetic Workspace", config)


def _store(tmp_path: Path, *, phase_hook=None) -> ManagedProductProfileStore:
    root = tmp_path / "profile"
    root.mkdir(exist_ok=True)
    return ManagedProductProfileStore(
        root,
        "default",
        clock=lambda: datetime(2026, 8, 7, 8, 0, tzinfo=UTC),
        phase_hook=phase_hook,
    )


def test_profile_revisions_are_immutable_and_exact_retry_is_no_change(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.commit(
        workspaces=[_workspace(tmp_path)],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=None,
    )
    original = (store.config_root / f"{first.revision_id}.json").read_bytes()

    repeated = store.commit(
        workspaces=[_workspace(tmp_path)],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=first.record_digest,
    )
    second = store.commit(
        workspaces=[_workspace(tmp_path), _workspace(tmp_path, "second")],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=first.record_digest,
    )

    assert repeated.result == "no_change"
    assert repeated.revision_id == first.revision_id
    assert second.revision == 2
    assert second.revision_id != first.revision_id
    assert (store.config_root / f"{first.revision_id}.json").read_bytes() == original


def test_profile_pointer_interruption_is_recoverable_without_overwriting_revision(tmp_path: Path) -> None:
    def interrupt(phase: str) -> None:
        if phase == "revision_persisted":
            raise RuntimeError("injected pointer interruption")

    store = _store(tmp_path, phase_hook=interrupt)
    with pytest.raises(RuntimeError, match="pointer interruption"):
        store.commit(
            workspaces=[_workspace(tmp_path)],
            obsidian_targets=[],
            request_budgets=BUDGETS,
            expected_current_digest=None,
        )

    recovery = store.inspect_recovery()
    assert recovery.state == "current_missing"
    assert len(recovery.recoverable_revision_ids) == 1
    selected = store.select_recovery_revision(recovery.recoverable_revision_ids[0])
    assert selected.result == "selected"
    assert store.load_current()["revision_id"] == selected.revision_id


def test_failed_second_revision_restores_first_pointer_and_retains_both_revisions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.commit(
        workspaces=[_workspace(tmp_path)],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=None,
    )
    second = store.commit(
        workspaces=[_workspace(tmp_path), _workspace(tmp_path, "second")],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=first.record_digest,
    )

    store.rollback_current_if_matches(second, predecessor_revision_id=first.revision_id)

    assert store.load_current()["revision_id"] == first.revision_id
    assert (store.config_root / f"{first.revision_id}.json").is_file()
    assert (store.config_root / f"{second.revision_id}.json").is_file()


def test_failed_first_revision_removes_only_pointer_and_retains_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.commit(
        workspaces=[_workspace(tmp_path)],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=None,
    )

    store.rollback_current_if_matches(first, predecessor_revision_id=None)

    assert store.load_current(missing_ok=True) is None
    assert not store.current_pointer_path.exists()
    assert (store.config_root / f"{first.revision_id}.json").is_file()


def test_managed_profile_root_is_created_only_through_security_controller(tmp_path: Path) -> None:
    class Controller:
        def __init__(self):
            self.created = []

        def inspect(self, path):
            return RootSecurityAttestation(path_identity(path), "volume-c", "NTFS", True, True, ROOT_SECURITY_POLICY, True)

        def secure_create(self, path, *, operation_id):
            path.mkdir()
            self.created.append((path, operation_id))
            return self.inspect(path)

    controller = Controller()
    root = ensure_managed_profile_root("default", controller, known_folder_resolver=lambda: tmp_path)

    assert root == (tmp_path / "ResearchKB" / "default").resolve()
    assert [item[0] for item in controller.created] == [tmp_path / "ResearchKB", tmp_path / "ResearchKB" / "default"]


def test_profile_load_rejects_digest_valid_but_structurally_invalid_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    committed = store.commit(
        workspaces=[_workspace(tmp_path)],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=None,
    )
    revision_path = store.config_root / f"{committed.revision_id}.json"
    record = json.loads(revision_path.read_text(encoding="utf-8"))
    record["workspaces"][0]["unexpected"] = True
    record["record_digest"] = _digest({key: value for key, value in record.items() if key != "record_digest"})
    revision_path.write_text(_json(record), encoding="utf-8", newline="\n")
    pointer = json.loads(store.current_pointer_path.read_text(encoding="utf-8"))
    pointer["record_digest"] = record["record_digest"]
    pointer["pointer_digest"] = _digest({key: value for key, value in pointer.items() if key != "pointer_digest"})
    store.current_pointer_path.write_text(_json(pointer), encoding="utf-8", newline="\n")

    with pytest.raises(AppOperationError, match="workspace entry"):
        store.load_current()


def test_profile_load_requires_bound_predecessor_revision(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.commit(
        workspaces=[_workspace(tmp_path)],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=None,
    )
    store.commit(
        workspaces=[_workspace(tmp_path), _workspace(tmp_path, "second")],
        obsidian_targets=[],
        request_budgets=BUDGETS,
        expected_current_digest=first.record_digest,
    )
    (store.config_root / f"{first.revision_id}.json").unlink()

    with pytest.raises(AppOperationError, match="unavailable"):
        store.load_current()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
