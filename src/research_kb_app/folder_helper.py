from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from research_kb_app.errors import AppOperationError
from research_kb_app.folder_helper_worker import MAX_FRAME_BYTES, PROTOCOL, PURPOSES


HELPER_TIMEOUT_SECONDS = 120
MAX_DIAGNOSTIC_STDERR_BYTES = 4_096
_RESPONSE_KEYS = {"protocol", "status", "request_nonce", "path", "diagnostic_code", "auth_tag"}
_LOGGER = logging.getLogger(__name__)
HelperInvoker = Callable[[dict[str, Any], float], dict[str, Any]]
ProcessFactory = Callable[..., subprocess.Popen[bytes]]


@dataclass(frozen=True, slots=True)
class FolderHelperResult:
    status: str
    path: Path | None


class FolderHelperService:
    def __init__(
        self,
        *,
        helper_invoker: HelperInvoker | None = None,
        process_factory: ProcessFactory = subprocess.Popen,
        timeout_seconds: float = HELPER_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("folder helper timeout must be positive")
        self._invoke = helper_invoker
        self._process_factory = process_factory
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._active: set[subprocess.Popen[bytes]] = set()
        self._closed = False

    def select(
        self,
        *,
        purpose: str,
        allow_existing: bool,
        allow_new_child: bool,
        initial_location_id: str | None,
    ) -> FolderHelperResult:
        if purpose not in PURPOSES:
            raise AppOperationError("RKBAPP-FOLDER-PURPOSE", "Folder helper purpose is invalid", status_code=400)
        secret = secrets.token_hex(32)
        request = {
            "protocol": PROTOCOL,
            "auth_secret": secret,
            "purpose": purpose,
            "allow_existing": allow_existing,
            "allow_new_child": allow_new_child,
            "initial_location_id": initial_location_id,
            "parent_pid": os.getpid(),
            "request_nonce": secrets.token_hex(24),
        }
        with self._lock:
            if self._closed:
                raise AppOperationError("RKBAPP-FOLDER-CLOSED", "Folder selection helper is closed")
        response = (
            self._invoke(request, self.timeout_seconds)
            if self._invoke is not None
            else self._invoke_default(request, self.timeout_seconds)
        )
        _validate_response(response, request=request, secret=secret)
        if response["status"] == "cancelled":
            return FolderHelperResult("cancelled", None)
        if response["status"] != "selected":
            raise AppOperationError("RKBAPP-FOLDER-HELPER", "Folder selection helper failed")
        path = Path(response["path"])
        if not path.is_absolute() or not path.is_dir():
            raise AppOperationError("RKBAPP-FOLDER-RESULT", "Folder selection is unavailable")
        return FolderHelperResult("selected", path.resolve(strict=True))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            processes = tuple(self._active)
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _invoke_default(self, request: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = self._process_factory(
            [sys.executable, "-I", "-m", "research_kb_app.folder_helper_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        with self._lock:
            if self._closed:
                process.terminate()
                process.wait(timeout=5)
                raise AppOperationError("RKBAPP-FOLDER-CLOSED", "Folder selection helper is closed")
            self._active.add(process)
        try:
            try:
                stdout, stderr = process.communicate(_json_bytes(request), timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stdout, stderr = process.communicate()
                _log_helper_diagnostic(process.returncode, stdout, stderr, diagnostic="timeout")
                raise AppOperationError("RKBAPP-FOLDER-TIMEOUT", "Folder selection timed out") from error
        finally:
            with self._lock:
                self._active.discard(process)
        if process.returncode != 0 or not stdout or len(stdout) > MAX_FRAME_BYTES or stdout.count(b"\n") != 1:
            _log_helper_diagnostic(process.returncode, stdout, stderr, diagnostic="invalid_frame")
            raise AppOperationError(
                "RKBAPP-FOLDER-FRAME",
                "Folder selection helper stopped before returning a valid selection; try again",
            )
        try:
            value = json.loads(stdout.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            _log_helper_diagnostic(process.returncode, stdout, stderr, diagnostic="invalid_json")
            raise AppOperationError(
                "RKBAPP-FOLDER-FRAME",
                "Folder selection helper returned invalid data; try again",
            ) from error
        if not isinstance(value, dict):
            _log_helper_diagnostic(process.returncode, stdout, stderr, diagnostic="invalid_object")
            raise AppOperationError(
                "RKBAPP-FOLDER-FRAME",
                "Folder selection helper returned invalid data; try again",
            )
        return value


def _validate_response(response: dict[str, Any], *, request: dict[str, Any], secret: str) -> None:
    if not isinstance(response, dict) or set(response) != _RESPONSE_KEYS:
        raise AppOperationError("RKBAPP-FOLDER-FRAME", "Folder selection helper response is malformed")
    payload = {key: value for key, value in response.items() if key != "auth_tag"}
    expected = hmac.new(bytes.fromhex(secret), _canonical_bytes(payload), hashlib.sha256).hexdigest()
    valid = (
        response["protocol"] == PROTOCOL
        and response["status"] in {"selected", "cancelled", "failure"}
        and response["request_nonce"] == request["request_nonce"]
        and isinstance(response["auth_tag"], str)
        and hmac.compare_digest(response["auth_tag"], expected)
        and ((response["status"] == "selected") == isinstance(response["path"], str))
        and (response["diagnostic_code"] is None or response["diagnostic_code"] == "RKBAPP-FOLDER-HELPER")
    )
    if not valid:
        raise AppOperationError("RKBAPP-FOLDER-AUTH", "Folder selection helper response could not be authenticated")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return _canonical_bytes(value) + b"\n"


def _log_helper_diagnostic(returncode: int | None, stdout: bytes, stderr: bytes, *, diagnostic: str) -> None:
    stderr_sample = stderr[:MAX_DIAGNOSTIC_STDERR_BYTES]
    _LOGGER.warning(
        "folder helper diagnostic=%s return_code=%s stdout_bytes=%d stdout_newlines=%d stderr_bytes=%d stderr_sha256=%s stderr_sample_bytes=%d",
        diagnostic,
        returncode,
        len(stdout),
        stdout.count(b"\n"),
        len(stderr),
        hashlib.sha256(stderr).hexdigest(),
        len(stderr_sample),
    )


__all__ = ["FolderHelperResult", "FolderHelperService", "HELPER_TIMEOUT_SECONDS"]
