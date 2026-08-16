from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from research_kb_app.config import ObsidianTarget

from tests.python.conftest import EXPECTED_ORIGIN, AppHarness


GENERATION_ID = f"gen-{'a' * 64}"
MANIFEST_DIGEST = "b" * 64
SOURCE_WATERMARK = "c" * 64


class FakeObsidianGeneratedViewsService:
    def __init__(self) -> None:
        self.files = {
            "Home.md": b"# Synthetic Home\n",
            "Papers/_index.md": b"# Synthetic Papers\n",
        }
        self.render_requests: list[dict] = []
        self.stream_calls = 0
        self.generation_id = GENERATION_ID
        self.manifest_digest = MANIFEST_DIGEST
        self.source_watermark = SOURCE_WATERMARK
        self.change_on_cursor = False

    def limits(self, session):
        return {
            "status": "success",
            "optional_tables": ["library_summary", "question_coverage"],
            "max_status_page_size": 100,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def status(self, session, *, page_size: int, cursor: str | None):
        manifest_digest = "e" * 64 if self.change_on_cursor and cursor is not None else self.manifest_digest
        entries = [
            {
                "logical_path": logical_path,
                "view_kind": "home" if logical_path == "Home.md" else "paper_index",
                "view_id": "home" if logical_path == "Home.md" else "paper-index",
                "freshness": "current",
                "freshness_reasons": [],
                "source_watermark": self.source_watermark,
                "content_digest": hashlib.sha256(content).hexdigest(),
                "byte_count": len(content),
                "rendered_at": "2026-08-04T08:00:00Z",
            }
            for logical_path, content in sorted(self.files.items())
        ]
        start = 0 if cursor is None else next(
            index + 1 for index, item in enumerate(entries) if item["logical_path"] == cursor
        )
        page = entries[start : start + page_size]
        return {
            "status": "success",
            "projection_state": "ready",
            "integrity_state": "intact",
            "generation_id": self.generation_id,
            "manifest_digest": manifest_digest,
            "source_watermark": self.source_watermark,
            "optional_tables": ["library_summary"],
            "entries": page,
            "file_count": len(entries),
            "current_count": len(entries),
            "stale_count": 0,
            "edited_paths": [],
            "edited_paths_truncated": False,
            "next_cursor": page[-1]["logical_path"] if start + page_size < len(entries) else None,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def preview_render(self, session, *, optional_tables):
        return {
            "status": "success",
            "projection_state": "ready",
            "integrity_state": "intact",
            "generation_id": self.generation_id,
            "current_manifest_digest": self.manifest_digest,
            "source_watermark": self.source_watermark,
            "optional_tables": list(optional_tables),
            "proposed_file_count": 2,
            "changed_file_count": 1,
            "removed_file_count": 0,
            "changed_paths": ["Home.md"],
            "changed_paths_truncated": False,
            "removed_paths": [],
            "removed_paths_truncated": False,
            "edited_paths": [],
            "edited_paths_truncated": False,
            "expected_state": {
                "source_watermark": self.source_watermark,
                "manifest_digest": self.manifest_digest,
                "integrity_digest": "d" * 64,
            },
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def render(self, session, request, *, actor: str):
        assert actor == "user"
        self.render_requests.append(dict(request))
        return {
            "status": "success",
            "result": "committed",
            "generation_id": self.generation_id,
            "manifest_digest": self.manifest_digest,
            "source_watermark": self.source_watermark,
            "file_count": 2,
            "changed_file_count": 1,
            "removed_file_count": 0,
            "persistent_writes": 1,
            "canonical_scientific_write": False,
        }

    def stream_snapshot(self, session, *, expected_manifest_digest: str, sink):
        assert expected_manifest_digest == self.manifest_digest
        self.stream_calls += 1
        for logical_path, content in sorted(self.files.items()):
            sink(logical_path, hashlib.sha256(content).hexdigest(), content)
        return {
            "status": "success",
            "generation_id": self.generation_id,
            "manifest_digest": self.manifest_digest,
            "file_count": len(self.files),
            "byte_count": sum(len(content) for content in self.files.values()),
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }


def _enable_obsidian(harness: AppHarness, tmp_path: Path) -> tuple[str, ObsidianTarget, FakeObsidianGeneratedViewsService]:
    csrf = harness.open_workspace()
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    target = ObsidianTarget(
        target_id="synthetic-vault",
        label="Synthetic Vault",
        workspace_option_id="p2-small",
        vault_root=vault,
        managed_subtree=PurePosixPath("Research KB/Generated"),
        personal_notes_subtree=PurePosixPath("Research KB/Personal"),
    )
    runtime = harness.client.app.state.runtime
    runtime.config = replace(runtime.config, obsidian_targets=(target,))
    service = FakeObsidianGeneratedViewsService()
    runtime.obsidian = service
    return csrf, target, service


def _post(harness: AppHarness, csrf: str, path: str, payload: dict):
    return harness.client.post(
        path,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=payload,
    )


def test_obsidian_reads_are_bounded_and_path_free(app_harness: AppHarness, tmp_path: Path) -> None:
    _, target, _ = _enable_obsidian(app_harness, tmp_path)

    targets = app_harness.client.get("/api/obsidian/targets")
    status = app_harness.client.get("/api/obsidian/status", params={"page_size": 1})

    assert targets.status_code == 200, targets.text
    assert targets.json()["targets"] == [
        {"target_id": "synthetic-vault", "label": "Synthetic Vault"}
    ]
    assert status.status_code == 200, status.text
    assert status.json()["entries"][0]["logical_path"] == "Home.md"
    assert status.json()["next_cursor"] == "Home.md"
    combined = targets.text + status.text
    assert str(target.vault_root) not in combined
    assert target.managed_subtree.as_posix() not in combined
    assert MANIFEST_DIGEST not in combined
    assert SOURCE_WATERMARK not in combined


def test_render_preview_uses_single_use_server_side_state(app_harness: AppHarness, tmp_path: Path) -> None:
    csrf, _, service = _enable_obsidian(app_harness, tmp_path)
    payload = {"optional_tables": ["library_summary"]}

    missing_csrf = app_harness.client.post(
        "/api/obsidian/render/preview",
        headers={"Origin": EXPECTED_ORIGIN},
        json=payload,
    )
    preview = _post(app_harness, csrf, "/api/obsidian/render/preview", payload)
    assert missing_csrf.status_code == 401
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    assert "expected_state" not in preview.text
    assert MANIFEST_DIGEST not in preview.text

    applied = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/apply",
        {
            "preview_token": token,
            "optional_tables": ["library_summary"],
            "continuation": "render",
        },
    )
    replay = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/apply",
        {
            "preview_token": token,
            "optional_tables": ["library_summary"],
            "continuation": "render",
        },
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["canonical_scientific_write"] is False
    assert replay.status_code == 409
    assert replay.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-PREVIEW-STALE"
    assert service.render_requests == [
        {
            "optional_tables": ["library_summary"],
            "expected_state": {
                "source_watermark": SOURCE_WATERMARK,
                "manifest_digest": MANIFEST_DIGEST,
                "integrity_digest": "d" * 64,
            },
            "discard_managed_edits": False,
        }
    ]


def test_sync_preview_and_apply_are_target_bound_and_preserve_personal_files(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    csrf, target, service = _enable_obsidian(app_harness, tmp_path)
    sentinel = target.vault_root / "personal-sentinel.md"
    sentinel.write_bytes(b"personal\n")

    preview = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id},
    )
    assert preview.status_code == 200, preview.text
    token = preview.json()["preview_token"]
    assert preview.json()["create_count"] == 2
    assert "expected_destination_state" not in preview.text
    assert str(target.vault_root) not in preview.text
    assert target.managed_subtree.as_posix() not in preview.text
    assert MANIFEST_DIGEST not in preview.text

    applied = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/apply",
        {
            "target_id": target.target_id,
            "preview_token": token,
            "continuation": "sync",
        },
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["result"] == "committed"
    assert service.stream_calls == 1
    assert (target.managed_root / "Home.md").read_bytes() == service.files["Home.md"]
    assert sentinel.read_bytes() == b"personal\n"


def test_sync_apply_rejects_destination_change_and_invalidates_preview(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    csrf, target, service = _enable_obsidian(app_harness, tmp_path)
    preview = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id},
    )
    token = preview.json()["preview_token"]
    target.managed_root.mkdir(parents=True)
    (target.managed_root / "unowned.md").write_bytes(b"changed after preview\n")

    stale = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/apply",
        {
            "target_id": target.target_id,
            "preview_token": token,
            "continuation": "sync",
        },
    )
    replay = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/apply",
        {
            "target_id": target.target_id,
            "preview_token": token,
            "continuation": "sync",
        },
    )

    assert stale.status_code == 409
    assert stale.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-STALE-PREVIEW"
    assert replay.status_code == 409
    assert replay.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-PREVIEW-STALE"
    assert service.stream_calls == 0
    assert (target.managed_root / "unowned.md").read_bytes() == b"changed after preview\n"


def test_obsidian_bodies_reject_paths_unknown_fields_and_mismatched_target(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    csrf, target, _ = _enable_obsidian(app_harness, tmp_path)
    unknown = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id, "vault_root": str(target.vault_root)},
    )
    path_target = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": "../vault"},
    )
    preview = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id},
    ).json()
    mismatch = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/apply",
        {
            "target_id": "other-target",
            "preview_token": preview["preview_token"],
            "continuation": "sync",
        },
    )

    assert unknown.status_code == 422
    assert path_target.status_code == 400
    assert mismatch.status_code == 409
    assert mismatch.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-PREVIEW-BINDING"
    assert str(target.vault_root) not in unknown.text + path_target.text + mismatch.text


def test_obsidian_preview_expires_and_operation_busy_does_not_consume_a_fresh_token(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    csrf, _, _ = _enable_obsidian(app_harness, tmp_path)
    runtime = app_harness.client.app.state.runtime
    now = [0.0]
    runtime._monotonic_clock = lambda: now[0]
    expired_token = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/preview",
        {"optional_tables": []},
    ).json()["preview_token"]
    now[0] = 301.0
    expired = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/apply",
        {
            "preview_token": expired_token,
            "optional_tables": [],
            "continuation": "render",
        },
    )
    assert expired.status_code == 409
    assert expired.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-PREVIEW-STALE"

    fresh_token = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/preview",
        {"optional_tables": []},
    ).json()["preview_token"]
    competing = runtime.operation.acquire("intake")
    busy = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/apply",
        {
            "preview_token": fresh_token,
            "optional_tables": [],
            "continuation": "render",
        },
    )
    assert busy.status_code == 409
    assert busy.json()["diagnostic"]["code"] == "RKBAPP-OPERATION-BUSY"
    runtime.operation.complete(competing)
    applied = _post(
        app_harness,
        csrf,
        "/api/obsidian/render/apply",
        {
            "preview_token": fresh_token,
            "optional_tables": [],
            "continuation": "render",
        },
    )
    assert applied.status_code == 200, applied.text


def test_sync_collects_paginated_inventory_and_rejects_mid_page_source_change(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    csrf, target, service = _enable_obsidian(app_harness, tmp_path)
    runtime = app_harness.client.app.state.runtime
    runtime.config = replace(
        runtime.config,
        request_budgets=replace(runtime.config.request_budgets, max_page_size=1),
    )
    complete = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id},
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["source_file_count"] == 2

    service.change_on_cursor = True
    raced = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id},
    )
    assert raced.status_code == 409
    assert raced.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-STALE-PREVIEW"


def test_sync_apply_rejects_source_change_before_streaming(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    csrf, target, service = _enable_obsidian(app_harness, tmp_path)
    preview = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/preview",
        {"target_id": target.target_id},
    ).json()
    service.generation_id = f"gen-{'f' * 64}"
    service.manifest_digest = "e" * 64
    service.source_watermark = "f" * 64

    stale = _post(
        app_harness,
        csrf,
        "/api/obsidian/sync/apply",
        {
            "target_id": target.target_id,
            "preview_token": preview["preview_token"],
            "continuation": "sync",
        },
    )

    assert stale.status_code == 409
    assert stale.json()["diagnostic"]["code"] == "RKBAPP-OBSIDIAN-STALE-PREVIEW"
    assert service.stream_calls == 0
    assert not target.managed_root.exists()


def test_cancelled_render_waits_for_worker_settlement_before_releasing_operation(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    _, _, service = _enable_obsidian(app_harness, tmp_path)
    runtime = app_harness.client.app.state.runtime
    preview = runtime.preview_obsidian_render(
        browser_session_id="browser-session",
        optional_tables=[],
    )
    started = threading.Event()
    release = threading.Event()
    original_render = service.render

    def blocking_render(session, request, *, actor: str):
        started.set()
        assert release.wait(5)
        return original_render(session, request, actor=actor)

    service.render = blocking_render

    async def scenario() -> None:
        task = asyncio.create_task(
            runtime.apply_obsidian_render(
                browser_session_id="browser-session",
                preview_token=preview["preview_token"],
                optional_tables=[],
                continuation="render",
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

    asyncio.run(scenario())
