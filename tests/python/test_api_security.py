from __future__ import annotations

import time

from research_kb.services import CatalogProjectionService

from conftest import EXPECTED_ORIGIN, STARTUP_TOKEN, AppHarness, tree_digest


def test_runtime_is_public_but_workspace_facts_require_session(app_harness: AppHarness) -> None:
    runtime = app_harness.client.get("/api/runtime")
    workspaces = app_harness.client.get("/api/workspaces")

    assert runtime.status_code == 200
    assert runtime.json()["surface"] == "p11-operational-workspace"
    assert workspaces.status_code == 401
    assert "config_path" not in runtime.text


def test_bootstrap_is_one_time_and_validation_does_not_echo_secret(app_harness: AppHarness) -> None:
    first = app_harness.client.post(
        "/api/session/bootstrap",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"startup_token": STARTUP_TOKEN},
    )
    replay = app_harness.client.post(
        "/api/session/bootstrap",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"startup_token": STARTUP_TOKEN},
    )
    malformed = app_harness.client.post(
        "/api/session/bootstrap",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"startup_token": 123},
    )

    assert first.status_code == 200
    cookie = first.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "domain=" not in cookie
    assert replay.status_code == 401
    assert malformed.status_code == 422
    assert STARTUP_TOKEN not in replay.text + malformed.text


def test_exact_host_origin_csrf_and_security_headers(app_harness: AppHarness) -> None:
    host = app_harness.client.get("/api/runtime", headers={"Host": "localhost"})
    origin = app_harness.client.post(
        "/api/session/bootstrap",
        headers={"Origin": "http://attacker.invalid"},
        json={"startup_token": STARTUP_TOKEN},
    )
    csrf = app_harness.bootstrap()
    missing_csrf = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"option_id": "p2-small"},
    )
    opened = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "p2-small"},
    )

    assert host.status_code == 400
    assert origin.status_code == 403
    assert missing_csrf.status_code == 401, missing_csrf.text
    assert opened.status_code == 200, opened.text
    assert opened.headers["content-security-policy"].startswith("default-src 'self'")
    assert opened.headers["x-content-type-options"] == "nosniff"
    assert "config_path" not in opened.text


def test_catalog_rebuild_is_disposable_and_canonical_tree_is_unchanged(app_harness: AppHarness) -> None:
    before = tree_digest(app_harness.workspace_root)
    csrf = app_harness.bootstrap()
    app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "p2-small"},
    )
    accepted = app_harness.client.post(
        "/api/catalog/rebuild",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    assert accepted.status_code == 202, accepted.text

    deadline = time.monotonic() + 10
    status = app_harness.client.get("/api/catalog/status").json()
    while status["operation"]["state"] == "building" and time.monotonic() < deadline:
        time.sleep(0.05)
        status = app_harness.client.get("/api/catalog/status").json()

    items = app_harness.client.get("/api/catalog/items", params={"page_size": 5})
    assert status["operation"]["state"] == "current"
    assert status["projection_state"] == "current"
    assert items.status_code == 200
    assert len(items.json()["items"]) == 5
    selected = items.json()["items"][0]
    detail = app_harness.client.get(f"/api/catalog/items/{selected['item_id']}")
    capabilities = app_harness.client.get("/api/capabilities")
    health = app_harness.client.get("/api/health")
    assert detail.status_code == 200
    assert detail.json()["current_record_status"] == "current"
    assert detail.json()["item"]["record_id"] == selected["record_id"]
    assert capabilities.json()["app"]["surface"] == "p11-operational-workspace"
    assert capabilities.json()["app"]["canonical_scientific_writes"] == "user_approved_only"
    assert capabilities.json()["app"]["operational_acceptance"] == {
        "backup_restore": True,
        "operational_maintenance": True,
        "lazy_stale_maintenance": True,
    }
    assert health.json()["projection_state"] == "current"
    assert health.json()["operational_acceptance"] == {
        "backup_restore": True,
        "operational_maintenance": True,
        "lazy_stale_maintenance": True,
        "projection_rebuildable": True,
    }
    assert "config_path" not in detail.text + capabilities.text + health.text
    assert tree_digest(app_harness.workspace_root) == before


def test_workspace_open_status_and_health_use_one_cached_inspection(
    app_harness: AppHarness,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        CatalogProjectionService,
        "status",
        lambda _self: (_ for _ in ()).throw(AssertionError("deep status scan")),
    )
    csrf = app_harness.bootstrap()

    opened = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "p2-small"},
    )
    status = app_harness.client.get("/api/catalog/status")
    health = app_harness.client.get("/api/health")

    assert opened.status_code == 200, opened.text
    assert status.status_code == 200, status.text
    assert health.status_code == 200, health.text
    assert opened.json()["catalog"]["projection_state"] == "missing"
    assert status.json()["projection_state"] == "missing"
    assert health.json()["projection_state"] == "missing"


def test_budgets_and_path_shaped_ids_fail_closed(app_harness: AppHarness) -> None:
    csrf = app_harness.bootstrap()
    too_large = app_harness.client.post(
        "/api/workspaces/open",
        headers={
            "Origin": EXPECTED_ORIGIN,
            "X-RKB-CSRF": csrf,
            "Content-Type": "application/json",
        },
        content=b'{' + b'"x":"' + b"a" * 5000 + b'"}',
    )
    path_id = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "../workspace"},
    )

    assert too_large.status_code == 413
    assert path_id.status_code == 400
    assert "../workspace" not in path_id.text
