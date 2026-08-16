from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from research_kb.services import WorkspaceBootstrapService

from conftest import portable_fixture_root
from research_kb_app.compatibility import CompatibilityError
from research_kb_app.errors import AppOperationError
from research_kb_app.launcher import _open_when_ready, build_parser
from research_kb_app.storage import StoragePreflightError


def test_production_launcher_uses_dynamic_loopback_and_keeps_token_out_of_log(tmp_path: Path) -> None:
    fixture = portable_fixture_root()
    workspace = tmp_path / "workspace"
    shutil.copytree(fixture, workspace)
    assert WorkspaceBootstrapService(workspace / "workspace.yaml").run().exit_code == 0
    repository = Path(__file__).resolve().parents[2]
    state_root = tmp_path / "app-state"
    log_root = state_root / "logs"
    config_path = tmp_path / "app-config.json"
    config_path.write_text(
        json.dumps(
            {
                "contract_version": "research-kb-app-config@1.0",
                "workspaces": [
                    {
                        "option_id": "p2-small",
                        "label": "P2 Small Synthetic",
                        "config_path": str((workspace / "workspace.yaml").resolve()),
                    }
                ],
                "state_root": str(state_root.resolve()),
                "log_root": str(log_root.resolve()),
                "frontend_root": str((repository / "web" / "release").resolve()),
                "request_budgets": {
                    "max_body_bytes": 16384,
                    "max_query_bytes": 2048,
                    "max_page_size": 100,
                    "request_timeout_seconds": 30,
                },
            }
        ),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "research_kb_app.launcher", "--config", str(config_path), "--no-browser"],
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        url = _read_value(process, "URL: ")
        token = _read_value(process, "ONE-TIME TOKEN: ")
        log_path = Path(_read_value(process, "LOG: "))
        assert url.startswith("http://127.0.0.1:")
        with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
            _wait_ready(client)
            bootstrap = client.post(
                "/api/session/bootstrap",
                headers={"Origin": url.rstrip("/")},
                json={"startup_token": token},
            )
            assert bootstrap.status_code == 200
            csrf = client.get("/api/session/csrf").json()["csrf_token"]
            shutdown = client.post(
                "/api/shutdown",
                headers={"Origin": url.rstrip("/"), "X-RKB-CSRF": csrf},
                json={},
            )
            assert shutdown.status_code == 200
        assert process.wait(timeout=10) == 0
        assert log_path.is_file()
        assert token not in log_path.read_text(encoding="utf-8")
        assert token not in url
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_default_launch_opens_browser_after_server_is_ready(monkeypatch, tmp_path: Path) -> None:
    parsed = build_parser().parse_args(["--config", str(tmp_path / "app-config.json")])
    automated = build_parser().parse_args(
        ["--config", str(tmp_path / "app-config.json"), "--no-browser"]
    )
    opened: list[str] = []
    monkeypatch.setattr("research_kb_app.launcher.webbrowser.open", opened.append)

    _open_when_ready(SimpleNamespace(started=True), "http://127.0.0.1:43123")

    assert parsed.no_browser is False
    assert automated.no_browser is True
    assert opened == ["http://127.0.0.1:43123"]


def test_launcher_accepts_managed_first_run_and_explicit_profile() -> None:
    first_run = build_parser().parse_args([])
    selected = build_parser().parse_args(["--profile", "review-profile"])

    assert first_run.config is None
    assert first_run.profile is None
    assert selected.config is None
    assert selected.profile == "review-profile"


def test_launcher_defers_feature_specific_core_imports_until_after_identity_check() -> None:
    from research_kb_app import launcher

    assert "SetupRuntime" not in launcher.__dict__


def test_launcher_rejects_config_and_profile_together(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["--config", str(tmp_path / "app-config.json"), "--profile", "default"]
        )


def test_storage_refusal_precedes_directory_and_listener_side_effects(monkeypatch, tmp_path: Path) -> None:
    from research_kb_app import launcher

    compatibility = object()
    config = SimpleNamespace(
        path=tmp_path / "config.json",
        state_root=tmp_path / "state",
        log_root=tmp_path / "logs",
    )
    calls: list[str] = []
    monkeypatch.setattr(launcher, "load_compatibility", lambda _path: compatibility)
    monkeypatch.setattr(launcher, "verify_installed_core", lambda _value: calls.append("identity"))
    monkeypatch.setattr(launcher, "load_app_config", lambda _path: config)

    def refuse_storage(_config) -> None:
        calls.append("storage")
        raise StoragePreflightError("App storage must be local NTFS")

    monkeypatch.setattr(launcher, "preflight_storage", refuse_storage)
    monkeypatch.setattr(launcher, "_ensure_directory", lambda _path: calls.append("directory"))
    monkeypatch.setattr(launcher, "create_app", lambda *_args, **_kwargs: calls.append("app"))
    monkeypatch.setattr(launcher.socket, "socket", lambda *_args, **_kwargs: calls.append("listener"))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda *_args: calls.append("browser"))

    assert launcher.main(["--config", str(tmp_path / "config.json"), "--no-browser"]) == 2
    assert calls == ["identity", "storage"]
    assert not config.state_root.exists()
    assert not config.log_root.exists()


def test_profile_root_refusal_precedes_profile_layout_and_listener_side_effects(
    monkeypatch, tmp_path: Path
) -> None:
    from research_kb_app import launcher

    calls: list[str] = []
    compatibility = object()
    monkeypatch.setattr(launcher, "load_compatibility", lambda _path: compatibility)
    monkeypatch.setattr(launcher, "verify_installed_core", lambda _value: calls.append("identity"))

    def refuse_profile(_profile_id, _root_security) -> Path:
        calls.append("profile-root")
        raise AppOperationError("RKBAPP-PROFILE-ROOT", "Managed profile root is not secure")

    monkeypatch.setattr(launcher, "ensure_managed_profile_root", refuse_profile)
    monkeypatch.setattr(launcher, "preflight_storage", lambda _config: calls.append("storage"))
    monkeypatch.setattr(launcher, "_ensure_directory", lambda _path: calls.append("directory"))
    monkeypatch.setattr(launcher, "create_app", lambda *_args, **_kwargs: calls.append("app"))
    monkeypatch.setattr(launcher.socket, "socket", lambda *_args, **_kwargs: calls.append("listener"))

    assert launcher.main(["--profile", "default", "--no-browser"]) == 2
    assert calls == ["identity", "profile-root"]


@pytest.mark.parametrize(
    "diagnostic",
    [
        "Installed Research KB Core package payload is stale or incompatible",
        "Installed Core dependency profile is stale or incompatible",
        "Installed Core capability profile is stale or incompatible",
    ],
)
def test_identity_refusal_precedes_config_session_and_io_side_effects(
    monkeypatch, tmp_path: Path, diagnostic: str
) -> None:
    from research_kb_app import launcher

    calls: list[str] = []
    compatibility = object()
    monkeypatch.setattr(launcher, "load_compatibility", lambda _path: compatibility)

    def refuse_identity(_value) -> None:
        calls.append("identity")
        raise CompatibilityError(diagnostic)

    monkeypatch.setattr(launcher, "verify_installed_core", refuse_identity)
    monkeypatch.setattr(launcher, "load_app_config", lambda _path: calls.append("config"))
    monkeypatch.setattr(launcher, "preflight_storage", lambda _config: calls.append("storage"))
    monkeypatch.setattr(launcher, "_ensure_directory", lambda _path: calls.append("directory"))
    monkeypatch.setattr(launcher, "create_app", lambda *_args, **_kwargs: calls.append("app"))
    monkeypatch.setattr(launcher.socket, "socket", lambda *_args, **_kwargs: calls.append("listener"))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda *_args: calls.append("browser"))

    assert launcher.main(["--config", str(tmp_path / "config.json")]) == 2
    assert calls == ["identity"]


def _read_value(process: subprocess.Popen[str], prefix: str) -> str:
    assert process.stdout is not None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
        if process.poll() is not None:
            break
    raise AssertionError(f"launcher did not emit {prefix.strip()}")


def _wait_ready(client: httpx.Client) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if client.get("/api/runtime").status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise AssertionError("launcher did not become ready")
