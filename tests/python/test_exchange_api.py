from __future__ import annotations

import asyncio
import threading

import pytest

from conftest import EXPECTED_ORIGIN
from research_kb_app.errors import AppOperationError
from research_kb_app.runtime import AppRuntime, ExchangePreviewLease, OperationCoordinator


ARCHIVE_TYPE = "application/vnd.research-kb.exchange+zip"


def _post(harness, path: str, csrf: str, payload: dict):
    return harness.client.post(
        path,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=payload,
    )


def test_source_free_exchange_round_trip_uses_single_use_tokens_and_external_trust(app_harness) -> None:
    csrf = app_harness.open_workspace()
    capabilities = app_harness.client.get("/api/exchange/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["safe_reader_profile"]["profile_id"] == "p10-exchange-safe-reader-v1"

    preview = _post(
        app_harness,
        "/api/exchange/export/preview",
        csrf,
        {"scope": "workspace", "selector_id": None, "include_sources": False, "rights_asserted": False},
    )
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["record_count"] > 0
    assert "basis_digest" not in preview_payload
    assert "source_ref" not in preview.text

    built = _post(
        app_harness,
        "/api/exchange/export/build",
        csrf,
        {"preview_token": preview_payload["preview_token"]},
    )
    assert built.status_code == 200, built.text
    built_payload = built.json()
    assert built_payload["archive_bytes"] > 0
    assert "path" not in built.text.lower()

    stale_build = _post(
        app_harness,
        "/api/exchange/export/build",
        csrf,
        {"preview_token": preview_payload["preview_token"]},
    )
    assert stale_build.status_code == 409

    download = app_harness.client.get(
        f"/api/exchange/export/download/{built_payload['download_token']}"
    )
    assert download.status_code == 200
    assert download.headers["content-type"] == ARCHIVE_TYPE
    assert download.content.startswith(b"PK")
    assert app_harness.client.get(
        f"/api/exchange/export/download/{built_payload['download_token']}"
    ).status_code == 409

    uploaded = app_harness.client.post(
        "/api/exchange/import/upload",
        headers={
            "Origin": EXPECTED_ORIGIN,
            "X-RKB-CSRF": csrf,
            "Content-Type": ARCHIVE_TYPE,
            "Content-Length": str(len(download.content)),
        },
        content=download.content,
    )
    assert uploaded.status_code == 200, uploaded.text
    upload_payload = uploaded.json()
    assert "path" not in uploaded.text.lower()

    import_preview = _post(
        app_harness,
        "/api/exchange/import/preview",
        csrf,
        {"upload_token": upload_payload["upload_token"]},
    )
    assert import_preview.status_code == 200, import_preview.text
    import_payload = import_preview.json()
    assert import_payload["compatibility"] == "supported"
    assert import_payload["trust_projection"] == "unsigned_external_claims"
    assert "archive_sha256" not in import_payload

    applied = _post(
        app_harness,
        "/api/exchange/import/apply",
        csrf,
        {"preview_token": import_payload["preview_token"]},
    )
    assert applied.status_code == 200, applied.text
    import_id = applied.json()["import_id"]
    assert applied.json()["canonical_scientific_write"] is False
    assert _post(
        app_harness,
        "/api/exchange/import/apply",
        csrf,
        {"preview_token": import_payload["preview_token"]},
    ).status_code == 409

    imports = app_harness.client.get("/api/exchange/imports")
    assert imports.status_code == 200
    assert imports.json()["imports"][0]["import_id"] == import_id
    detail = app_harness.client.get(f"/api/exchange/imports/{import_id}")
    assert detail.status_code == 200
    assert detail.json()["records"]
    assert all(
        item["local_admissibility"] == "external_unreviewed"
        for item in detail.json()["records"]
    )
    assert "use as local fact" not in detail.text.lower()
    assert "\\" not in detail.text


def test_exchange_upload_requires_exact_media_type_length_auth_and_budget(app_harness) -> None:
    csrf = app_harness.open_workspace()
    archive = b"PK\x03\x04synthetic"
    missing_length = app_harness.client.post(
        "/api/exchange/import/upload",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf, "Content-Type": ARCHIVE_TYPE},
        content=archive,
    )
    assert missing_length.status_code in {200, 411}
    if missing_length.status_code == 200:
        # TestClient supplies Content-Length automatically; the endpoint still validates it.
        assert "upload_token" in missing_length.json()

    wrong_type = app_harness.client.post(
        "/api/exchange/import/upload",
        headers={
            "Origin": EXPECTED_ORIGIN,
            "X-RKB-CSRF": csrf,
            "Content-Type": "application/zip",
        },
        content=archive,
    )
    assert wrong_type.status_code == 415
    unauthenticated = app_harness.client.post(
        "/api/exchange/import/upload",
        headers={"Origin": EXPECTED_ORIGIN, "Content-Type": ARCHIVE_TYPE},
        content=archive,
    )
    assert unauthenticated.status_code == 401


def test_expired_exchange_upload_is_rejected_and_cleaned(app_harness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    now = [0.0]
    runtime._monotonic_clock = lambda: now[0]
    archive = _build_source_free_archive(app_harness, csrf)
    uploaded = app_harness.client.post(
        "/api/exchange/import/upload",
        headers={
            "Origin": EXPECTED_ORIGIN,
            "X-RKB-CSRF": csrf,
            "Content-Type": ARCHIVE_TYPE,
            "Content-Length": str(len(archive)),
        },
        content=archive,
    )
    token = uploaded.json()["upload_token"]
    spool = app_harness.config.state_root / "exchange-spool"
    assert any(spool.iterdir())

    now[0] = 301.0
    expired = _post(
        app_harness,
        "/api/exchange/import/preview",
        csrf,
        {"upload_token": token},
    )

    assert expired.status_code == 409
    assert expired.json()["diagnostic"]["code"] == "RKBAPP-EXCHANGE-UPLOAD-STALE"
    assert not list(spool.iterdir())


def test_cancelled_exchange_build_settles_worker_and_releases_operation(app_harness) -> None:
    app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    preview = runtime.preview_exchange_export(
        browser_session_id="browser-session",
        request={
            "scope": "workspace",
            "selector_id": None,
            "include_sources": False,
            "rights_asserted": False,
        },
    )
    started = threading.Event()
    release = threading.Event()
    original_build = runtime.exchange.build_export

    def blocking_build(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original_build(*args, **kwargs)

    runtime.exchange.build_export = blocking_build

    async def scenario() -> None:
        task = asyncio.create_task(
            runtime.build_exchange_export(
                browser_session_id="browser-session",
                preview_token=preview["preview_token"],
            )
        )
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert runtime.operation.is_busy()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not runtime.operation.is_busy()
        spool = app_harness.config.state_root / "exchange-spool"
        assert not spool.exists() or not list(spool.iterdir())

    asyncio.run(scenario())


def test_import_preview_without_upload_fails_closed() -> None:
    runtime = object.__new__(AppRuntime)
    runtime.operation = OperationCoordinator()
    runtime.active_option_id = "workspace"
    runtime._monotonic_clock = lambda: 0.0
    runtime._exchange_uploads = {}
    runtime._exchange_downloads = {}
    runtime._exchange_previews = {
        "preview-token": ExchangePreviewLease(
            token="preview-token",
            kind="import",
            browser_session_id="browser-session",
            workspace_option_id="workspace",
            request={},
            preview={},
            upload=None,
            created_at="2026-08-04T00:00:00Z",
            expires_at=300.0,
        )
    }

    with pytest.raises(AppOperationError) as caught:
        asyncio.run(
            runtime.apply_exchange_import(
                browser_session_id="browser-session",
                preview_token="preview-token",
            )
        )

    assert caught.value.code == "RKBAPP-EXCHANGE-UPLOAD-MISSING"
    assert not runtime.operation.is_busy()


def _build_source_free_archive(app_harness, csrf: str) -> bytes:
    preview = _post(
        app_harness,
        "/api/exchange/export/preview",
        csrf,
        {"scope": "workspace", "selector_id": None, "include_sources": False, "rights_asserted": False},
    ).json()
    built = _post(
        app_harness,
        "/api/exchange/export/build",
        csrf,
        {"preview_token": preview["preview_token"]},
    ).json()
    response = app_harness.client.get(
        f"/api/exchange/export/download/{built['download_token']}"
    )
    assert response.status_code == 200
    return response.content
