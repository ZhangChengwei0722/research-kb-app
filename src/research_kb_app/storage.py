from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Callable

from research_kb_app.config import AppConfig
from research_kb.services import WorkspaceStorageInspectionService


class StoragePreflightError(RuntimeError):
    """Raised when an App-managed root is not on an accepted local filesystem."""


@dataclass(frozen=True, slots=True)
class StoragePreflight:
    status: str
    filesystem: str
    roots: tuple[str, ...]


FilesystemProbe = Callable[[Path], str]
WorkspaceRootInspector = Callable[[Path], Iterable[Path]]
_DRIVE_UNKNOWN = 0
_DRIVE_NO_ROOT_DIR = 1
_DRIVE_REMOTE = 4


def preflight_storage(
    config: AppConfig,
    *,
    filesystem_probe: FilesystemProbe | None = None,
    workspace_root_inspector: WorkspaceRootInspector | None = None,
) -> StoragePreflight:
    roots = _managed_roots(config, workspace_root_inspector=workspace_root_inspector)
    probe = filesystem_probe or filesystem_type
    facts: list[str] = []
    for root in roots:
        if _is_remote_path(root):
            raise StoragePreflightError("App storage must be local NTFS")
        filesystem = probe(root)
        facts.append(filesystem)
        if filesystem not in {"NTFS", "not_applicable"}:
            raise StoragePreflightError("App storage must be local NTFS")
    if facts and set(facts) == {"not_applicable"}:
        return StoragePreflight(
            status="not_applicable",
            filesystem="not_applicable",
            roots=tuple(str(root) for root in roots),
        )
    if any(value != "NTFS" for value in facts):
        raise StoragePreflightError("App storage classification is inconsistent")
    return StoragePreflight(status="passed", filesystem="NTFS", roots=tuple(str(root) for root in roots))


def filesystem_type(path: Path) -> str:
    if os.name != "nt":
        return "not_applicable"
    if _is_remote_path(path):
        return "remote"
    volume_path = ctypes.create_unicode_buffer(260)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    query_path = _nearest_existing_ancestor(path)
    if query_path is None:
        return "unknown"
    if not kernel32.GetVolumePathNameW(str(query_path), volume_path, len(volume_path)):
        return "unknown"
    drive_type = kernel32.GetDriveTypeW(volume_path.value)
    if drive_type == _DRIVE_REMOTE:
        return "remote"
    if drive_type in {_DRIVE_UNKNOWN, _DRIVE_NO_ROOT_DIR}:
        return "unknown"
    serial_number = ctypes.c_uint32()
    maximum_component_length = ctypes.c_uint32()
    filesystem_flags = ctypes.c_uint32()
    filesystem_name = ctypes.create_unicode_buffer(260)
    if not kernel32.GetVolumeInformationW(
        volume_path.value,
        None,
        0,
        ctypes.byref(serial_number),
        ctypes.byref(maximum_component_length),
        ctypes.byref(filesystem_flags),
        filesystem_name,
        len(filesystem_name),
    ):
        return "unknown"
    return filesystem_name.value.upper() or "unknown"


def _nearest_existing_ancestor(path: Path) -> Path | None:
    """Resolve volume identity without creating a not-yet-materialized root."""
    candidate = Path(path)
    while True:
        try:
            if candidate.exists():
                return candidate.resolve()
        except OSError:
            return None
        parent = candidate.parent
        if parent == candidate:
            return None
        candidate = parent


def _managed_roots(
    config: AppConfig,
    *,
    workspace_root_inspector: WorkspaceRootInspector | None = None,
) -> tuple[Path, ...]:
    candidates = [
        config.state_root,
        config.log_root,
        *(option.config_path.parent for option in config.workspaces),
        *(target.vault_root for target in config.obsidian_targets),
    ]
    inspector = workspace_root_inspector or _inspect_workspace_roots
    for option in config.workspaces:
        if _is_remote_path(option.config_path):
            continue
        try:
            candidates.extend(inspector(option.config_path))
        except Exception as error:
            raise StoragePreflightError("Core workspace storage inspection failed") from error
    return tuple(
        sorted(
            {_normalize_managed_root(path) for path in candidates},
            key=lambda path: str(path).lower(),
        )
    )


def _normalize_managed_root(path: Path) -> Path:
    candidate = Path(path)
    if _is_remote_path(candidate):
        return candidate
    return candidate.resolve()


def _inspect_workspace_roots(config_path: Path) -> Iterable[Path]:
    roots = WorkspaceStorageInspectionService().inspect(config_path)
    return roots.paths()


def _is_remote_path(path: Path) -> bool:
    return str(path).startswith("\\\\")


__all__ = [
    "StoragePreflight",
    "StoragePreflightError",
    "filesystem_type",
    "preflight_storage",
]
