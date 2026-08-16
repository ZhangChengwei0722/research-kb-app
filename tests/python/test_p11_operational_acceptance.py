from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from research_kb.services import WorkspaceBootstrapService

from conftest import (
    EXPECTED_HOST,
    EXPECTED_ORIGIN,
    STARTUP_TOKEN,
    AppHarness,
    portable_fixture_root,
)
from research_kb_app.api import create_app
from research_kb_app.compatibility import load_compatibility
from research_kb_app.config import load_app_config


def _rebuild(harness: AppHarness, csrf: str) -> list[str]:
    accepted = harness.client.post(
        "/api/catalog/rebuild",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    assert accepted.status_code == 202, accepted.text
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        health = harness.client.get("/api/health")
        assert health.status_code == 200, health.text
        if health.json()["operation"]["state"] not in {"running", "building"}:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Catalog rebuild did not become idle")
    status = harness.client.get("/api/catalog/status")
    assert status.status_code == 200, status.text
    assert status.json()["projection_state"] == "current"
    items = harness.client.get("/api/catalog/items", params={"page_size": 100})
    assert items.status_code == 200, items.text
    return [item["item_id"] for item in items.json()["items"]]


def test_operational_lists_use_the_bound_catalog_projection(app_harness: AppHarness) -> None:
    app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    query = runtime.query
    calls: list[tuple[str, object]] = []

    class IntakeSpy:
        @staticmethod
        def limits(_session):
            return {"max_job_page_size": 100}

        @staticmethod
        def list_jobs(_session, *, page_size, cursor, catalog_query=None):
            calls.append(("job", catalog_query))
            return {"jobs": [], "next_cursor": None, "page_size": page_size, "cursor": cursor}

    class AgentSpy:
        @staticmethod
        def list_tasks(_session, *, page_size, cursor, catalog_query=None):
            calls.append(("task", catalog_query))
            return {"tasks": [], "next_cursor": None, "page_size": page_size, "cursor": cursor}

    runtime.intake = IntakeSpy()
    runtime.agent = AgentSpy()
    runtime._catalog_status = {"projection_state": "current"}

    runtime.list_jobs(page_size=20, cursor=None)
    runtime.list_agent_tasks(page_size=20, cursor=None)

    assert calls == [("job", query), ("task", query)]


def test_catalog_stale_transition_invalidates_query_operational_state(
    app_harness: AppHarness,
) -> None:
    app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime

    class QuerySpy:
        marked = False

        def mark_stale(self):
            self.marked = True

    query = QuerySpy()
    runtime.query = query
    runtime._catalog_status = {
        "projection_state": "current",
        "source_watermark": "sha256:current",
        "current_source_watermark": "sha256:current",
    }

    runtime._mark_catalog_stale()

    assert query.marked is True
    assert runtime._catalog_status["projection_state"] == "stale"


def test_deleted_and_corrupted_projection_rebuild_to_identical_answers(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    expected = _rebuild(app_harness, csrf)
    runtime = app_harness.client.app.state.runtime
    database = runtime.projection.paths.database_path

    database.unlink()
    reopened = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "p2-small"},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["catalog"]["projection_state"] == "missing"
    assert _rebuild(app_harness, csrf) == expected

    database.write_bytes(b"not-a-sqlite-projection")
    reopened = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "p2-small"},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["catalog"]["projection_state"] == "corrupt"
    assert _rebuild(app_harness, csrf) == expected


def test_three_configured_workspaces_keep_projection_and_opaque_state_isolated(
    tmp_path: Path,
) -> None:
    fixture = portable_fixture_root()
    workspace_options = []
    workspace_ids = (
        "workspace_10000000-0000-4000-8000-000000000001",
        "workspace_10000000-0000-4000-8000-000000000002",
        "workspace_10000000-0000-4000-8000-000000000003",
    )
    for index, workspace_id in enumerate(workspace_ids, start=1):
        root = tmp_path / f"workspace-{index}"
        shutil.copytree(fixture, root)
        config_path = root / "workspace.yaml"
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        payload["workspace"]["id"] = workspace_id
        payload["workspace"]["local_inbox"] = "./sources/inbox"
        config_path.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
            newline="\n",
        )
        guardian_path = root / "knowledge" / "guardian" / "reports.jsonl"
        guardian_records = [
            json.loads(line)
            for line in guardian_path.read_text(encoding="utf-8").splitlines()
        ]
        for record in guardian_records:
            record["workspace_id"] = workspace_id
        guardian_path.write_text(
            "".join(
                json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                + "\n"
                for record in guardian_records
            ),
            encoding="utf-8",
            newline="\n",
        )
        runtime_root = root / "knowledge" / ".research-kb"
        if runtime_root.exists():
            shutil.rmtree(runtime_root)
        (root / "sources" / "inbox").mkdir(parents=True, exist_ok=True)
        assert WorkspaceBootstrapService(config_path).run().exit_code == 0
        workspace_options.append(
            {
                "option_id": f"workspace-{index}",
                "label": f"Synthetic {index}",
                "config_path": str(config_path.resolve()),
            }
        )

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<!doctype html><title>P11</title>\n", encoding="utf-8")
    app_config_path = tmp_path / "app-config.json"
    app_config_path.write_text(
        json.dumps(
            {
                "contract_version": "research-kb-app-config@1.0",
                "workspaces": workspace_options,
                "state_root": str((tmp_path / "app-state").resolve()),
                "log_root": str((tmp_path / "app-state" / "logs").resolve()),
                "frontend_root": str(frontend.resolve()),
                "request_budgets": {
                    "max_body_bytes": 4096,
                    "max_query_bytes": 1024,
                    "max_page_size": 100,
                    "request_timeout_seconds": 30,
                },
            }
        ),
        encoding="utf-8",
    )
    compatibility = load_compatibility()
    app = create_app(
        load_app_config(app_config_path),
        compatibility,
        startup_token=STARTUP_TOKEN,
        expected_host=EXPECTED_HOST,
        expected_origin=EXPECTED_ORIGIN,
    )
    with TestClient(app, base_url=EXPECTED_ORIGIN) as client:
        bootstrap = client.post(
            "/api/session/bootstrap",
            headers={"Origin": EXPECTED_ORIGIN},
            json={"startup_token": STARTUP_TOKEN},
        )
        assert bootstrap.status_code == 200
        csrf = client.get("/api/session/csrf").json()["csrf_token"]
        runtime = client.app.state.runtime
        projection_paths = []
        for index in range(1, 4):
            opened = client.post(
                "/api/workspaces/open",
                headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
                json={"option_id": f"workspace-{index}"},
            )
            assert opened.status_code == 200, opened.text
            assert opened.json()["workspace"]["workspace_id"] == workspace_ids[index - 1]
            projection_paths.append(runtime.projection.paths.database_path)
            assert runtime._agent_leases == {}
            assert runtime._obsidian_leases == {}
            assert runtime._exchange_uploads == {}
            assert runtime._exchange_previews == {}
            assert runtime._exchange_downloads == {}
            if index < 3:
                runtime._agent_leases["stale"] = {"workspace": index}

        assert len(set(projection_paths)) == 3
        assert all(path.parent != other.parent for i, path in enumerate(projection_paths) for other in projection_paths[i + 1 :])
