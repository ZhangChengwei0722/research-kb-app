from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable


PROTOCOL = "research-kb-folder-helper@1.0"
MAX_FRAME_BYTES = 65_536
PURPOSES = frozenset(
    {
        "workspace_parent",
        "existing_workspace_config",
        "source_root",
        "local_inbox",
        "obsidian_vault",
        "task_package_destination",
        "backup_destination",
    }
)
INITIAL_LOCATIONS = frozenset({"home", "documents", "local_app_data"})
_REQUEST_KEYS = {
    "protocol",
    "auth_secret",
    "purpose",
    "allow_existing",
    "allow_new_child",
    "initial_location_id",
    "parent_pid",
    "request_nonce",
}
Selector = Callable[[dict[str, Any]], str]


def main() -> int:
    raw = sys.stdin.buffer.readline(MAX_FRAME_BYTES + 1)
    if not raw or len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
        return 2
    if sys.stdin.buffer.read(1):
        return 2
    try:
        request = json.loads(raw.decode("utf-8"))
        _validate_request(request)
    except (UnicodeError, ValueError, TypeError):
        return 3
    threading.Thread(target=_watch_parent, args=(request["parent_pid"],), daemon=True).start()
    response = execute_request(request, selector=_select_directory)
    encoded = _json_bytes(response)
    if len(encoded) > MAX_FRAME_BYTES:
        return 4
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


def execute_request(request: dict[str, Any], *, selector: Selector) -> dict[str, Any]:
    _validate_request(request)
    try:
        selected = selector(request)
        if not selected:
            payload = {
                "protocol": PROTOCOL,
                "status": "cancelled",
                "request_nonce": request["request_nonce"],
                "path": None,
                "diagnostic_code": None,
            }
        else:
            path = Path(selected)
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("selected path is invalid")
            payload = {
                "protocol": PROTOCOL,
                "status": "selected",
                "request_nonce": request["request_nonce"],
                "path": str(path),
                "diagnostic_code": None,
            }
    except Exception:
        payload = {
            "protocol": PROTOCOL,
            "status": "failure",
            "request_nonce": request["request_nonce"],
            "path": None,
            "diagnostic_code": "RKBAPP-FOLDER-HELPER",
        }
    return {**payload, "auth_tag": _auth_tag(payload, request["auth_secret"])}


def _select_directory(request: dict[str, Any]) -> str:
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        return filedialog.askdirectory(
            parent=root,
            mustexist=not request["allow_new_child"],
            initialdir=str(_initial_location(request["initial_location_id"])),
            title=_dialog_title(request["purpose"]),
        )
    finally:
        root.destroy()


def _initial_location(location_id: str | None) -> Path:
    if location_id == "documents":
        return Path.home() / "Documents"
    if location_id == "local_app_data":
        value = os.environ.get("LOCALAPPDATA")
        if value:
            return Path(value)
    return Path.home()


def _dialog_title(purpose: str) -> str:
    return {
        "workspace_parent": "Select workspace parent",
        "existing_workspace_config": "Select existing workspace folder",
        "source_root": "Select source folder",
        "local_inbox": "Select local inbox",
        "obsidian_vault": "Select Obsidian vault",
        "task_package_destination": "Select task package destination",
        "backup_destination": "Select backup destination",
    }[purpose]


def _validate_request(request: Any) -> None:
    valid = (
        isinstance(request, dict)
        and set(request) == _REQUEST_KEYS
        and request.get("protocol") == PROTOCOL
        and isinstance(request.get("auth_secret"), str)
        and len(request["auth_secret"]) == 64
        and all(character in "0123456789abcdef" for character in request["auth_secret"])
        and request.get("purpose") in PURPOSES
        and type(request.get("allow_existing")) is bool
        and type(request.get("allow_new_child")) is bool
        and request.get("initial_location_id") in INITIAL_LOCATIONS | {None}
        and type(request.get("parent_pid")) is int
        and request["parent_pid"] > 0
        and isinstance(request.get("request_nonce"), str)
        and 16 <= len(request["request_nonce"]) <= 128
    )
    if not valid:
        raise ValueError("folder helper request is invalid")


def _watch_parent(parent_pid: int) -> None:
    while True:
        time.sleep(0.5)
        if not _parent_is_alive(parent_pid):
            os._exit(5)


def _parent_is_alive(parent_pid: int) -> bool:
    if sys.platform == "win32":
        return _windows_parent_is_alive(parent_pid)
    try:
        os.kill(parent_pid, 0)
    except OSError:
        return False
    return True


def _windows_parent_is_alive(parent_pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(synchronize, False, parent_pid)
    if not handle:
        return False
    try:
        return wait_for_single_object(handle, 0) == wait_timeout
    finally:
        close_handle(handle)


def _auth_tag(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(bytes.fromhex(secret), _canonical_bytes(payload), hashlib.sha256).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return _canonical_bytes(value) + b"\n"


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MAX_FRAME_BYTES", "PROTOCOL", "execute_request", "main"]
