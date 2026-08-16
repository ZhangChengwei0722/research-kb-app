from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from ctypes import wintypes
from research_kb.workspace_materialization import (
    ROOT_SECURITY_POLICY,
    RootSecurityAttestation,
    path_identity,
)

from research_kb_app.errors import AppOperationError


MAX_SUPPORTED_PATH_UTF16_UNITS = 240
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_DRIVE_UNKNOWN = 0
_DRIVE_NO_ROOT_DIR = 1
_DRIVE_REMOTE = 4
_SE_FILE_OBJECT = 1
_OWNER_SECURITY_INFORMATION = 0x00000001
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_ACCESS_ALLOWED_ACE_TYPE = 0x00
_ACCESS_DENIED_ACE_TYPE = 0x01
_INHERIT_ONLY_ACE = 0x08
_ACL_SIZE_INFORMATION_CLASS = 2
_GENERIC_ALL = 0x10000000
_FILE_ALL_ACCESS = 0x001F01FF
_WRITE_MASK = (
    0x40000000
    | _GENERIC_ALL
    | 0x00010000
    | 0x00040000
    | 0x00080000
    | 0x00000002
    | 0x00000004
    | 0x00000010
    | 0x00000040
    | 0x00000100
)
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"


class ACE_HEADER(ctypes.Structure):
    _fields_ = [("AceType", wintypes.BYTE), ("AceFlags", wintypes.BYTE), ("AceSize", wintypes.WORD)]


class ACCESS_ALLOWED_ACE(ctypes.Structure):
    _fields_ = [("Header", ACE_HEADER), ("Mask", wintypes.DWORD), ("SidStart", wintypes.DWORD)]


class ACL_SIZE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("AceCount", wintypes.DWORD),
        ("AclBytesInUse", wintypes.DWORD),
        ("AclBytesFree", wintypes.DWORD),
    ]


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_USER_VALUE(ctypes.Structure):
    _fields_ = [("User", SID_AND_ATTRIBUTES)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


@dataclass(frozen=True, slots=True)
class WindowsRootFacts:
    volume_id: str
    filesystem: str
    local: bool
    reparse_free: bool
    acl_secure: bool
    acl_protected: bool


FactsProbe = Callable[[Path], WindowsRootFacts]
AclSetter = Callable[[Path], None]


class WindowsRootSecurityService:
    def __init__(
        self,
        *,
        facts_probe: FactsProbe | None = None,
        acl_setter: AclSetter | None = None,
    ) -> None:
        self._facts_probe = facts_probe or inspect_windows_root
        self._acl_setter = acl_setter or apply_protected_current_user_acl

    def inspect(self, path: Path) -> RootSecurityAttestation:
        candidate = _validated_existing_directory(path)
        facts = self._facts_probe(candidate)
        return RootSecurityAttestation(
            path_identity=path_identity(candidate),
            volume_id=facts.volume_id,
            filesystem=facts.filesystem,
            local=facts.local,
            reparse_free=facts.reparse_free,
            acl_policy_id=ROOT_SECURITY_POLICY,
            acl_secure=facts.acl_secure and facts.acl_protected,
        )

    def secure_create(self, path: Path, *, operation_id: str) -> RootSecurityAttestation:
        candidate = _validated_absolute_path(path, allow_missing=True)
        if not operation_id or candidate.exists() or os.path.lexists(candidate):
            raise AppOperationError("RKBAPP-ROOT-CREATE", "Secure root target is unavailable")
        parent = _validated_existing_directory(candidate.parent)
        parent_attestation = self.inspect(parent)
        _require_acceptable(parent_attestation)
        candidate.mkdir()
        created_stat = candidate.stat(follow_symlinks=False)
        try:
            self._acl_setter(candidate)
            attestation = self.verify(candidate)
            _require_acceptable(attestation)
            if attestation.volume_id != parent_attestation.volume_id:
                raise AppOperationError("RKBAPP-ROOT-VOLUME", "Secure root changed volume during creation")
            return attestation
        except Exception:
            try:
                current = candidate.stat(follow_symlinks=False)
                if current.st_dev == created_stat.st_dev and current.st_ino == created_stat.st_ino:
                    candidate.rmdir()
            except OSError:
                pass
            raise

    def verify(self, path: Path) -> RootSecurityAttestation:
        return self.inspect(path)

    def capabilities(self, path: Path) -> dict[str, Any]:
        attestation = self.inspect(path)
        return {
            "filesystem": attestation.filesystem,
            "local": attestation.local,
            "reparse_free": attestation.reparse_free,
            "acl_policy_id": attestation.acl_policy_id,
            "acl_secure": attestation.acl_secure,
            "accepted": _acceptable(attestation),
        }


class WindowsNamedMutexFactory:
    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("mutex timeout must be positive")
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def __call__(self, key: str) -> Iterator[object]:
        if os.name != "nt":
            raise AppOperationError("RKBAPP-MUTEX-PLATFORM", "Windows named mutex is unavailable")
        if not key or len(key) > 256:
            raise AppOperationError("RKBAPP-MUTEX-KEY", "Writer mutex key is invalid")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        name = f"Local\\ResearchKB-{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
        descriptor = _descriptor_from_sddl(_mutex_sddl())
        attributes = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
        kernel32.CreateMutexW.argtypes = [ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(ctypes.byref(attributes), False, name)
        if not handle:
            _local_free(descriptor)
            raise AppOperationError("RKBAPP-MUTEX-CREATE", "Writer mutex could not be created")
        try:
            result = kernel32.WaitForSingleObject(handle, int(self.timeout_seconds * 1000))
            if result not in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
                if result == _WAIT_TIMEOUT:
                    raise AppOperationError("RKBAPP-MUTEX-TIMEOUT", "Another setup operation is active")
                raise AppOperationError("RKBAPP-MUTEX-WAIT", "Writer mutex wait failed")
            try:
                yield handle
            finally:
                kernel32.ReleaseMutex(handle)
        finally:
            kernel32.CloseHandle(handle)
            _local_free(descriptor)


def inspect_windows_root(path: Path) -> WindowsRootFacts:
    if os.name != "nt":
        return WindowsRootFacts("not-applicable", "not_applicable", False, False, False, False)
    original = _validated_absolute_path(path, allow_missing=False)
    reparse_free = not _has_reparse_ancestry(original)
    try:
        resolved = original.resolve(strict=True)
    except OSError:
        return WindowsRootFacts("unknown", "unknown", False, reparse_free, False, False)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    volume_path = ctypes.create_unicode_buffer(32768)
    kernel32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetVolumePathNameW.restype = wintypes.BOOL
    if not kernel32.GetVolumePathNameW(str(resolved), volume_path, len(volume_path)):
        return WindowsRootFacts("unknown", "unknown", False, reparse_free, False, False)
    kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetDriveTypeW.restype = wintypes.UINT
    drive_type = kernel32.GetDriveTypeW(volume_path.value)
    local = drive_type not in {_DRIVE_UNKNOWN, _DRIVE_NO_ROOT_DIR, _DRIVE_REMOTE}
    serial = wintypes.DWORD()
    max_component = wintypes.DWORD()
    flags = wintypes.DWORD()
    filesystem = ctypes.create_unicode_buffer(256)
    kernel32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    kernel32.GetVolumeInformationW.restype = wintypes.BOOL
    ok = kernel32.GetVolumeInformationW(
        volume_path.value,
        None,
        0,
        ctypes.byref(serial),
        ctypes.byref(max_component),
        ctypes.byref(flags),
        filesystem,
        len(filesystem),
    )
    if not ok:
        return WindowsRootFacts("unknown", "unknown", local, reparse_free, False, False)
    volume_name = ctypes.create_unicode_buffer(32768)
    kernel32.GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL
    if kernel32.GetVolumeNameForVolumeMountPointW(volume_path.value, volume_name, len(volume_name)):
        volume_id = volume_name.value.rstrip("\\").casefold()
    else:
        volume_id = f"serial-{serial.value:08x}"
    acl_secure, acl_protected = _inspect_acl(resolved)
    return WindowsRootFacts(
        volume_id,
        filesystem.value.upper() or "unknown",
        local,
        reparse_free,
        acl_secure,
        acl_protected,
    )


def apply_protected_current_user_acl(path: Path) -> None:
    if os.name != "nt":
        raise AppOperationError("RKBAPP-ACL-PLATFORM", "Windows ACLs are unavailable")
    descriptor = _descriptor_from_sddl(_directory_sddl())
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        advapi32.SetFileSecurityW.restype = wintypes.BOOL
        information = _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION
        if not advapi32.SetFileSecurityW(str(path), information, descriptor):
            raise AppOperationError("RKBAPP-ACL-SET", "Managed root ACL could not be applied")
    finally:
        _local_free(descriptor)


def _inspect_acl(path: Path) -> tuple[bool, bool]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi32.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi32.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    advapi32.GetAclInformation.restype = wintypes.BOOL
    advapi32.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetAce.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        _SE_FILE_OBJECT,
        _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor.value or not dacl.value:
        if descriptor.value:
            kernel32.LocalFree(descriptor)
        return False, False
    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        protected = bool(
            advapi32.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
            and control.value & 0x1000
        )
        information = ACL_SIZE_INFORMATION()
        if not advapi32.GetAclInformation(
            dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            return False, protected
        try:
            current_sid = current_user_sid()
        except (AppOperationError, OSError):
            return False, protected
        allowed_writers = {current_sid, _SYSTEM_SID, _ADMINISTRATORS_SID}
        current_full = False
        for index in range(information.AceCount):
            ace_pointer = ctypes.c_void_p()
            if not advapi32.GetAce(dacl, index, ctypes.byref(ace_pointer)) or not ace_pointer.value:
                return False, protected
            ace = ctypes.cast(ace_pointer, ctypes.POINTER(ACCESS_ALLOWED_ACE)).contents
            if ace.Header.AceType == _ACCESS_DENIED_ACE_TYPE:
                return False, protected
            if ace.Header.AceType != _ACCESS_ALLOWED_ACE_TYPE:
                return False, protected
            sid_pointer = ctypes.c_void_p(ace_pointer.value + ACCESS_ALLOWED_ACE.SidStart.offset)
            try:
                sid = _sid_to_string(sid_pointer)
            except (AppOperationError, OSError):
                return False, protected
            applies_to_root = not (ace.Header.AceFlags & _INHERIT_ONLY_ACE)
            if applies_to_root and ace.Mask & _WRITE_MASK and sid not in allowed_writers:
                return False, protected
            if applies_to_root and sid == current_sid and (
                ace.Mask & _GENERIC_ALL or ace.Mask & _FILE_ALL_ACCESS == _FILE_ALL_ACCESS
            ):
                current_full = True
        return current_full, protected
    finally:
        kernel32.LocalFree(descriptor)


def current_user_sid() -> str:
    if os.name != "nt":
        raise AppOperationError("RKBAPP-SID-PLATFORM", "Windows user identity is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise AppOperationError("RKBAPP-SID", "Current user identity could not be opened")
    try:
        required = wintypes.DWORD()
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(required))
        if required.value == 0:
            raise AppOperationError("RKBAPP-SID", "Current user identity is unavailable")
        buffer = ctypes.create_string_buffer(required.value)
        if not advapi32.GetTokenInformation(token, _TOKEN_USER, buffer, required, ctypes.byref(required)):
            raise AppOperationError("RKBAPP-SID", "Current user identity could not be read")
        token_user = ctypes.cast(buffer, ctypes.POINTER(TOKEN_USER_VALUE)).contents
        return _sid_to_string(token_user.User.Sid)
    finally:
        kernel32.CloseHandle(token)


def _sid_to_string(sid: ctypes.c_void_p | int) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    value = wintypes.LPWSTR()
    pointer = sid if isinstance(sid, ctypes.c_void_p) else ctypes.c_void_p(sid)
    if not advapi32.ConvertSidToStringSidW(pointer, ctypes.byref(value)):
        raise AppOperationError("RKBAPP-SID", "Windows security principal could not be read")
    try:
        return value.value
    finally:
        _local_free(ctypes.cast(value, ctypes.c_void_p))


def _descriptor_from_sddl(sddl: str) -> ctypes.c_void_p:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        _SDDL_REVISION_1,
        ctypes.byref(descriptor),
        None,
    ):
        raise AppOperationError("RKBAPP-ACL-SDDL", "Windows security policy could not be materialized")
    return descriptor


def _directory_sddl() -> str:
    return f"D:P(A;OICI;FA;;;{current_user_sid()})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"


def _mutex_sddl() -> str:
    return f"D:P(A;;GA;;;{current_user_sid()})(A;;GA;;;SY)(A;;GA;;;BA)"


def _local_free(pointer: ctypes.c_void_p) -> None:
    if pointer and pointer.value:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(pointer)


def _validated_existing_directory(path: Path) -> Path:
    candidate = _validated_absolute_path(path, allow_missing=False)
    if not candidate.is_dir():
        raise AppOperationError("RKBAPP-ROOT-TYPE", "Managed root is not an existing directory")
    # Keep the lexical path so the platform probe can inspect reparse ancestry
    # before resolving the final target identity.
    return candidate


def _validated_absolute_path(path: Path, *, allow_missing: bool) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or str(candidate).startswith("\\\\"):
        raise AppOperationError("RKBAPP-ROOT-PATH", "Managed root must be an absolute local path")
    if _utf16_units(str(candidate)) > MAX_SUPPORTED_PATH_UTF16_UNITS:
        raise AppOperationError("RKBAPP-ROOT-PATH", "Managed root path exceeds the supported length")
    if not allow_missing and not candidate.exists():
        raise AppOperationError("RKBAPP-ROOT-PATH", "Managed root is unavailable")
    return candidate


def _has_reparse_ancestry(path: Path) -> bool:
    candidate = Path(path).absolute()
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current = current / part
        try:
            details = current.stat(follow_symlinks=False)
        except OSError:
            return True
        attributes = getattr(details, "st_file_attributes", 0)
        if stat.S_ISLNK(details.st_mode) or attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            return True
    return False


def _acceptable(attestation: RootSecurityAttestation) -> bool:
    return (
        attestation.filesystem.upper() == "NTFS"
        and attestation.local
        and attestation.reparse_free
        and attestation.acl_policy_id == ROOT_SECURITY_POLICY
        and attestation.acl_secure
        and bool(attestation.volume_id)
    )


def _require_acceptable(attestation: RootSecurityAttestation) -> None:
    if not _acceptable(attestation):
        raise AppOperationError("RKBAPP-ROOT-SECURITY", "Managed root does not satisfy the Windows beta policy")


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


__all__ = [
    "MAX_SUPPORTED_PATH_UTF16_UNITS",
    "WindowsNamedMutexFactory",
    "WindowsRootFacts",
    "WindowsRootSecurityService",
    "apply_protected_current_user_acl",
    "current_user_sid",
    "inspect_windows_root",
]
