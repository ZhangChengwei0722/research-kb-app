from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from research_kb_app.errors import AppOperationError


EGRESS_POLICY = "research-kb-egress-policy@1.0"
MAX_CLIPBOARD_UTF8_BYTES = 1024 * 1024
MAX_TASK_PACKAGE_BYTES = 4 * 1024 * 1024
RESTRICTED_CLEAR_DELAY_SECONDS = 60
_CLIPBOARD_KEY = r"Software\Microsoft\Clipboard"
_POLICY_KEY = r"Software\Policies\Microsoft\Windows\System"
_METADATA_CLASSES = frozenset({"metadata", "bibliographic_metadata", "visible_ui_text"})
_TASK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_ROUTES = {
    "cloud_agent": "explicit",
    "local_agent_package": "explicit",
    "clipboard": "conditional",
    "backup": "explicit",
    "obsidian": "explicit",
    "discovery": "metadata_only",
    "acquisition": "explicit_create_only",
    "exchange": "explicit",
    "telemetry": "disabled",
    "update": "disabled",
    "support_upload": "disabled",
}


RegistryReader = Callable[[str, str], object | None]
ReceiptWriter = Callable[[dict[str, Any]], object]


class ClipboardBackend(Protocol):
    def write_text(self, text: str) -> None: ...

    def clear_if_digest(self, expected_sha256: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ClipboardSecurityState:
    policy_id: str
    history: str
    cloud_sync: str

    def public(self) -> dict[str, str]:
        return {"policy_id": self.policy_id, "history": self.history, "cloud_sync": self.cloud_sync}


@dataclass(slots=True)
class _TimedClearOperation:
    operation_id: str
    content_sha256: str
    receipt: dict[str, Any]
    timer: Any | None = None
    status: str = "scheduled"


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int


def _file_identity(stat_result: os.stat_result) -> _FileIdentity:
    return _FileIdentity(int(stat_result.st_dev), int(stat_result.st_ino))


class ClipboardPolicyProbe:
    def __init__(self, *, registry_reader: RegistryReader | None = None) -> None:
        self._read = registry_reader or _read_current_user_registry

    def inspect(self) -> ClipboardSecurityState:
        history = _effective_state(
            self._read(_CLIPBOARD_KEY, "EnableClipboardHistory"),
            self._read(_POLICY_KEY, "AllowClipboardHistory"),
        )
        cloud = _effective_state(
            self._read(_CLIPBOARD_KEY, "CloudClipboardAutomaticUpload"),
            self._read(_POLICY_KEY, "AllowCrossDeviceClipboard"),
        )
        return ClipboardSecurityState(EGRESS_POLICY, history, cloud)


class EgressPolicyService:
    def __init__(
        self,
        *,
        clipboard_probe: ClipboardPolicyProbe | None = None,
        clipboard_backend: ClipboardBackend | None = None,
        receipt_writer: ReceiptWriter | None = None,
        clock: Callable[[], datetime] | None = None,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self.clipboard_probe = clipboard_probe or ClipboardPolicyProbe()
        self.clipboard_backend = clipboard_backend or WindowsClipboardBackend()
        self.receipt_writer = receipt_writer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.timer_factory = timer_factory
        self._timed_clear_lock = threading.RLock()
        self._timed_clear_operations: dict[str, _TimedClearOperation] = {}
        self._closed = False

    def show(self) -> dict[str, Any]:
        clipboard = self.clipboard_probe.inspect()
        return {
            "status": "success",
            "policy_id": EGRESS_POLICY,
            "routes": dict(_ROUTES),
            "clipboard": clipboard.public(),
        }

    def copy_text(
        self,
        text: str,
        *,
        content_classes: Iterable[str],
        metadata_disclosure_accepted: bool,
        clear_after_seconds: int | None = None,
        user_action: str = "explicit_copy",
    ) -> dict[str, Any]:
        self._require_open()
        if not isinstance(text, str) or not text:
            raise AppOperationError("RKBAPP-EGRESS-CONTENT", "Clipboard content is empty", status_code=400)
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_CLIPBOARD_UTF8_BYTES:
            raise AppOperationError("RKBAPP-EGRESS-CONTENT", "Clipboard content exceeds the supported budget", status_code=413)
        raw_classes = tuple(content_classes)
        if not raw_classes or any(not isinstance(item, str) or not item for item in raw_classes):
            raise AppOperationError("RKBAPP-EGRESS-CLASS", "Clipboard content classes are invalid", status_code=400)
        classes = tuple(sorted(set(raw_classes)))
        restricted = any(item not in _METADATA_CLASSES for item in classes)
        state = self.clipboard_probe.inspect()
        if restricted and (state.history != "disabled" or state.cloud_sync != "disabled"):
            raise AppOperationError(
                "RKBAPP-CLIPBOARD-POLICY",
                "Restricted content cannot be copied while clipboard history or cloud sync is enabled or unknown",
            )
        if not restricted and not metadata_disclosure_accepted:
            raise AppOperationError(
                "RKBAPP-CLIPBOARD-DISCLOSURE",
                "Metadata clipboard disclosure must be accepted",
            )

        digest = hashlib.sha256(encoded).hexdigest()
        operation_id = f"egress_{secrets.token_hex(24)}"
        receipt = {
            "policy_id": EGRESS_POLICY,
            "operation_id": operation_id,
            "route": "clipboard",
            "content_classes": list(classes),
            "execution_scope": "current_user_windows_clipboard",
            "target_identity": "windows_clipboard",
            "user_action": user_action,
            "content_sha256": digest,
            "content_utf8_bytes": len(encoded),
            "clipboard_history": state.history,
            "clipboard_cloud_sync": state.cloud_sync,
            "custody": "os_clipboard",
            "failure_disposition": "none",
            "created_at": _timestamp(self.clock()),
        }
        if self.receipt_writer is not None:
            self.receipt_writer({**receipt, "event": "clipboard_copy_intent"})
        try:
            self.clipboard_backend.write_text(text)
        except Exception:
            self._record_failure(receipt, "clipboard_write", "not_written")
            raise
        timed_clear: _TimedClearOperation | None = None
        if restricted:
            timed_clear = self._schedule_timed_clear(
                operation_id=operation_id,
                digest=digest,
                receipt=receipt,
            )
        else:
            # The delay is a service policy. A caller-provided value is never used
            # to extend or omit the restricted-content lifecycle.
            _ = clear_after_seconds
        timed_clear_status = "not_required" if timed_clear is None else timed_clear.status
        timed_clear_completed = timed_clear_status == "cleared"
        if self.receipt_writer is not None:
            try:
                self.receipt_writer(
                    {
                        **receipt,
                        "event": "clipboard_copy_completed",
                        "timed_clear_scheduled": timed_clear is not None,
                        "timed_clear_completed": timed_clear_completed,
                        "timed_clear_status": timed_clear_status,
                    }
                )
            except Exception:
                if timed_clear is not None:
                    self._cancel_timed_clear(timed_clear, stage="completion_receipt")
                else:
                    disposition = self._attempt_clear(digest)[1]
                    self._record_failure(receipt, "completion_receipt", disposition)
                raise
        return {
            "status": "success",
            "route": "clipboard",
            "content_sha256": digest,
            "timed_clear_scheduled": timed_clear is not None,
            "timed_clear_completed": timed_clear_completed,
            "timed_clear_status": timed_clear_status,
            "clipboard": state.public(),
        }

    def export_task_package(self, destination: Path, handoff: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(handoff, dict):
            raise AppOperationError("RKBAPP-EGRESS-PACKAGE", "Agent task package is invalid", status_code=400)
        task_id = handoff.get("task_id")
        classes = handoff.get("effective_content_classes")
        if (
            not isinstance(task_id, str)
            or not _TASK_ID.fullmatch(task_id)
            or not isinstance(classes, list)
            or not classes
            or any(not isinstance(item, str) or not item for item in classes)
        ):
            raise AppOperationError("RKBAPP-EGRESS-PACKAGE", "Agent task package identity is invalid", status_code=400)
        root = Path(destination).resolve(strict=True)
        if not root.is_dir():
            raise AppOperationError("RKBAPP-EGRESS-PACKAGE", "Agent task package destination is unavailable")
        content = json.dumps(handoff, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(content) > MAX_TASK_PACKAGE_BYTES:
            raise AppOperationError("RKBAPP-EGRESS-PACKAGE", "Agent task package exceeds the supported budget", status_code=413)
        digest = hashlib.sha256(content).hexdigest()
        filename = f"research-kb-agent-task-{task_id}.json"
        target = root / filename
        operation_id = f"egress_{secrets.token_hex(24)}"
        receipt = {
            "policy_id": EGRESS_POLICY,
            "operation_id": operation_id,
            "route": "local_agent_package",
            "task_id": task_id,
            "content_classes": sorted(set(classes)),
            "execution_scope": "explicit_user_selected_local_directory",
            "target_identity": filename,
            "user_action": "explicit_create_only_export",
            "content_sha256": digest,
            "content_utf8_bytes": len(content),
            "custody": "user_selected_local_directory",
            "failure_disposition": "none",
            "created_at": _timestamp(self.clock()),
        }
        if self.receipt_writer is not None:
            self.receipt_writer({**receipt, "event": "task_package_export_intent"})
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            self._record_package_failure(receipt, "target_exists", "not_written")
            raise AppOperationError(
                "RKBAPP-EGRESS-PACKAGE-EXISTS",
                "Agent task package already exists; choose another empty destination",
                status_code=409,
            ) from error
        owned_identity: _FileIdentity | None = None
        stage = "create"
        descriptor_open = True
        try:
            owned_identity = _file_identity(os.fstat(descriptor))
            stage = "write"
            handle = os.fdopen(descriptor, "wb")
            descriptor_open = False
            with handle:
                handle.write(content)
                stage = "flush"
                handle.flush()
                stage = "fsync"
                os.fsync(handle.fileno())
            stage = "readback"
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise OSError("task package read-back digest mismatch")
            stage = "completion_receipt"
            if self.receipt_writer is not None:
                self.receipt_writer({**receipt, "event": "task_package_export_completed"})
        except Exception:
            disposition = self._cleanup_owned_file(target, owned_identity)
            self._record_package_failure(receipt, stage, disposition)
            raise
        finally:
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        return {
            "status": "success",
            "route": "local_agent_package",
            "filename": filename,
            "content_sha256": digest,
            "content_utf8_bytes": len(content),
        }

    def close(self) -> None:
        with self._timed_clear_lock:
            if self._closed and not self._timed_clear_operations:
                return
            self._closed = True
            operations = tuple(self._timed_clear_operations.values())
        for operation in operations:
            self._cancel_timed_clear(operation, stage="close")

    def _require_open(self) -> None:
        with self._timed_clear_lock:
            if self._closed:
                raise AppOperationError("RKBAPP-EGRESS-CLOSED", "Egress policy service is closed", status_code=503)

    def _schedule_timed_clear(
        self,
        *,
        operation_id: str,
        digest: str,
        receipt: dict[str, Any],
    ) -> _TimedClearOperation:
        operation = _TimedClearOperation(operation_id, digest, dict(receipt))
        with self._timed_clear_lock:
            if self._closed:
                raise AppOperationError("RKBAPP-EGRESS-CLOSED", "Egress policy service is closed", status_code=503)
            self._timed_clear_operations[operation_id] = operation
        try:
            timer = self.timer_factory(
                RESTRICTED_CLEAR_DELAY_SECONDS,
                self._run_timed_clear,
                args=(operation_id,),
            )
            timer.daemon = True
            operation.timer = timer
            timer.start()
        except Exception as error:
            with self._timed_clear_lock:
                self._timed_clear_operations.pop(operation_id, None)
                operation.status = "failed"
            disposition = self._attempt_clear(digest)[1]
            self._record_timed_clear_failure(operation, "timed_clear_schedule", disposition)
            self._record_failure(receipt, "timed_clear_schedule", disposition)
            raise AppOperationError("RKBAPP-CLIPBOARD-CLEAR", "Clipboard clear could not be scheduled") from error
        return operation

    def _run_timed_clear(self, operation_id: str) -> None:
        with self._timed_clear_lock:
            operation = self._timed_clear_operations.get(operation_id)
            if operation is None or operation.status != "scheduled":
                return
            operation.status = "clearing"
        try:
            cleared, disposition = self._attempt_clear(operation.content_sha256)
            if cleared:
                self._finish_timed_clear(operation, "cleared", "timed_clear_callback")
            else:
                self._finish_timed_clear(operation, disposition, "timed_clear_callback")
        except Exception:
            # Timer threads must never leak an exception outside the service.
            operation.status = "failed"
            self._record_timed_clear_failure(operation, "timed_clear_callback", "clear_failed")

    def _cancel_timed_clear(self, operation: _TimedClearOperation, *, stage: str) -> None:
        with self._timed_clear_lock:
            if operation.status != "scheduled":
                return
            operation.status = "clearing"
        timer = operation.timer
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        try:
            cleared, disposition = self._attempt_clear(operation.content_sha256)
            if cleared:
                self._finish_timed_clear(operation, "cleared", stage)
            else:
                self._finish_timed_clear(operation, disposition, stage)
        except Exception:
            operation.status = "failed"
            self._record_timed_clear_failure(operation, stage, "clear_failed")

    def _finish_timed_clear(self, operation: _TimedClearOperation, status: str, stage: str) -> None:
        with self._timed_clear_lock:
            operation.status = status
            self._timed_clear_operations.pop(operation.operation_id, None)
        if status == "cleared":
            self._record_receipt(
                {
                    **operation.receipt,
                    "event": "clipboard_timed_clear_completed",
                    "failure_disposition": "none",
                    "clear_stage": stage,
                }
            )
        else:
            self._record_timed_clear_failure(operation, stage, status)

    def _record_timed_clear_failure(self, operation: _TimedClearOperation, stage: str, disposition: str) -> None:
        self._record_receipt(
            {
                **operation.receipt,
                "event": "clipboard_timed_clear_failed",
                "failure_stage": stage,
                "failure_disposition": disposition,
            }
        )

    def _record_receipt(self, payload: dict[str, Any]) -> None:
        if self.receipt_writer is None:
            return
        try:
            self.receipt_writer(payload)
        except Exception:
            pass

    def _attempt_clear(self, digest: str) -> tuple[bool, str]:
        try:
            cleared = self.clipboard_backend.clear_if_digest(digest)
        except Exception:
            return False, "clear_failed"
        return (True, "cleared") if cleared else (False, "clipboard_changed")

    def _cleanup_owned_file(self, target: Path, owned_identity: _FileIdentity | None) -> str:
        if owned_identity is None:
            return "cleanup_failed"
        try:
            current_identity = _file_identity(os.stat(target, follow_symlinks=False))
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "cleanup_failed"
        if current_identity != owned_identity:
            return "retained_identity_changed"
        try:
            target.unlink()
        except FileNotFoundError:
            return "removed"
        except OSError:
            return "cleanup_failed"
        return "removed"

    def _record_failure(self, receipt: dict[str, Any], stage: str, disposition: str) -> None:
        if self.receipt_writer is None:
            return
        try:
            self.receipt_writer(
                {
                    **receipt,
                    "event": "clipboard_copy_failed",
                    "failure_stage": stage,
                    "failure_disposition": disposition,
                }
            )
        except Exception:
            pass

    def _record_package_failure(self, receipt: dict[str, Any], stage: str, disposition: str) -> None:
        if self.receipt_writer is None:
            return
        try:
            self.receipt_writer(
                {
                    **receipt,
                    "event": "task_package_export_failed",
                    "failure_stage": stage,
                    "failure_disposition": disposition,
                }
            )
        except Exception:
            pass


class WindowsClipboardBackend:
    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002

    def write_text(self, text: str) -> None:
        if os.name != "nt":
            raise AppOperationError("RKBAPP-CLIPBOARD-PLATFORM", "Windows clipboard is unavailable")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_clipboard_api(user32, kernel32)
        encoded = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(self._GMEM_MOVEABLE, len(encoded))
        if not handle:
            raise AppOperationError("RKBAPP-CLIPBOARD-WRITE", "Clipboard allocation failed")
        transferred = False
        try:
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise AppOperationError("RKBAPP-CLIPBOARD-WRITE", "Clipboard allocation could not be locked")
            try:
                ctypes.memmove(pointer, encoded, len(encoded))
            finally:
                kernel32.GlobalUnlock(handle)
            _open_clipboard(user32)
            try:
                if not user32.EmptyClipboard() or not user32.SetClipboardData(self._CF_UNICODETEXT, handle):
                    raise AppOperationError("RKBAPP-CLIPBOARD-WRITE", "Clipboard write failed")
                transferred = True
            finally:
                user32.CloseClipboard()
        finally:
            if not transferred:
                kernel32.GlobalFree(handle)

    def clear_if_digest(self, expected_sha256: str) -> bool:
        if os.name != "nt":
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _configure_clipboard_api(user32, kernel32)
        _open_clipboard(user32)
        try:
            handle = user32.GetClipboardData(self._CF_UNICODETEXT)
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return False
            try:
                current = ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
            if hashlib.sha256(current.encode("utf-8")).hexdigest() != expected_sha256:
                return False
            return bool(user32.EmptyClipboard())
        finally:
            user32.CloseClipboard()


def _open_clipboard(user32: Any) -> None:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.01)
    raise AppOperationError("RKBAPP-CLIPBOARD-BUSY", "Clipboard is currently unavailable")


def _configure_clipboard_api(user32: Any, kernel32: Any) -> None:
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.restype = wintypes.HANDLE


def _read_current_user_registry(subkey: str, name: str) -> object | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _effective_state(value: object | None, policy: object | None) -> str:
    state = _binary_state(value)
    policy_state = _binary_state(policy)
    if state == "unknown":
        return "unknown"
    if policy is None:
        return state
    if policy_state == "unknown" or policy_state != state:
        return "unknown"
    return state


def _binary_state(value: object | None) -> str:
    if type(value) is not int or value not in {0, 1}:
        return "unknown"
    return "enabled" if value == 1 else "disabled"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("egress clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "EGRESS_POLICY",
    "RESTRICTED_CLEAR_DELAY_SECONDS",
    "ClipboardPolicyProbe",
    "ClipboardSecurityState",
    "EgressPolicyService",
    "WindowsClipboardBackend",
]
