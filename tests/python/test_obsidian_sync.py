from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

from research_kb_app.config import ObsidianTarget
from research_kb_app.errors import AppOperationError
from research_kb_app.obsidian_sync import (
    ObsidianVaultSyncService,
    SourceFile,
    SourceProjection,
)


FIXED_TIME = datetime(2026, 8, 4, 8, 0, 0, tzinfo=timezone.utc)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _target(tmp_path: Path) -> ObsidianTarget:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    return ObsidianTarget(
        target_id="synthetic-vault",
        label="Synthetic Vault",
        workspace_option_id="p2-small",
        vault_root=vault,
        managed_subtree=PurePosixPath("Research KB/Generated"),
        personal_notes_subtree=PurePosixPath("Research KB/Personal"),
    )


def _source(*, revision: int = 1) -> tuple[SourceProjection, dict[str, bytes]]:
    files = {
        "Home.md": f"# Synthetic Home {revision}\n".encode(),
        "Papers/_index.md": b"# Synthetic Papers\n",
    }
    projection = SourceProjection(
        generation_id=f"gen-{'a' * 63}{revision}",
        manifest_digest=("b" if revision == 1 else "c") * 64,
        source_watermark=("d" if revision == 1 else "e") * 64,
        files=tuple(
            SourceFile(path, _digest(content), len(content))
            for path, content in sorted(files.items())
        ),
    )
    return projection, files


def _stream(source: SourceProjection, files: dict[str, bytes], calls: list[str] | None = None):
    def stream(sink):
        if calls is not None:
            calls.append(source.manifest_digest)
        for item in source.files:
            sink(item.logical_path, item.content_digest, files[item.logical_path])
        return {
            "manifest_digest": source.manifest_digest,
            "generation_id": source.generation_id,
            "file_count": len(source.files),
            "byte_count": source.total_bytes,
        }

    return stream


def _apply(service, target, source, files, *, continuation="sync"):
    preview = service.preview(
        target=target,
        workspace_option_id="p2-small",
        source=source,
    )
    return service.apply(
        target=target,
        workspace_option_id="p2-small",
        source=source,
        expected_destination_state=preview["expected_destination_state"],
        continuation=continuation,
        stream_snapshot=_stream(source, files),
    )


def test_initial_sync_and_exact_repeat_are_confined_and_idempotent(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    source, files = _source()
    personal_sentinel = target.vault_root / "personal-sentinel.md"
    personal_sentinel.write_bytes(b"personal\n")

    preview = service.preview(target=target, workspace_option_id="p2-small", source=source)
    assert preview["destination_state"] == "missing"
    assert preview["create_count"] == 2
    first = _apply(service, target, source, files)
    assert first["result"] == "committed"
    assert first["canonical_scientific_write"] is False
    assert (target.managed_root / "Home.md").read_bytes() == files["Home.md"]
    assert personal_sentinel.read_bytes() == b"personal\n"

    calls: list[str] = []
    repeat_preview = service.preview(target=target, workspace_option_id="p2-small", source=source)
    repeat = service.apply(
        target=target,
        workspace_option_id="p2-small",
        source=source,
        expected_destination_state=repeat_preview["expected_destination_state"],
        continuation="sync",
        stream_snapshot=_stream(source, files, calls),
    )
    assert repeat["result"] == "no_change"
    assert calls == []
    assert personal_sentinel.read_bytes() == b"personal\n"


def test_source_update_changes_the_expected_files_and_removes_old_content(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    first, first_files = _source()
    _apply(service, target, first, first_files)
    second, second_files = _source(revision=2)

    preview = service.preview(target=target, workspace_option_id="p2-small", source=second)
    assert preview["update_count"] == 1
    assert preview["no_change_count"] == 1
    result = _apply(service, target, second, second_files)
    assert result["result"] == "committed"
    assert (target.managed_root / "Home.md").read_bytes() == second_files["Home.md"]


def test_managed_edit_blocks_ordinary_sync_and_user_discard_restores_source(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    source, files = _source()
    _apply(service, target, source, files)
    (target.managed_root / "Home.md").write_bytes(b"user edit\n")

    preview = service.preview(target=target, workspace_option_id="p2-small", source=source)
    assert preview["destination_state"] == "edited"
    assert preview["edited_count"] == 1
    with pytest.raises(AppOperationError, match="changed"):
        service.apply(
            target=target,
            workspace_option_id="p2-small",
            source=source,
            expected_destination_state=preview["expected_destination_state"],
            continuation="sync",
            stream_snapshot=_stream(source, files),
        )

    result = service.apply(
        target=target,
        workspace_option_id="p2-small",
        source=source,
        expected_destination_state=preview["expected_destination_state"],
        continuation="discard_managed_edits",
        stream_snapshot=_stream(source, files),
    )
    assert result["result"] == "committed"
    assert (target.managed_root / "Home.md").read_bytes() == files["Home.md"]


def test_export_personal_copy_preserves_edited_and_unknown_bytes_before_sync(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    source, files = _source()
    _apply(service, target, source, files)
    edited = b"user-owned edit\n"
    unknown = b"user-owned unknown\n"
    (target.managed_root / "Home.md").write_bytes(edited)
    (target.managed_root / "Notes.md").write_bytes(unknown)
    personal_sentinel = target.vault_root / "outside-personal.md"
    personal_sentinel.write_bytes(b"untouched\n")

    preview = service.preview(target=target, workspace_option_id="p2-small", source=source)
    result = service.apply(
        target=target,
        workspace_option_id="p2-small",
        source=source,
        expected_destination_state=preview["expected_destination_state"],
        continuation="export_personal_copy_then_sync",
        stream_snapshot=_stream(source, files),
    )

    export = result["personal_copy"]
    export_root = target.personal_notes_root / export["export_id"]
    assert (export_root / "Home.md").read_bytes() == edited
    assert (export_root / "Notes.md").read_bytes() == unknown
    assert (export_root / ".research-kb-personal-copy.json").is_file()
    assert personal_sentinel.read_bytes() == b"untouched\n"
    assert (target.managed_root / "Home.md").read_bytes() == files["Home.md"]
    assert not (target.managed_root / "Notes.md").exists()


def test_unowned_first_sync_collision_cannot_be_discarded_or_exported(tmp_path: Path) -> None:
    target = _target(tmp_path)
    target.managed_root.mkdir(parents=True)
    (target.managed_root / "Personal.md").write_bytes(b"unowned\n")
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    source, files = _source()
    preview = service.preview(target=target, workspace_option_id="p2-small", source=source)
    assert preview["destination_state"] == "collision"

    for continuation in ("sync", "discard_managed_edits", "export_personal_copy_then_sync"):
        with pytest.raises(AppOperationError, match="unowned"):
            service.apply(
                target=target,
                workspace_option_id="p2-small",
                source=source,
                expected_destination_state=preview["expected_destination_state"],
                continuation=continuation,
                stream_snapshot=_stream(source, files),
            )
    assert (target.managed_root / "Personal.md").read_bytes() == b"unowned\n"


def test_destination_change_after_preview_rejects_apply_without_streaming(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    source, files = _source()
    _apply(service, target, source, files)
    preview = service.preview(target=target, workspace_option_id="p2-small", source=source)
    (target.managed_root / "Home.md").write_bytes(b"changed after preview\n")
    calls: list[str] = []

    with pytest.raises(AppOperationError, match="after preview"):
        service.apply(
            target=target,
            workspace_option_id="p2-small",
            source=source,
            expected_destination_state=preview["expected_destination_state"],
            continuation="sync",
            stream_snapshot=_stream(source, files, calls),
        )
    assert calls == []


def test_source_stream_mismatch_leaves_previous_managed_tree_intact(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    first, first_files = _source()
    _apply(service, target, first, first_files)
    second, second_files = _source(revision=2)
    preview = service.preview(target=target, workspace_option_id="p2-small", source=second)

    def incomplete_stream(sink):
        item = second.files[0]
        sink(item.logical_path, item.content_digest, second_files[item.logical_path])
        return {
            "manifest_digest": second.manifest_digest,
            "generation_id": second.generation_id,
            "file_count": 1,
            "byte_count": item.byte_count,
        }

    with pytest.raises(AppOperationError, match="incomplete"):
        service.apply(
            target=target,
            workspace_option_id="p2-small",
            source=second,
            expected_destination_state=preview["expected_destination_state"],
            continuation="sync",
            stream_snapshot=incomplete_stream,
        )
    assert (target.managed_root / "Home.md").read_bytes() == first_files["Home.md"]
    assert not list(target.managed_root.parent.glob(".rkb-obsidian-stage-*"))
    assert not list(target.managed_root.parent.glob(".rkb-obsidian-backup-*"))


def test_source_projection_rejects_unsafe_logical_path(tmp_path: Path) -> None:
    target = _target(tmp_path)
    service = ObsidianVaultSyncService(clock=lambda: FIXED_TIME)
    content = b"unsafe\n"
    source = SourceProjection(
        generation_id=f"gen-{'f' * 64}",
        manifest_digest="a" * 64,
        source_watermark="b" * 64,
        files=(SourceFile("../escape.md", _digest(content), len(content)),),
    )
    with pytest.raises(AppOperationError, match="logical path"):
        service.preview(target=target, workspace_option_id="p2-small", source=source)
