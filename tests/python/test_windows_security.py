from __future__ import annotations

import os
from pathlib import Path

import pytest

from research_kb_app import windows_security
from research_kb_app.errors import AppOperationError
from research_kb_app.windows_security import (
    WindowsNamedMutexFactory,
    WindowsRootFacts,
    WindowsRootSecurityService,
    apply_protected_current_user_acl,
)


def _facts(
    *,
    secure: bool = True,
    protected: bool = True,
    volume_id: str = "volume-c",
) -> WindowsRootFacts:
    return WindowsRootFacts(volume_id, "NTFS", True, True, secure, protected)


def test_root_security_service_rejects_insecure_parent_before_creation(tmp_path: Path) -> None:
    target = tmp_path / "managed"
    service = WindowsRootSecurityService(facts_probe=lambda _path: _facts(secure=False), acl_setter=lambda _path: None)

    with pytest.raises(AppOperationError) as caught:
        service.secure_create(target, operation_id="operation-1")

    assert caught.value.code == "RKBAPP-ROOT-SECURITY"
    assert not target.exists()


def test_root_security_service_applies_acl_and_reopens_created_root(tmp_path: Path) -> None:
    target = tmp_path / "managed"
    applied = []
    service = WindowsRootSecurityService(
        facts_probe=lambda _path: _facts(),
        acl_setter=lambda path: applied.append(path),
    )

    attestation = service.secure_create(target, operation_id="operation-1")

    assert target.is_dir()
    assert applied == [target]
    assert attestation.filesystem == "NTFS"
    assert attestation.acl_secure is True


@pytest.mark.parametrize(
    ("secure", "protected", "expected"),
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
def test_root_security_service_requires_secure_protected_acl_facts(
    tmp_path: Path, secure: bool, protected: bool, expected: bool
) -> None:
    service = WindowsRootSecurityService(
        facts_probe=lambda _path: _facts(secure=secure, protected=protected),
        acl_setter=lambda _path: None,
    )

    attestation = service.inspect(tmp_path)

    assert attestation.acl_secure is expected
    assert service.capabilities(tmp_path)["accepted"] is expected


def test_root_security_service_rejects_unprotected_parent_before_creation(tmp_path: Path) -> None:
    target = tmp_path / "managed"
    service = WindowsRootSecurityService(
        facts_probe=lambda _path: _facts(secure=True, protected=False),
        acl_setter=lambda _path: None,
    )

    with pytest.raises(AppOperationError) as caught:
        service.secure_create(target, operation_id="operation-1")

    assert caught.value.code == "RKBAPP-ROOT-SECURITY"
    assert not target.exists()


def test_root_security_probe_receives_lexical_path_before_resolution(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    lexical = root / ".." / "root"
    observed: list[Path] = []

    service = WindowsRootSecurityService(
        facts_probe=lambda path: observed.append(path) or _facts(),
        acl_setter=lambda _path: None,
    )

    service.inspect(lexical)

    assert observed == [lexical]


def test_root_security_service_fails_closed_when_windows_identity_resolution_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    original_resolve = Path.resolve

    def deny_strict_resolve(path: Path, *, strict: bool = False) -> Path:
        if strict:
            raise PermissionError("injected access denied")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(windows_security.os, "name", "nt")
    monkeypatch.setattr(Path, "resolve", deny_strict_resolve)

    service = WindowsRootSecurityService()
    attestation = service.inspect(root)

    assert attestation.volume_id == "unknown"
    assert attestation.filesystem == "unknown"
    assert attestation.local is False
    assert attestation.acl_secure is False
    assert service.capabilities(root)["accepted"] is False


def test_root_security_service_removes_only_unchanged_operation_owned_empty_root_on_acl_failure(tmp_path: Path) -> None:
    target = tmp_path / "managed"

    def fail(_path: Path) -> None:
        raise AppOperationError("RKBAPP-ACL-SET", "injected")

    service = WindowsRootSecurityService(facts_probe=lambda _path: _facts(), acl_setter=fail)
    with pytest.raises(AppOperationError):
        service.secure_create(target, operation_id="operation-1")
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL live cell")
def test_live_windows_acl_round_trip_on_operation_owned_temp_root(tmp_path: Path) -> None:
    apply_protected_current_user_acl(tmp_path)
    service = WindowsRootSecurityService()
    parent = service.inspect(tmp_path)
    assert parent.filesystem == "NTFS"
    assert parent.acl_secure is True

    target = tmp_path / "secured-child"
    child = service.secure_create(target, operation_id="operation-live")
    assert child.acl_secure is True
    assert child.volume_id == parent.volume_id


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(AppOperationError("RKBAPP-SID", "injected"), id="app-operation-error"),
        pytest.param(OSError("injected"), id="os-error"),
    ],
)
@pytest.mark.skipif(os.name != "nt", reason="Windows ACL identity failure cell")
def test_live_windows_acl_probe_fails_closed_when_current_sid_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    apply_protected_current_user_acl(tmp_path)
    before = set(tmp_path.iterdir())

    def unavailable() -> str:
        raise failure

    monkeypatch.setattr(windows_security, "current_user_sid", unavailable)

    facts = windows_security.inspect_windows_root(tmp_path)
    capabilities = WindowsRootSecurityService().capabilities(tmp_path)

    assert facts.acl_protected is True
    assert facts.acl_secure is False
    assert capabilities["acl_secure"] is False
    assert capabilities["accepted"] is False
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(AppOperationError("RKBAPP-SID", "injected"), id="app-operation-error"),
        pytest.param(OSError("injected"), id="os-error"),
    ],
)
@pytest.mark.skipif(os.name != "nt", reason="Windows ACL identity failure cell")
def test_live_windows_acl_probe_fails_closed_when_ace_sid_conversion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    apply_protected_current_user_acl(tmp_path)
    current_sid = windows_security.current_user_sid()
    before = set(tmp_path.iterdir())

    def conversion_failure(_sid: object) -> str:
        raise failure

    monkeypatch.setattr(windows_security, "current_user_sid", lambda: current_sid)
    monkeypatch.setattr(windows_security, "_sid_to_string", conversion_failure)

    facts = windows_security.inspect_windows_root(tmp_path)
    capabilities = WindowsRootSecurityService().capabilities(tmp_path)

    assert facts.acl_protected is True
    assert facts.acl_secure is False
    assert capabilities["acl_secure"] is False
    assert capabilities["accepted"] is False
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("failure", [RuntimeError("injected"), TypeError("injected")])
@pytest.mark.skipif(os.name != "nt", reason="Windows ACL identity failure cell")
def test_live_windows_acl_probe_does_not_mask_unexpected_identity_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    apply_protected_current_user_acl(tmp_path)

    def unexpected() -> str:
        raise failure

    monkeypatch.setattr(windows_security, "current_user_sid", unexpected)

    with pytest.raises(type(failure), match="injected"):
        WindowsRootSecurityService().capabilities(tmp_path)


@pytest.mark.parametrize("failure", [RuntimeError("injected"), TypeError("injected")])
@pytest.mark.skipif(os.name != "nt", reason="Windows ACL identity failure cell")
def test_live_windows_acl_probe_does_not_mask_unexpected_ace_conversion_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    apply_protected_current_user_acl(tmp_path)
    current_sid = windows_security.current_user_sid()

    def unexpected(_sid: object) -> str:
        raise failure

    monkeypatch.setattr(windows_security, "current_user_sid", lambda: current_sid)
    monkeypatch.setattr(windows_security, "_sid_to_string", unexpected)

    with pytest.raises(type(failure), match="injected"):
        WindowsRootSecurityService().capabilities(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows mutex live cell")
def test_live_windows_named_mutex_round_trip() -> None:
    with WindowsNamedMutexFactory(timeout_seconds=1)("synthetic-target"):
        pass
