from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import EXPECTED_HOST, EXPECTED_ORIGIN, STARTUP_TOKEN
from research_kb_app.api import create_app
from research_kb_app.compatibility import load_compatibility
from research_kb_app.config import AppConfig, RequestBudgets


class FakeSetupRuntime:
    def __init__(self) -> None:
        self.callback = None
        self.cleared = False
        self.prepare_calls = []

    def set_profile_committed_callback(self, callback) -> None:
        self.callback = callback

    def clear(self) -> None:
        self.cleared = True

    def status(self):
        return {
            "status": "success",
            "interface_version": "research-kb-app-setup@1.0",
            "mode": "first_run",
            "profile_id": "default",
            "current_revision_id": None,
            "recovery_available": False,
        }

    def select_folder(self, **kwargs):
        assert kwargs["browser_session_id"]
        return {
            "status": "success",
            "interface_version": "research-kb-app-setup@1.0",
            "selection": {
                "lease_id": "selection_" + "a" * 48,
                "purpose": kwargs["purpose"],
                "display_label": "Research Workspace",
                "capabilities": {"accepted": True},
                "expires_in_seconds": 120,
            },
        }

    def recovery(self):
        return {
            "status": "success",
            "interface_version": "research-kb-app-setup@1.0",
            "profile_state": "current_missing",
            "current_revision_id": None,
            "recoverable_revision_ids": [],
            "workspace_setup_operations": [],
        }

    def prepare_workspace(self, **kwargs):
        self.prepare_calls.append(kwargs)
        return {"status": "success"}

    def recover_profile(self, revision_id: str):
        return {"status": "success", "profile_revision_id": revision_id, "restart_required": True}

    def recover_workspace(self, operation_id: str, action: str):
        return {
            "status": "success",
            "operation_id": operation_id,
            "result": action,
            "restart_required": True,
        }

    def export_agent_task_package(self, **kwargs):
        assert kwargs["browser_session_id"]
        assert kwargs["selection_lease_id"] == "selection_" + "a" * 48
        assert kwargs["handoff"]["task_id"] == "agenttask_1234"
        return {
            "status": "success",
            "route": "local_agent_package",
            "filename": "research-kb-agent-task-agenttask_1234.json",
            "content_sha256": "d" * 64,
            "content_utf8_bytes": 123,
        }


class FakeEgress:
    def __init__(self) -> None:
        self.closed = False

    def show(self):
        return {"status": "success", "policy_id": "research-kb-egress-policy@1.0"}

    def copy_text(self, text: str, **_kwargs):
        assert text == "server-derived metadata"
        return {
            "status": "success",
            "route": "clipboard",
            "content_sha256": "b" * 64,
            "timed_clear_scheduled": False,
        }

    def close(self) -> None:
        self.closed = True


def _config(tmp_path: Path) -> AppConfig:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    return AppConfig(
        path=tmp_path / "current.json",
        workspaces=(),
        state_root=tmp_path / "state",
        log_root=tmp_path / "state" / "logs",
        frontend_root=frontend,
        request_budgets=RequestBudgets(4096, 1024, 100, 30),
    )


def test_setup_and_egress_routes_preserve_session_csrf_and_path_boundaries(tmp_path: Path) -> None:
    setup = FakeSetupRuntime()
    egress = FakeEgress()
    app = create_app(
        _config(tmp_path),
        load_compatibility(),
        startup_token=STARTUP_TOKEN,
        expected_host=EXPECTED_HOST,
        expected_origin=EXPECTED_ORIGIN,
        setup_runtime=setup,  # type: ignore[arg-type]
        egress_policy=egress,  # type: ignore[arg-type]
    )
    app.state.runtime.prepare_task_metadata_egress = lambda task_id, expected: {
        "task_id": task_id,
        "text": "server-derived metadata",
        "content_classes": ["metadata"],
    }
    with TestClient(app, base_url=EXPECTED_ORIGIN) as client:
        assert client.get("/api/setup/status").status_code == 401
        bootstrap = client.post(
            "/api/session/bootstrap",
            headers={"Origin": EXPECTED_ORIGIN},
            json={"startup_token": STARTUP_TOKEN},
        )
        assert bootstrap.status_code == 200
        csrf = client.get("/api/session/csrf").json()["csrf_token"]

        missing_csrf = client.post(
            "/api/setup/select-folder",
            headers={"Origin": EXPECTED_ORIGIN},
            json={"purpose": "workspace_parent", "allow_new_child": True},
        )
        unknown = client.post(
            "/api/setup/select-folder",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={"purpose": "workspace_parent", "allow_new_child": True, "path": "C:\\private"},
        )
        selected = client.post(
            "/api/setup/select-folder",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={"purpose": "workspace_parent", "allow_new_child": True},
        )
        browser_expiry = client.post(
            "/api/setup/prepare-workspace",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={
                "workspace_parent_lease_id": "selection_" + "a" * 48,
                "source_roots": [{"root_id": "source-1", "selection_lease_id": "selection_" + "b" * 48}],
                "local_inbox_lease_id": "selection_" + "c" * 48,
                "workspace_name": "synthetic",
                "workspace_label": "Synthetic",
                "idempotency_key": "setup-one",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
        spoofed = client.post(
            "/api/egress/clipboard",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={
                "text": "visible metadata",
                "content_classes": ["metadata"],
                "metadata_disclosure_accepted": True,
            },
        )
        copied = client.post(
            "/api/egress/clipboard",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={
                "action": "metadata_only",
                "task_id": "agenttask_1234",
                "expected_state_id": "taskstate_1234",
                "expected_state_digest": "a" * 64,
                "metadata_disclosure_accepted": True,
            },
        )

        assert missing_csrf.status_code == 401
        assert unknown.status_code == 422
        assert selected.status_code == 200
        assert browser_expiry.status_code == 422
        assert setup.prepare_calls == []
        assert "C:\\private" not in unknown.text + selected.text
        assert "path" not in selected.json()["selection"]
        assert spoofed.status_code == 422
        assert copied.status_code == 200
        assert "visible metadata" not in copied.text
    assert setup.cleared is True
    assert egress.closed is True


def test_setup_recovery_action_requires_exact_identity_variant(tmp_path: Path) -> None:
    setup = FakeSetupRuntime()
    app = create_app(
        _config(tmp_path),
        load_compatibility(),
        startup_token=STARTUP_TOKEN,
        expected_host=EXPECTED_HOST,
        expected_origin=EXPECTED_ORIGIN,
        setup_runtime=setup,  # type: ignore[arg-type]
        egress_policy=FakeEgress(),  # type: ignore[arg-type]
    )
    with TestClient(app, base_url=EXPECTED_ORIGIN) as client:
        client.post(
            "/api/session/bootstrap",
            headers={"Origin": EXPECTED_ORIGIN},
            json={"startup_token": STARTUP_TOKEN},
        )
        csrf = client.get("/api/session/csrf").json()["csrf_token"]
        headers = {"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf}
        invalid = client.post(
            "/api/setup/recovery/action",
            headers=headers,
            json={
                "action": "select_profile_revision",
                "revision_id": "profile-rev-" + "a" * 32,
                "operation_id": "operation_00000000-0000-4000-8000-000000000000",
            },
        )
        resumed = client.post(
            "/api/setup/recovery/action",
            headers=headers,
            json={
                "action": "resume_workspace_setup",
                "operation_id": "operation_00000000-0000-4000-8000-000000000000",
            },
        )

        assert invalid.status_code == 422
        assert resumed.status_code == 200
        assert resumed.json()["result"] == "resume_workspace_setup"


def test_task_package_route_regenerates_server_handoff_and_favicon_is_empty(tmp_path: Path) -> None:
    setup = FakeSetupRuntime()
    app = create_app(
        _config(tmp_path),
        load_compatibility(),
        startup_token=STARTUP_TOKEN,
        expected_host=EXPECTED_HOST,
        expected_origin=EXPECTED_ORIGIN,
        setup_runtime=setup,  # type: ignore[arg-type]
        egress_policy=FakeEgress(),  # type: ignore[arg-type]
    )

    async def prepare(task_id, expected, executor_id):
        assert task_id == "agenttask_1234"
        assert expected == {"state_id": "taskstate_1234", "state_digest": "a" * 64}
        assert executor_id == "codex_cli"
        return {
            "handoff": {
                "task_id": task_id,
                "effective_content_classes": ["parsed_excerpt"],
            }
        }

    app.state.runtime.prepare_agent_handoff = prepare
    with TestClient(app, base_url=EXPECTED_ORIGIN) as client:
        assert client.get("/favicon.ico").status_code == 204
        client.post(
            "/api/session/bootstrap",
            headers={"Origin": EXPECTED_ORIGIN},
            json={"startup_token": STARTUP_TOKEN},
        )
        csrf = client.get("/api/session/csrf").json()["csrf_token"]
        exported = client.post(
            "/api/egress/agent-task-package/agenttask_1234",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={
                "expected_state_id": "taskstate_1234",
                "expected_state_digest": "a" * 64,
                "executor_id": "codex_cli",
                "selection_lease_id": "selection_" + "a" * 48,
            },
        )

        assert exported.status_code == 200
        assert exported.json()["filename"] == "research-kb-agent-task-agenttask_1234.json"
        assert str(tmp_path) not in exported.text
