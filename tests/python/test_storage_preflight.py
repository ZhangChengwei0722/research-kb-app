from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from research_kb_app.storage import StoragePreflightError, preflight_storage


def test_storage_preflight_accepts_ntfs_for_all_managed_roots(app_harness) -> None:
    result = preflight_storage(app_harness.config, filesystem_probe=lambda _path: "NTFS")
    assert result.status == "passed"
    assert result.filesystem == "NTFS"
    assert len(result.roots) == 5
    assert str(app_harness.config.frontend_root) not in result.roots


def test_storage_preflight_does_not_classify_read_only_frontend(app_harness) -> None:
    probed: list[Path] = []

    def probe(path: Path) -> str:
        probed.append(path)
        return "NTFS"

    preflight_storage(app_harness.config, filesystem_probe=probe)

    assert app_harness.config.frontend_root not in probed


@pytest.mark.parametrize("filesystem", ["exFAT", "FAT32", "unknown", "remote"])
def test_storage_preflight_rejects_non_ntfs(filesystem: str, app_harness) -> None:
    with pytest.raises(StoragePreflightError, match="local NTFS"):
        preflight_storage(app_harness.config, filesystem_probe=lambda _path: filesystem)


def test_storage_preflight_rejects_unc_root_before_resolve_or_probe(
    app_harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = app_harness.config
    remote = Path("\\\\server\\share\\research-kb-state")
    config = replace(config, state_root=remote)
    original_resolve = Path.resolve
    probes: list[Path] = []

    def reject_remote_resolve(path: Path, *, strict: bool = False) -> Path:
        if str(path).startswith("\\\\"):
            raise AssertionError("UNC root must be rejected before resolve")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", reject_remote_resolve)

    with pytest.raises(StoragePreflightError, match="local NTFS"):
        preflight_storage(
            config,
            filesystem_probe=lambda path: probes.append(path) or "NTFS",
        )

    assert all(not str(path).startswith("\\\\") for path in probes)


def test_storage_preflight_rejects_unc_workspace_before_core_inspection(
    app_harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = app_harness.config
    remote = Path("\\\\server\\share\\workspace.json")
    option = replace(config.workspaces[0], config_path=remote)
    config = replace(config, workspaces=(option,))
    original_resolve = Path.resolve
    inspections: list[Path] = []

    def reject_remote_resolve(path: Path, *, strict: bool = False) -> Path:
        if str(path).startswith("\\\\"):
            raise AssertionError("UNC workspace must be rejected before resolve")
        return original_resolve(path, strict=strict)

    def reject_workspace_inspection(path: Path) -> tuple[Path, ...]:
        inspections.append(path)
        raise AssertionError("UNC workspace must be rejected before Core inspection")

    monkeypatch.setattr(Path, "resolve", reject_remote_resolve)

    with pytest.raises(StoragePreflightError, match="local NTFS"):
        preflight_storage(
            config,
            filesystem_probe=lambda _path: "NTFS",
            workspace_root_inspector=reject_workspace_inspection,
        )

    assert inspections == []


def test_storage_preflight_rejects_inspector_unc_root_before_resolve_or_probe(
    app_harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = Path("\\\\server\\share\\workspace-state")
    original_resolve = Path.resolve
    probes: list[Path] = []

    def reject_remote_resolve(path: Path, *, strict: bool = False) -> Path:
        if str(path).startswith("\\\\"):
            raise AssertionError("inspected UNC root must be rejected before resolve")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", reject_remote_resolve)

    with pytest.raises(StoragePreflightError, match="local NTFS"):
        preflight_storage(
            app_harness.config,
            filesystem_probe=lambda path: probes.append(path) or "NTFS",
            workspace_root_inspector=lambda _path: (remote,),
        )

    assert all(not str(path).startswith("\\\\") for path in probes)


def test_filesystem_type_queries_nearest_existing_ancestor_without_creating_root(
    tmp_path: Path, monkeypatch
) -> None:
    from research_kb_app import storage

    existing = tmp_path / "existing"
    existing.mkdir()
    missing_root = existing / "state" / "logs"
    calls: list[Path] = []

    class FakeKernel32:
        def GetVolumePathNameW(self, value, buffer, _size):
            calls.append(Path(value))
            buffer.value = "C:\\"
            return 1

        def GetDriveTypeW(self, _volume):
            return 3

        def GetVolumeInformationW(
            self, _volume, _name, _name_size, _serial, _maximum, _flags, buffer, _size
        ):
            buffer.value = "NTFS"
            return 1

    monkeypatch.setattr(storage.os, "name", "nt")
    monkeypatch.setattr(storage.ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32())

    assert storage.filesystem_type(missing_root) == "NTFS"
    assert calls == [existing.resolve()]
    assert not missing_root.exists()


def test_filesystem_type_rejects_mapped_network_volume(tmp_path: Path, monkeypatch) -> None:
    from research_kb_app import storage

    class FakeKernel32:
        def GetVolumePathNameW(self, _value, buffer, _size):
            buffer.value = "Z:\\"
            return 1

        def GetDriveTypeW(self, _volume):
            return 4

    monkeypatch.setattr(storage.os, "name", "nt")
    monkeypatch.setattr(storage.ctypes, "WinDLL", lambda *_args, **_kwargs: FakeKernel32())

    assert storage.filesystem_type(tmp_path) == "remote"


def test_non_windows_storage_preflight_is_explicitly_not_applicable(app_harness) -> None:
    result = preflight_storage(
        app_harness.config,
        filesystem_probe=lambda _path: "not_applicable",
    )

    assert result.status == "not_applicable"
    assert result.filesystem == "not_applicable"
