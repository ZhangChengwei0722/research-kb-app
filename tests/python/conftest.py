from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from research_kb.services import WorkspaceBootstrapService

from research_kb_app.api import create_app
from research_kb_app.compatibility import load_compatibility, verify_installed_core
from research_kb_app.config import AppConfig, load_app_config
from tools.public_fixture_contract import (
    FixtureContractError,
    default_fixture_root,
    sha256_path_nul_content_v1,
    verify_fixture,
)


STARTUP_TOKEN = "p2c-test-token-000000000000000000000000"
EXPECTED_HOST = "testserver"
EXPECTED_ORIGIN = "http://testserver"


@pytest.fixture(autouse=True)
def fail_closed_external_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailClosedExternalReaderLauncher:
        def launch(self, _path: Path):
            raise AssertionError("External PDF reader launch is forbidden in App API tests")

    monkeypatch.setattr(
        "research_kb_app.runtime.ExternalReaderLauncher",
        FailClosedExternalReaderLauncher,
    )


@dataclass(slots=True)
class AppHarness:
    config: AppConfig
    client: TestClient
    workspace_root: Path

    def bootstrap(self) -> str:
        response = self.client.post(
            "/api/session/bootstrap",
            headers={"Origin": EXPECTED_ORIGIN},
            json={"startup_token": STARTUP_TOKEN},
        )
        assert response.status_code == 200
        csrf = self.client.get("/api/session/csrf")
        assert csrf.status_code == 200
        return csrf.json()["csrf_token"]

    def open_workspace(self) -> str:
        csrf = self.bootstrap()
        response = self.client.post(
            "/api/workspaces/open",
            headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
            json={"option_id": "p2-small"},
        )
        assert response.status_code == 200, response.text
        return csrf

    def wait_until_idle(self, timeout: float = 20) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.client.get("/api/health")
            assert response.status_code == 200, response.text
            health = response.json()
            if health["operation"]["state"] not in {"running", "building"}:
                return health
            time.sleep(0.05)
        raise AssertionError("App operation did not become idle")


@pytest.fixture
def app_harness(tmp_path: Path) -> AppHarness:
    fixture = portable_fixture_root()
    workspace_root = tmp_path / "workspace"
    shutil.copytree(fixture, workspace_root)
    workspace_config = workspace_root / "workspace.yaml"
    workspace_payload = yaml.safe_load(workspace_config.read_text(encoding="utf-8"))
    workspace_payload["workspace"]["local_inbox"] = "./sources/inbox"
    workspace_config.write_text(
        yaml.safe_dump(workspace_payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    (workspace_root / "sources" / "inbox").mkdir(parents=True, exist_ok=True)
    bootstrap = WorkspaceBootstrapService(workspace_root / "workspace.yaml").run()
    assert bootstrap.exit_code == 0
    frontend_root = tmp_path / "frontend"
    frontend_root.mkdir()
    (frontend_root / "index.html").write_text("<!doctype html><title>test</title>\n", encoding="utf-8")
    config_path = tmp_path / "app-config.json"
    config_path.write_text(
        json.dumps(
            {
                "contract_version": "research-kb-app-config@1.0",
                "workspaces": [
                    {
                        "option_id": "p2-small",
                        "label": "P2 Small Synthetic",
                        "config_path": str((workspace_root / "workspace.yaml").resolve()),
                    }
                ],
                "state_root": str((tmp_path / "app-state").resolve()),
                "log_root": str((tmp_path / "app-state" / "logs").resolve()),
                "frontend_root": str(frontend_root.resolve()),
                "request_budgets": {
                    "max_body_bytes": 4096,
                    "max_query_bytes": 1024,
                    "max_page_size": 100,
                    "request_timeout_seconds": 30,
                },
            }
        ),
        encoding="utf-8",
    )
    compatibility = load_compatibility()
    verify_installed_core(compatibility)
    config = load_app_config(config_path)
    app = create_app(
        config,
        compatibility,
        startup_token=STARTUP_TOKEN,
        expected_host=EXPECTED_HOST,
        expected_origin=EXPECTED_ORIGIN,
    )
    with TestClient(app, base_url=EXPECTED_ORIGIN) as client:
        yield AppHarness(config, client, workspace_root)


def tree_digest(root: Path) -> str:
    return sha256_path_nul_content_v1(root)


def synthetic_pdf_bytes(text: str = "Synthetic App intake text.") -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, item in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(item)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def portable_fixture_root() -> Path:
    configured = os.environ.get("RKB_P2_SMALL_FIXTURE")
    candidate = Path(configured) if configured else default_fixture_root()
    try:
        return verify_fixture(candidate).root
    except FixtureContractError as exc:
        raise AssertionError(f"p2-small fixture verification failed: {exc}") from exc
