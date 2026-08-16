from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping

from research_kb_app.config import ObsidianTarget
from research_kb_app.errors import AppOperationError


SYNC_MANIFEST_CONTRACT = "research-kb-obsidian-sync-manifest@1.0"
EXPORT_RECEIPT_CONTRACT = "research-kb-obsidian-personal-copy-receipt@1.0"
SYNC_MANIFEST_NAME = ".research-kb-generated-view.json"
MAX_SYNC_FILES = 100_000
MAX_SYNC_BYTES = 2 * 1024 * 1024 * 1024
MAX_PUBLIC_PATHS = 200
_DIGEST_LENGTH = 64
_MANIFEST_KEYS = {
    "contract_version",
    "workspace_option_id",
    "target_id",
    "source_generation_id",
    "source_manifest_digest",
    "source_watermark",
    "files",
    "manifest_payload_digest",
}
_FILE_KEYS = {"logical_path", "content_digest", "byte_count"}
SnapshotStreamer = Callable[[Callable[[str, str, bytes], None]], Mapping[str, Any]]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class SourceFile:
    logical_path: str
    content_digest: str
    byte_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_path": self.logical_path,
            "content_digest": self.content_digest,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True, slots=True)
class SourceProjection:
    generation_id: str
    manifest_digest: str
    source_watermark: str
    files: tuple[SourceFile, ...]

    @property
    def total_bytes(self) -> int:
        return sum(item.byte_count for item in self.files)


@dataclass(frozen=True, slots=True)
class DestinationInspection:
    state: str
    manifest: dict[str, Any] | None
    manifest_digest: str | None
    actual_files: dict[str, Path]
    actual_digests: dict[str, str]
    actual_sizes: dict[str, int]
    edited_paths: tuple[str, ...]
    missing_paths: tuple[str, ...]
    unknown_paths: tuple[str, ...]

    @property
    def expected_state(self) -> str:
        return _json_digest(
            {
                "state": self.state,
                "manifest_digest": self.manifest_digest,
                "actual": [
                    {
                        "logical_path": path,
                        "content_digest": self.actual_digests[path],
                        "byte_count": self.actual_sizes[path],
                    }
                    for path in sorted(self.actual_digests)
                ],
                "missing_paths": list(self.missing_paths),
            }
        )


class ObsidianVaultSyncService:
    def __init__(self, *, clock: Clock | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def preview(
        self,
        *,
        target: ObsidianTarget,
        workspace_option_id: str,
        source: SourceProjection,
    ) -> dict[str, Any]:
        _validate_source(source)
        _require_target_workspace(target, workspace_option_id)
        destination = _inspect_destination(target, workspace_option_id)
        source_by_path = {item.logical_path: item for item in source.files}
        saved = _manifest_file_index(destination.manifest)
        create = sorted(set(source_by_path) - set(saved))
        update = sorted(
            path
            for path in set(source_by_path) & set(saved)
            if source_by_path[path].content_digest != saved[path]["content_digest"]
            or source_by_path[path].byte_count != saved[path]["byte_count"]
        )
        no_change = sorted(
            path
            for path in set(source_by_path) & set(saved)
            if path not in update
        )
        remove = sorted(set(saved) - set(source_by_path))
        conflicts = sorted(
            set(destination.edited_paths)
            | set(destination.missing_paths)
            | set(destination.unknown_paths)
        )
        return {
            "target_id": target.target_id,
            "target_label": target.label,
            "managed_logical_root": target.managed_subtree.as_posix(),
            "source_generation_id": source.generation_id,
            "source_manifest_digest": source.manifest_digest,
            "source_watermark": source.source_watermark,
            "source_file_count": len(source.files),
            "source_byte_count": source.total_bytes,
            "destination_state": destination.state,
            "create_count": len(create),
            "update_count": len(update),
            "no_change_count": len(no_change),
            "remove_count": len(remove),
            "edited_count": len(destination.edited_paths),
            "missing_count": len(destination.missing_paths),
            "unknown_count": len(destination.unknown_paths),
            "collision_count": 1 if destination.state == "collision" else 0,
            "changed_paths": (create + update + remove)[:MAX_PUBLIC_PATHS],
            "changed_paths_truncated": len(create) + len(update) + len(remove) > MAX_PUBLIC_PATHS,
            "conflict_paths": conflicts[:MAX_PUBLIC_PATHS],
            "conflict_paths_truncated": len(conflicts) > MAX_PUBLIC_PATHS,
            "expected_destination_state": destination.expected_state,
        }

    def apply(
        self,
        *,
        target: ObsidianTarget,
        workspace_option_id: str,
        source: SourceProjection,
        expected_destination_state: str,
        continuation: str,
        stream_snapshot: SnapshotStreamer,
    ) -> dict[str, Any]:
        _validate_source(source)
        _require_target_workspace(target, workspace_option_id)
        if continuation not in {
            "sync",
            "discard_managed_edits",
            "export_personal_copy_then_sync",
        }:
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-CONTINUATION",
                "Obsidian sync continuation is invalid",
                status_code=400,
            )
        destination = _inspect_destination(target, workspace_option_id)
        if destination.expected_state != expected_destination_state:
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-STALE-PREVIEW",
                "Obsidian destination changed after preview",
            )
        if destination.state == "collision":
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-COLLISION",
                "Configured managed subtree contains unowned content",
            )
        has_conflicts = bool(
            destination.edited_paths or destination.missing_paths or destination.unknown_paths
        )
        if has_conflicts and continuation == "sync":
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-MANAGED-EDIT",
                "Managed Obsidian files changed after the previous sync",
            )
        if not has_conflicts and continuation != "sync":
            raise AppOperationError(
                "RKBAPP-OBSIDIAN-CONTINUATION",
                "Managed-edit continuation is not applicable",
            )
        if (
            destination.state == "current"
            and destination.manifest is not None
            and destination.manifest["source_manifest_digest"] == source.manifest_digest
            and destination.manifest["files"] == [item.to_dict() for item in source.files]
        ):
            return {
                "result": "no_change",
                "target_id": target.target_id,
                "source_generation_id": source.generation_id,
                "source_manifest_digest": source.manifest_digest,
                "destination_manifest_digest": destination.manifest_digest,
                "file_count": len(source.files),
                "byte_count": source.total_bytes,
                "continuation": continuation,
                "personal_copy": None,
                "canonical_scientific_write": False,
            }
        export = None
        if continuation == "export_personal_copy_then_sync":
            export = _export_personal_copy(target, destination, clock=self.clock)

        parent = target.managed_root.parent
        _ensure_confined_directory(parent, target.vault_root)
        operation_id = uuid.uuid4().hex
        staging = parent / f".rkb-obsidian-stage-{operation_id}"
        backup = parent / f".rkb-obsidian-backup-{operation_id}"
        _require_absent_owned_path(staging, parent, ".rkb-obsidian-stage-")
        _require_absent_owned_path(backup, parent, ".rkb-obsidian-backup-")
        staging.mkdir()
        published = False
        prior_moved = False
        source_index = {item.logical_path: item for item in source.files}
        seen: set[str] = set()

        def sink(logical_path: str, content_digest: str, content: bytes) -> None:
            expected = source_index.get(logical_path)
            if (
                expected is None
                or logical_path in seen
                or content_digest != expected.content_digest
                or len(content) != expected.byte_count
                or _sha256_bytes(content) != expected.content_digest
            ):
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-SOURCE-STREAM",
                    "Core generated-view snapshot does not match its manifest",
                )
            destination_path = staging / _logical_path(logical_path)
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            _write_create_only(destination_path, content)
            seen.add(logical_path)

        try:
            streamed = dict(stream_snapshot(sink))
            if (
                seen != set(source_index)
                or streamed.get("manifest_digest") != source.manifest_digest
                or streamed.get("generation_id") != source.generation_id
                or streamed.get("file_count") != len(source.files)
                or streamed.get("byte_count") != source.total_bytes
            ):
                raise AppOperationError(
                    "RKBAPP-OBSIDIAN-SOURCE-STREAM",
                    "Core generated-view snapshot is incomplete",
                )
            manifest = _build_manifest(target, workspace_option_id, source)
            _write_create_only(staging / SYNC_MANIFEST_NAME, _serialize_json(manifest))
            _verify_managed_tree(staging, manifest)

            if target.managed_root.exists():
                os.replace(target.managed_root, backup)
                prior_moved = True
            os.replace(staging, target.managed_root)
            published = True
            _verify_managed_tree(target.managed_root, manifest)
            if prior_moved:
                _remove_owned_tree(backup, parent, ".rkb-obsidian-backup-")
            return {
                "result": "committed",
                "target_id": target.target_id,
                "source_generation_id": source.generation_id,
                "source_manifest_digest": source.manifest_digest,
                "destination_manifest_digest": manifest["manifest_payload_digest"],
                "file_count": len(source.files),
                "byte_count": source.total_bytes,
                "continuation": continuation,
                "personal_copy": export,
                "canonical_scientific_write": False,
            }
        except Exception:
            if published and prior_moved and backup.exists():
                try:
                    current = _inspect_destination(target, workspace_option_id)
                    if current.state == "current" and current.manifest_digest == _build_manifest(
                        target,
                        workspace_option_id,
                        source,
                    )["manifest_payload_digest"]:
                        _remove_managed_publication(target.managed_root, parent)
                        os.replace(backup, target.managed_root)
                except Exception as recovery_error:
                    raise AppOperationError(
                        "RKBAPP-OBSIDIAN-RECOVERY-REQUIRED",
                        "Obsidian managed subtree requires recovery",
                    ) from recovery_error
            elif prior_moved and backup.exists() and not target.managed_root.exists():
                os.replace(backup, target.managed_root)
            elif published and not prior_moved and target.managed_root.exists():
                try:
                    current = _inspect_destination(target, workspace_option_id)
                    expected_manifest = _build_manifest(target, workspace_option_id, source)
                    if (
                        current.state == "current"
                        and current.manifest_digest == expected_manifest["manifest_payload_digest"]
                    ):
                        _remove_managed_publication(target.managed_root, parent)
                    else:
                        raise AppOperationError(
                            "RKBAPP-OBSIDIAN-RECOVERY-REQUIRED",
                            "Obsidian managed subtree requires recovery",
                        )
                except AppOperationError:
                    raise
                except Exception as recovery_error:
                    raise AppOperationError(
                        "RKBAPP-OBSIDIAN-RECOVERY-REQUIRED",
                        "Obsidian managed subtree requires recovery",
                    ) from recovery_error
            raise
        finally:
            if staging.exists():
                _remove_owned_tree(staging, parent, ".rkb-obsidian-stage-")


def source_projection_from_status(status: Mapping[str, Any], entries: Iterable[Mapping[str, Any]]) -> SourceProjection:
    if (
        status.get("projection_state") != "ready"
        or status.get("integrity_state") != "intact"
        or status.get("stale_count") != 0
        or not isinstance(status.get("generation_id"), str)
        or not _digest(status.get("manifest_digest"))
        or not _digest(status.get("source_watermark"))
    ):
        raise AppOperationError(
            "RKBAPP-OBSIDIAN-SOURCE-NOT-CURRENT",
            "Generated Obsidian views must be current and intact before synchronization",
        )
    files = tuple(
        SourceFile(
            logical_path=_logical_path(item.get("logical_path")).as_posix(),
            content_digest=_required_digest(item.get("content_digest")),
            byte_count=_byte_count(item.get("byte_count")),
        )
        for item in entries
    )
    projection = SourceProjection(
        generation_id=status["generation_id"],
        manifest_digest=status["manifest_digest"],
        source_watermark=status["source_watermark"],
        files=tuple(sorted(files, key=lambda item: item.logical_path)),
    )
    if status.get("file_count") != len(projection.files):
        raise AppOperationError(
            "RKBAPP-OBSIDIAN-SOURCE-PAGE",
            "Generated Obsidian view inventory is incomplete",
        )
    _validate_source(projection)
    return projection


def _validate_source(source: SourceProjection) -> None:
    if (
        not isinstance(source.generation_id, str)
        or not source.generation_id.startswith("gen-")
        or not _digest(source.generation_id.removeprefix("gen-"))
    ):
        raise AppOperationError("RKBAPP-OBSIDIAN-SOURCE", "Generated-view generation ID is invalid")
    _required_digest(source.manifest_digest)
    _required_digest(source.source_watermark)
    if not source.files or len(source.files) > MAX_SYNC_FILES:
        raise AppOperationError("RKBAPP-OBSIDIAN-SOURCE", "Generated-view file count is invalid")
    for item in source.files:
        _logical_path(item.logical_path)
        _required_digest(item.content_digest)
        _byte_count(item.byte_count)
    paths = [item.logical_path for item in source.files]
    if paths != sorted(set(paths)):
        raise AppOperationError("RKBAPP-OBSIDIAN-SOURCE", "Generated-view paths are not canonical")
    if source.total_bytes > MAX_SYNC_BYTES:
        raise AppOperationError("RKBAPP-OBSIDIAN-SOURCE", "Generated-view snapshot exceeds the byte budget")


def _inspect_destination(target: ObsidianTarget, workspace_option_id: str) -> DestinationInspection:
    _require_target_workspace(target, workspace_option_id)
    root = target.managed_root
    _require_safe_components(root, target.vault_root)
    if not root.exists():
        return DestinationInspection("missing", None, None, {}, {}, {}, (), (), ())
    if not root.is_dir() or _is_unsafe_link(root):
        raise AppOperationError("RKBAPP-OBSIDIAN-TARGET", "Managed Obsidian target is unsafe")
    actual_files: dict[str, Path] = {}
    for path in root.rglob("*"):
        _require_safe_components(path, target.vault_root)
        if path.is_dir():
            continue
        if not path.is_file():
            raise AppOperationError("RKBAPP-OBSIDIAN-TARGET", "Managed Obsidian target has an unsafe entry")
        logical_path = path.relative_to(root).as_posix()
        if logical_path != SYNC_MANIFEST_NAME:
            _logical_path(logical_path)
            actual_files[logical_path] = path
    if len(actual_files) > MAX_SYNC_FILES:
        raise AppOperationError("RKBAPP-OBSIDIAN-TARGET", "Managed Obsidian target exceeds the file budget")
    manifest_path = root / SYNC_MANIFEST_NAME
    if not actual_files and not manifest_path.exists():
        return DestinationInspection("empty", None, None, {}, {}, {}, (), (), ())
    if not manifest_path.is_file() or _is_unsafe_link(manifest_path):
        return _collision(actual_files)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _validate_manifest(manifest, target=target, workspace_option_id=workspace_option_id)
    except (OSError, UnicodeError, json.JSONDecodeError, AppOperationError):
        return _collision(actual_files)
    expected = _manifest_file_index(manifest)
    actual_digests = {path: _sha256_file(value) for path, value in sorted(actual_files.items())}
    actual_sizes = {path: value.stat().st_size for path, value in sorted(actual_files.items())}
    missing = tuple(sorted(set(expected) - set(actual_files)))
    unknown = tuple(sorted(set(actual_files) - set(expected)))
    edited = tuple(
        sorted(
            path
            for path in set(expected) & set(actual_files)
            if actual_digests[path] != expected[path]["content_digest"]
            or actual_sizes[path] != expected[path]["byte_count"]
        )
    )
    state = "edited" if edited or missing or unknown else "current"
    return DestinationInspection(
        state,
        manifest,
        manifest["manifest_payload_digest"],
        actual_files,
        actual_digests,
        actual_sizes,
        edited,
        missing,
        unknown,
    )


def _collision(actual_files: dict[str, Path]) -> DestinationInspection:
    digests = {path: _sha256_file(value) for path, value in sorted(actual_files.items())}
    sizes = {path: value.stat().st_size for path, value in sorted(actual_files.items())}
    return DestinationInspection(
        "collision",
        None,
        None,
        actual_files,
        digests,
        sizes,
        (),
        (),
        tuple(sorted(actual_files)),
    )


def _build_manifest(
    target: ObsidianTarget,
    workspace_option_id: str,
    source: SourceProjection,
) -> dict[str, Any]:
    payload = {
        "contract_version": SYNC_MANIFEST_CONTRACT,
        "workspace_option_id": workspace_option_id,
        "target_id": target.target_id,
        "source_generation_id": source.generation_id,
        "source_manifest_digest": source.manifest_digest,
        "source_watermark": source.source_watermark,
        "files": [item.to_dict() for item in source.files],
    }
    return {**payload, "manifest_payload_digest": _json_digest(payload)}


def _validate_manifest(
    manifest: Any,
    *,
    target: ObsidianTarget,
    workspace_option_id: str,
) -> None:
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise AppOperationError("RKBAPP-OBSIDIAN-MANIFEST", "Obsidian sync manifest is invalid")
    if (
        manifest["contract_version"] != SYNC_MANIFEST_CONTRACT
        or manifest["workspace_option_id"] != workspace_option_id
        or manifest["target_id"] != target.target_id
        or not isinstance(manifest["source_generation_id"], str)
        or not _digest(manifest["source_manifest_digest"])
        or not _digest(manifest["source_watermark"])
        or not isinstance(manifest["files"], list)
    ):
        raise AppOperationError("RKBAPP-OBSIDIAN-MANIFEST", "Obsidian sync manifest binding is invalid")
    files = []
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != _FILE_KEYS:
            raise AppOperationError("RKBAPP-OBSIDIAN-MANIFEST", "Obsidian sync file entry is invalid")
        files.append(
            {
                "logical_path": _logical_path(item["logical_path"]).as_posix(),
                "content_digest": _required_digest(item["content_digest"]),
                "byte_count": _byte_count(item["byte_count"]),
            }
        )
    if files != sorted(files, key=lambda item: item["logical_path"]) or len(files) != len(
        {item["logical_path"] for item in files}
    ):
        raise AppOperationError("RKBAPP-OBSIDIAN-MANIFEST", "Obsidian sync file order is invalid")
    payload = {key: value for key, value in manifest.items() if key != "manifest_payload_digest"}
    if manifest["manifest_payload_digest"] != _json_digest(payload):
        raise AppOperationError("RKBAPP-OBSIDIAN-MANIFEST", "Obsidian sync manifest digest is invalid")


def _manifest_file_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {} if manifest is None else {item["logical_path"]: item for item in manifest["files"]}


def _verify_managed_tree(root: Path, manifest: dict[str, Any]) -> None:
    expected = _manifest_file_index(manifest)
    actual: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        logical = path.relative_to(root).as_posix()
        if logical != SYNC_MANIFEST_NAME:
            actual[logical] = path
    if set(actual) != set(expected):
        raise AppOperationError("RKBAPP-OBSIDIAN-VERIFY", "Obsidian synchronized file set is incomplete")
    if any(
        path.stat().st_size != expected[logical]["byte_count"]
        or _sha256_file(path) != expected[logical]["content_digest"]
        for logical, path in actual.items()
    ):
        raise AppOperationError("RKBAPP-OBSIDIAN-VERIFY", "Obsidian synchronized content is invalid")
    stored = json.loads((root / SYNC_MANIFEST_NAME).read_text(encoding="utf-8"))
    if stored != manifest:
        raise AppOperationError("RKBAPP-OBSIDIAN-VERIFY", "Obsidian sync manifest was not published")


def _export_personal_copy(
    target: ObsidianTarget,
    inspection: DestinationInspection,
    *,
    clock: Clock,
) -> dict[str, Any]:
    personal_root = target.personal_notes_root
    _ensure_confined_directory(personal_root, target.vault_root)
    export_id = f"managed-edits-{uuid.uuid4().hex}"
    export_root = personal_root / export_id
    export_root.mkdir()
    paths = sorted(
        (set(inspection.edited_paths) | set(inspection.unknown_paths))
        & set(inspection.actual_files)
    )
    files = []
    for logical_path in paths:
        source = inspection.actual_files[logical_path]
        target_path = export_root / _logical_path(logical_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _write_create_only(target_path, source.read_bytes())
        digest = _sha256_file(target_path)
        if digest != inspection.actual_digests[logical_path]:
            raise AppOperationError("RKBAPP-OBSIDIAN-EXPORT", "Personal-copy verification failed")
        files.append(
            {
                "logical_path": logical_path,
                "content_digest": digest,
                "byte_count": target_path.stat().st_size,
            }
        )
    created_at = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt_payload = {
        "contract_version": EXPORT_RECEIPT_CONTRACT,
        "export_id": export_id,
        "target_id": target.target_id,
        "created_at": created_at,
        "files": files,
        "missing_paths": list(inspection.missing_paths),
    }
    receipt = {**receipt_payload, "receipt_digest": _json_digest(receipt_payload)}
    _write_create_only(export_root / ".research-kb-personal-copy.json", _serialize_json(receipt))
    return {
        "export_id": export_id,
        "file_count": len(files),
        "missing_count": len(inspection.missing_paths),
        "receipt_digest": receipt["receipt_digest"],
    }


def _require_target_workspace(target: ObsidianTarget, workspace_option_id: str) -> None:
    if target.workspace_option_id != workspace_option_id:
        raise AppOperationError(
            "RKBAPP-OBSIDIAN-TARGET-WORKSPACE",
            "Obsidian target does not belong to the selected workspace",
        )


def _logical_path(value: Any) -> Path:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value or ":" in value:
        raise AppOperationError("RKBAPP-OBSIDIAN-PATH", "Generated-view logical path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.suffix != ".md":
        raise AppOperationError("RKBAPP-OBSIDIAN-PATH", "Generated-view logical path is invalid")
    return Path(*path.parts)


def _required_digest(value: Any) -> str:
    if not _digest(value):
        raise AppOperationError("RKBAPP-OBSIDIAN-DIGEST", "Obsidian digest is invalid")
    return value


def _digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _byte_count(value: Any) -> int:
    if type(value) is not int or value < 0 or value > MAX_SYNC_BYTES:
        raise AppOperationError("RKBAPP-OBSIDIAN-SIZE", "Obsidian file byte count is invalid")
    return value


def _serialize_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _json_digest(value: Mapping[str, Any]) -> str:
    return _sha256_bytes(_serialize_json(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_create_only(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _ensure_confined_directory(path: Path, vault_root: Path) -> None:
    if not path.resolve().is_relative_to(vault_root):
        raise AppOperationError("RKBAPP-OBSIDIAN-TARGET", "Obsidian target escapes its vault")
    path.mkdir(parents=True, exist_ok=True)
    _require_safe_components(path, vault_root)


def _require_safe_components(path: Path, vault_root: Path) -> None:
    resolved_vault = vault_root.resolve()
    if not path.resolve().is_relative_to(resolved_vault):
        raise AppOperationError("RKBAPP-OBSIDIAN-TARGET", "Obsidian target escapes its vault")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            raise AppOperationError("RKBAPP-OBSIDIAN-TARGET", "Obsidian target traverses an unsafe link")


def _is_unsafe_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _require_absent_owned_path(path: Path, parent: Path, prefix: str) -> None:
    if path.parent != parent or not path.name.startswith(prefix) or path.exists():
        raise AppOperationError("RKBAPP-OBSIDIAN-TEMP", "Obsidian operation path is unavailable")


def _remove_owned_tree(path: Path, parent: Path, prefix: str) -> None:
    if path.parent != parent or not path.name.startswith(prefix) or _is_unsafe_link(path):
        raise AppOperationError("RKBAPP-OBSIDIAN-TEMP", "Refusing to remove an unowned path")
    shutil.rmtree(path)


def _remove_managed_publication(path: Path, parent: Path) -> None:
    if path.parent != parent or _is_unsafe_link(path):
        raise AppOperationError("RKBAPP-OBSIDIAN-RECOVERY-REQUIRED", "Managed publication is unsafe")
    shutil.rmtree(path)


__all__ = [
    "DestinationInspection",
    "MAX_PUBLIC_PATHS",
    "MAX_SYNC_BYTES",
    "MAX_SYNC_FILES",
    "ObsidianVaultSyncService",
    "SourceFile",
    "SourceProjection",
    "source_projection_from_status",
]
