from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_kb_app.errors import AppOperationError
from research_kb_app.lifecycle_receipts import AppendOnlyReceiptStore


INSTANCE_CONTRACT = "research-kb-app-profile-instance@1.0"
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    process_creation_filetime: int
    executable_sha256: str


class ProfileInstanceGuard(AbstractContextManager["ProfileInstanceGuard"]):
    def __init__(
        self,
        runtime_root: Path,
        instance_key: str,
        *,
        mutex_factory: Callable[[str], AbstractContextManager[Any]],
        receipt_store: AppendOnlyReceiptStore,
        identity_provider: Callable[[], ProcessIdentity] | None = None,
        identity_matcher: Callable[[ProcessIdentity], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.runtime_root = Path(runtime_root)
        self.instance_key = instance_key
        self.mutex_factory = mutex_factory
        self.receipt_store = receipt_store
        self.identity_provider = identity_provider or current_process_identity
        self.identity_matcher = identity_matcher or process_identity_is_live
        self.clock = clock or (lambda: datetime.now(UTC))
        self.instance_id = f"instance_{uuid.uuid4()}"
        self._mutex: AbstractContextManager[Any] | None = None
        self._record_digest: str | None = None

    @property
    def current_path(self) -> Path:
        return self.runtime_root / "current-instance.json"

    def __enter__(self) -> "ProfileInstanceGuard":
        self._mutex = self.mutex_factory(self.instance_key)
        self._mutex.__enter__()
        try:
            self.runtime_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            stale = _read_record(self.current_path, missing_ok=True)
            if stale is not None:
                identity = ProcessIdentity(
                    stale["pid"],
                    stale["process_creation_filetime"],
                    stale["executable_sha256"],
                )
                if self.identity_matcher(identity):
                    raise AppOperationError("RKBAPP-INSTANCE-ACTIVE", "Managed profile is already open")
                self.receipt_store.append(
                    {
                        "event": "stale_instance_recovered",
                        "prior_instance_id": stale["instance_id"],
                        "prior_record_digest": stale["record_digest"],
                        "recovery_basis": "pid_creation_and_executable_identity_not_live",
                    }
                )
                self.current_path.unlink()
            identity = self.identity_provider()
            basis = {
                "contract_version": INSTANCE_CONTRACT,
                "instance_id": self.instance_id,
                "instance_key_sha256": hashlib.sha256(self.instance_key.encode("utf-8")).hexdigest(),
                **asdict(identity),
                "acquired_at": _timestamp(self.clock()),
            }
            record = {**basis, "record_digest": _digest(basis)}
            _write_new(self.current_path, _json_bytes(record))
            self._record_digest = record["record_digest"]
            self.receipt_store.append(
                {
                    "event": "profile_instance_acquired",
                    "instance_id": self.instance_id,
                    "instance_record_digest": self._record_digest,
                    "pid": identity.pid,
                    "process_creation_filetime": identity.process_creation_filetime,
                    "executable_sha256": identity.executable_sha256,
                }
            )
            return self
        except Exception:
            if self._record_digest is not None:
                current = _read_record(self.current_path, missing_ok=True)
                if current is not None and current["instance_id"] == self.instance_id:
                    self.current_path.unlink()
            self._release_mutex(None, None, None)
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        try:
            current = _read_record(self.current_path, missing_ok=False)
            if current is None or current["instance_id"] != self.instance_id or current["record_digest"] != self._record_digest:
                raise AppOperationError("RKBAPP-INSTANCE-DRIFT", "Managed profile instance receipt changed")
            self.current_path.unlink()
            self.receipt_store.append(
                {
                    "event": "profile_instance_released",
                    "instance_id": self.instance_id,
                    "instance_record_digest": self._record_digest,
                    "exit": "error" if exc_type is not None else "clean",
                }
            )
        finally:
            self._release_mutex(exc_type, exc_value, traceback)
        return None

    def _release_mutex(self, exc_type, exc_value, traceback) -> None:
        mutex = self._mutex
        self._mutex = None
        if mutex is not None:
            mutex.__exit__(exc_type, exc_value, traceback)


def current_process_identity() -> ProcessIdentity:
    if os.name != "nt":
        raise AppOperationError("RKBAPP-INSTANCE-PLATFORM", "Windows process identity is unavailable")
    return _process_identity(os.getpid(), current_process=True)


def process_identity_is_live(expected: ProcessIdentity) -> bool:
    if os.name != "nt":
        raise AppOperationError("RKBAPP-INSTANCE-PLATFORM", "Windows process identity is unavailable")
    try:
        actual = _process_identity(expected.pid, current_process=False)
    except ProcessLookupError:
        return False
    return actual == expected


def _process_identity(pid: int, *, current_process: bool) -> ProcessIdentity:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.GetCurrentProcess() if current_process else kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:
            raise ProcessLookupError(pid)
        raise AppOperationError("RKBAPP-INSTANCE-IDENTITY", "Process identity could not be inspected")
    close = not current_process
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        if not kernel32.GetProcessTimes(handle, creation, exit_time, kernel, user):
            raise AppOperationError("RKBAPP-INSTANCE-IDENTITY", "Process creation time is unavailable")
        size = wintypes.DWORD(32768)
        executable = ctypes.create_unicode_buffer(size.value)
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        if not kernel32.QueryFullProcessImageNameW(handle, 0, executable, ctypes.byref(size)):
            raise AppOperationError("RKBAPP-INSTANCE-IDENTITY", "Process executable identity is unavailable")
        creation_value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        executable_sha256 = _file_sha256(Path(executable.value))
        return ProcessIdentity(pid, creation_value, executable_sha256)
    finally:
        if close:
            kernel32.CloseHandle(handle)


def _read_record(path: Path, *, missing_ok: bool) -> dict[str, Any] | None:
    if not path.is_file():
        if missing_ok:
            return None
        raise AppOperationError("RKBAPP-INSTANCE-RECEIPT", "Managed profile instance receipt is missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AppOperationError("RKBAPP-INSTANCE-RECEIPT", "Managed profile instance receipt is unreadable") from error
    expected_keys = {
        "contract_version",
        "instance_id",
        "instance_key_sha256",
        "pid",
        "process_creation_filetime",
        "executable_sha256",
        "acquired_at",
        "record_digest",
    }
    if not isinstance(value, dict) or set(value) != expected_keys or value.get("contract_version") != INSTANCE_CONTRACT:
        raise AppOperationError("RKBAPP-INSTANCE-RECEIPT", "Managed profile instance receipt is invalid")
    digest = value["record_digest"]
    basis = {key: item for key, item in value.items() if key != "record_digest"}
    if not isinstance(digest, str) or _digest(basis) != digest:
        raise AppOperationError("RKBAPP-INSTANCE-RECEIPT", "Managed profile instance receipt digest is invalid")
    return value


def _write_new(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise AppOperationError("RKBAPP-INSTANCE-IDENTITY", "Process executable could not be hashed") from error
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value).rstrip(b"\n")).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("instance clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "INSTANCE_CONTRACT",
    "ProcessIdentity",
    "ProfileInstanceGuard",
    "current_process_identity",
    "process_identity_is_live",
]
