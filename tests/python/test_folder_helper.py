from __future__ import annotations

import hashlib
import hmac
import json
import logging
import subprocess
import sys
import threading
import time
import textwrap
from pathlib import Path

import pytest

from research_kb_app.errors import AppOperationError
from research_kb_app.folder_helper import FolderHelperService
from research_kb_app.folder_helper import MAX_DIAGNOSTIC_STDERR_BYTES
from research_kb_app.folder_helper_worker import MAX_FRAME_BYTES, PROTOCOL, execute_request


def _signed_response(request, *, path, status="selected"):
    payload = {
        "protocol": PROTOCOL,
        "status": status,
        "request_nonce": request["request_nonce"],
        "path": path,
        "diagnostic_code": None,
    }
    tag = hmac.new(
        bytes.fromhex(request["auth_secret"]),
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**payload, "auth_tag": tag}


def _worker_request(parent_pid: int) -> dict[str, object]:
    return {
        "protocol": PROTOCOL,
        "auth_secret": "a" * 64,
        "purpose": "source_root",
        "allow_existing": True,
        "allow_new_child": False,
        "initial_location_id": "home",
        "parent_pid": parent_pid,
        "request_nonce": "nonce-0000000000000000",
    }


def _run_isolated_worker(*, parent_pid: int, selector_delay: float, timeout: float) -> tuple[int, bytes, bytes]:
    script = textwrap.dedent(
        f"""
        import time
        import research_kb_app.folder_helper_worker as worker

        def selector(_request):
            time.sleep({selector_delay!r})
            return ""

        worker._select_directory = selector
        raise SystemExit(worker.main())
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        stdout, stderr = child.communicate(
            json.dumps(_worker_request(parent_pid), separators=(",", ":")).encode("utf-8") + b"\n",
            timeout=timeout,
        )
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
    return child.returncode, stdout, stderr


def _sleeping_sentinel() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class _StaticFrameProcess:
    def __init__(self, *, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    def communicate(self, _input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def poll(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class _TimeoutProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None

    def communicate(self, _input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("folder-helper", timeout)
        return b"", b""

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class _RegistrationBarrierSet(set):
    def __init__(self) -> None:
        super().__init__()
        self.add_started = threading.Event()
        self.allow_add = threading.Event()

    def add(self, process) -> None:
        self.add_started.set()
        if not self.allow_add.wait(5):
            raise AssertionError("folder helper registration barrier timed out")
        super().add(process)


class _CloseRaceProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.finished = threading.Event()

    def communicate(self, _input: bytes | None = None, timeout: float | None = None) -> tuple[bytes, bytes]:
        if not self.finished.wait(timeout or 5):
            raise subprocess.TimeoutExpired("folder-helper", timeout)
        return b"", b"synthetic-stderr-secret C:\\synthetic\\private\\folder"

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.finished.set()

    def kill(self) -> None:
        self.returncode = -9
        self.finished.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self.finished.wait(timeout or 5):
            raise subprocess.TimeoutExpired("folder-helper", timeout)
        return self.returncode or 0


def _select_with_process(process) -> None:
    FolderHelperService(process_factory=lambda *_args, **_kwargs: process).select(
        purpose="local_inbox",
        allow_existing=True,
        allow_new_child=False,
        initial_location_id=None,
    )


def test_worker_subprocess_returns_authenticated_cancel_while_parent_is_alive() -> None:
    sentinel = _sleeping_sentinel()
    try:
        returncode, stdout, stderr = _run_isolated_worker(
            parent_pid=sentinel.pid,
            selector_delay=1.25,
            timeout=5,
        )
    finally:
        _stop_process(sentinel)

    assert returncode == 0, {"returncode": returncode, "stdout_bytes": len(stdout), "stderr_bytes": len(stderr)}
    assert stdout.count(b"\n") == 1
    response = json.loads(stdout.decode("utf-8"))
    assert response["status"] == "cancelled"
    assert response["path"] is None
    assert len(response["auth_tag"]) == 64


def test_worker_subprocess_exits_without_frame_when_parent_stops() -> None:
    sentinel = _sleeping_sentinel()
    child = None
    try:
        script = textwrap.dedent(
            """
            import time
            import research_kb_app.folder_helper_worker as worker

            def selector(_request):
                time.sleep(10)
                return ""

            worker._select_directory = selector
            raise SystemExit(worker.main())
            """
        )
        child = subprocess.Popen(
            [sys.executable, "-I", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        payload = json.dumps(_worker_request(sentinel.pid), separators=(",", ":")).encode("utf-8") + b"\n"
        with pytest.raises(subprocess.TimeoutExpired):
            child.communicate(payload, timeout=1.0)
        _stop_process(sentinel)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 5, {"returncode": child.returncode, "stdout_bytes": len(stdout), "stderr_bytes": len(stderr)}
        assert stdout == b""
    finally:
        if child is not None:
            _stop_process(child)
        _stop_process(sentinel)


@pytest.mark.parametrize(
    ("returncode", "stdout"),
    (
        (7, b""),
        (0, b""),
        (0, b"{}\n{}\n"),
        (0, b"x" * (MAX_FRAME_BYTES + 1)),
    ),
    ids=("child_crash", "empty", "multi", "oversize"),
)
def test_default_helper_rejects_child_exit_and_invalid_frame_shapes(
    returncode: int,
    stdout: bytes,
) -> None:
    with pytest.raises(AppOperationError) as caught:
        _select_with_process(_StaticFrameProcess(returncode=returncode, stdout=stdout, stderr=b""))
    assert caught.value.code == "RKBAPP-FOLDER-FRAME"


def test_default_helper_rejects_forged_hmac_at_real_frame_boundary() -> None:
    class ForgedFrameProcess(_StaticFrameProcess):
        def communicate(self, input_bytes: bytes, timeout: float | None = None) -> tuple[bytes, bytes]:
            request = json.loads(input_bytes.decode("utf-8"))
            response = _signed_response(request, path=None, status="cancelled")
            response["auth_tag"] = "0" * 64
            return json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n", b""

    with pytest.raises(AppOperationError) as caught:
        _select_with_process(ForgedFrameProcess(returncode=0, stdout=b"", stderr=b""))
    assert caught.value.code == "RKBAPP-FOLDER-AUTH"


def test_default_helper_timeout_fails_closed() -> None:
    process = _TimeoutProcess()
    service = FolderHelperService(
        process_factory=lambda *_args, **_kwargs: process,
        timeout_seconds=0.01,
    )
    with pytest.raises(AppOperationError) as caught:
        service.select(
            purpose="local_inbox",
            allow_existing=True,
            allow_new_child=False,
            initial_location_id=None,
        )
    assert caught.value.code == "RKBAPP-FOLDER-TIMEOUT"
    assert process.returncode == -15


def test_default_helper_diagnostics_redact_frame_payload_and_bound_stderr_sample(caplog: pytest.LogCaptureFixture) -> None:
    stderr = b"synthetic-secret C:\\synthetic\\private\\folder\n" + b"x" * (MAX_DIAGNOSTIC_STDERR_BYTES + 20)
    with caplog.at_level(logging.WARNING, logger="research_kb_app.folder_helper"):
        with pytest.raises(AppOperationError):
            _select_with_process(_StaticFrameProcess(returncode=9, stdout=b"synthetic-path\n", stderr=stderr))

    message = caplog.records[-1].getMessage()
    assert "synthetic-secret" not in message
    assert "synthetic-path" not in message
    assert "stdout_bytes=15" in message
    assert f"stderr_bytes={len(stderr)}" in message
    assert f"stderr_sample_bytes={MAX_DIAGNOSTIC_STDERR_BYTES}" in message


def test_folder_helper_returns_server_side_path_only_after_authenticated_response(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    service = FolderHelperService(helper_invoker=lambda request, _timeout: _signed_response(request, path=str(selected)))

    result = service.select(
        purpose="local_inbox",
        allow_existing=True,
        allow_new_child=False,
        initial_location_id="documents",
    )

    assert result.status == "selected"
    assert result.path == selected.resolve()


def test_folder_helper_rejects_tampered_nonce_tag_and_extra_fields(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()

    for mutate in (
        lambda response: {**response, "request_nonce": "wrong-nonce"},
        lambda response: {**response, "auth_tag": "0" * 64},
        lambda response: {**response, "extra": True},
    ):
        def invoke(request, _timeout, mutate=mutate):
            return mutate(_signed_response(request, path=str(selected)))

        with pytest.raises(AppOperationError):
            FolderHelperService(helper_invoker=invoke).select(
                purpose="local_inbox",
                allow_existing=True,
                allow_new_child=False,
                initial_location_id=None,
            )


def test_worker_returns_authenticated_cancel_without_path() -> None:
    request = {
        "protocol": PROTOCOL,
        "auth_secret": "a" * 64,
        "purpose": "source_root",
        "allow_existing": True,
        "allow_new_child": False,
        "initial_location_id": "home",
        "parent_pid": 1,
        "request_nonce": "nonce-0000000000000000",
    }
    response = execute_request(request, selector=lambda _request: "")
    assert response["status"] == "cancelled"
    assert response["path"] is None
    assert len(response["auth_tag"]) == 64


def test_close_terminates_active_default_helper_and_rejects_new_selection() -> None:
    class BlockingProcess:
        def __init__(self):
            self.started = threading.Event()
            self.finished = threading.Event()
            self.returncode = None
            self.terminated = False

        def communicate(self, _input, timeout):
            assert timeout == 120
            self.started.set()
            self.finished.wait(5)
            return b"", b""

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15
            self.finished.set()

        def kill(self):
            self.returncode = -9
            self.finished.set()

        def wait(self, timeout):
            assert self.finished.wait(timeout)
            return self.returncode

    process = BlockingProcess()
    service = FolderHelperService(process_factory=lambda *_args, **_kwargs: process)  # type: ignore[arg-type]
    errors = []
    thread = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: service.select(
                purpose="local_inbox",
                allow_existing=True,
                allow_new_child=False,
                initial_location_id=None,
            ),
        )
    )
    thread.start()
    assert process.started.wait(2)

    service.close()
    thread.join(timeout=2)

    assert process.terminated is True
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], AppOperationError)
    with pytest.raises(AppOperationError) as caught:
        service.select(
            purpose="local_inbox",
            allow_existing=True,
            allow_new_child=False,
            initial_location_id=None,
        )
    assert caught.value.code == "RKBAPP-FOLDER-CLOSED"


def test_start_and_close_register_atomically_and_redact_shutdown_diagnostics(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _CloseRaceProcess()
    factory_calls = 0

    def factory(*_args, **_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        return process

    service = FolderHelperService(process_factory=factory)  # type: ignore[arg-type]
    active = _RegistrationBarrierSet()
    service._active = active  # type: ignore[assignment]
    auth_secret = "a" * 64
    request_nonce = "b" * 48
    monkeypatch.setattr(
        "research_kb_app.folder_helper.secrets.token_hex",
        lambda size: auth_secret if size == 32 else request_nonce,
    )
    select_errors: list[Exception] = []
    select_thread = threading.Thread(
        target=lambda: _capture_error(
            select_errors,
            lambda: service.select(
                purpose="local_inbox",
                allow_existing=True,
                allow_new_child=False,
                initial_location_id=None,
            ),
        )
    )
    close_started = threading.Event()
    close_finished = threading.Event()

    def close_service() -> None:
        close_started.set()
        service.close()
        close_finished.set()

    close_thread = threading.Thread(target=close_service)
    with caplog.at_level(logging.WARNING, logger="research_kb_app.folder_helper"):
        select_thread.start()
        assert active.add_started.wait(2)
        close_thread.start()
        assert close_started.wait(2)
        close_missed_registration = close_finished.wait(0.25)
        active.allow_add.set()
        if close_missed_registration:
            process.terminate()
        select_thread.join(timeout=2)
        close_thread.join(timeout=2)

    assert close_missed_registration is False
    assert process.terminated is True
    assert not select_thread.is_alive()
    assert not close_thread.is_alive()
    assert select_errors and isinstance(select_errors[0], AppOperationError)
    assert service._active == set()
    assert factory_calls == 1

    with pytest.raises(AppOperationError) as caught:
        service.select(
            purpose="local_inbox",
            allow_existing=True,
            allow_new_child=False,
            initial_location_id=None,
        )
    assert caught.value.code == "RKBAPP-FOLDER-CLOSED"
    assert factory_calls == 1

    log_output = "\n".join(record.getMessage() for record in caplog.records)
    assert auth_secret not in log_output
    assert request_nonce not in log_output
    assert "C:\\synthetic\\private\\folder" not in log_output
    assert "synthetic-stderr-secret" not in log_output
    assert "stderr_bytes=" in log_output
    assert "stderr_sha256=" in log_output


def _capture_error(errors, invoke) -> None:
    try:
        invoke()
    except Exception as error:
        errors.append(error)
