from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

import yaml
from research_kb.services import WorkspaceBootstrapService

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools.public_fixture_contract import (  # noqa: E402
    sha256_path_nul_content_v1,
    verify_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    verification = verify_fixture(args.fixture)
    workspace = args.target / "workspace"
    shutil.copytree(verification.root, workspace)
    workspace_config = workspace / "workspace.yaml"
    workspace_payload = yaml.safe_load(workspace_config.read_text(encoding="utf-8"))
    workspace_payload["workspace"]["local_inbox"] = "./sources/inbox"
    workspace_config.write_text(
        yaml.safe_dump(workspace_payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    (workspace / "sources" / "inbox").mkdir(parents=True, exist_ok=True)
    if WorkspaceBootstrapService(workspace / "workspace.yaml").run().exit_code != 0:
        raise RuntimeError("synthetic workspace bootstrap failed")
    frontend_root = Path(str(resources.files("research_kb_app").joinpath("web_dist")))
    if not (frontend_root / "index.html").is_file():
        raise RuntimeError("installed production frontend is unavailable")
    packaged_assets = {
        path.relative_to(frontend_root / "assets").as_posix()
        for path in (frontend_root / "assets").rglob("*")
        if path.is_file()
    }
    referenced_assets = _reachable_assets(frontend_root, packaged_assets)
    if referenced_assets != packaged_assets:
        raise RuntimeError("installed production frontend contains missing or unreferenced assets")
    state_root = args.target / "app-state"
    config_path = args.target / "app-config.json"
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
                "log_root": str((state_root / "logs").resolve()),
                "frontend_root": str(frontend_root.resolve()),
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
        [
            sys.executable,
            str(Path(__file__).with_name("e2e_launcher.py")),
            "--config",
            str(config_path),
            "--no-browser",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        url = _read_value(process, "URL: ")
        token = _read_value(process, "ONE-TIME TOKEN: ")
        log_path = Path(_read_value(process, "LOG: "))
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        _wait_ready(opener, url)
        origin = url.rstrip("/")
        _json_request(
            opener,
            origin + "/api/session/bootstrap",
            {"startup_token": token},
            headers={"Origin": origin},
        )
        csrf = _json_request(opener, origin + "/api/session/csrf")["csrf_token"]
        _json_request(
            opener,
            origin + "/api/workspaces/open",
            {"option_id": "p2-small"},
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        upload = _multipart_request(
            opener,
            origin + "/api/intake/upload",
            metadata={
                "idempotency_key": "installed-wheel-upload-1",
                "requested_operation": "basic_paper_card",
                "document_route": "primary",
                "route_reason": None,
                "bibliography": {
                    "title": "Synthetic installed App intake",
                    "authors": [],
                    "year": 2026,
                    "doi": None,
                },
            },
            pdf=_synthetic_pdf_bytes(),
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        if upload["status"] != "accepted":
            raise RuntimeError("installed App upload was not accepted")
        health = _wait_operation(opener, origin)
        if health["projection_state"] != "current":
            raise RuntimeError("installed App Catalog did not refresh after intake")
        jobs = _json_request(opener, origin + "/api/intake/jobs?page_size=10")
        if len(jobs["jobs"]) != 1:
            raise RuntimeError("installed App intake did not create exactly one Job")
        job = jobs["jobs"][0]
        if (
            job["status"] != "waiting_user"
            or job["current_node"] != "trusted_parse_authority_primary"
            or job["wait_reason"] != "authority_required"
        ):
            raise RuntimeError("installed App intake did not stop at trusted Parse authority")
        preparation = _json_request(
            opener,
            origin + f"/api/intake/jobs/{job['job_id']}/trusted-parse/prepare",
            {
                "expected_state_id": job["state_id"],
                "expected_state_digest": job["state_digest"],
            },
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        if preparation["persistent_writes"] != 0:
            raise RuntimeError("installed App trusted Parse prepare performed a persistent write")
        source = preparation.get("source")
        if not isinstance(source, dict) or source.get("identity_status") != "current":
            raise RuntimeError("installed App trusted Parse source identity is not current")
        if preparation["allowed_operation"] != "parse_run":
            raise RuntimeError("installed App trusted Parse operation is not parse_run")
        lease_token = preparation.get("lease_token")
        aggregate_preview_digest = preparation.get("aggregate_preview_digest")
        if (
            not isinstance(lease_token, str)
            or not lease_token
            or not isinstance(aggregate_preview_digest, str)
            or not aggregate_preview_digest
        ):
            raise RuntimeError("installed App trusted Parse prepare omitted opaque approval facts")
        approved = _json_request(
            opener,
            origin + f"/api/intake/jobs/{job['job_id']}/trusted-parse/approve",
            {
                "lease_token": lease_token,
                "aggregate_preview_digest": aggregate_preview_digest,
            },
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        if approved["status"] != "accepted":
            raise RuntimeError("installed App trusted Parse approval was not accepted")
        completed_health = _wait_operation(opener, origin)
        if completed_health["projection_state"] != "current":
            raise RuntimeError("installed App Catalog did not refresh after trusted Parse")
        jobs = _json_request(opener, origin + "/api/intake/jobs?page_size=10")
        if (
            len(jobs["jobs"]) != 1
            or jobs["jobs"][0]["current_node"] != "primary_semantic_gate"
            or jobs["jobs"][0]["status"] != "completed"
        ):
            raise RuntimeError("installed App intake did not complete the primary semantic gate")
        rendered = json.dumps({"upload": upload, "health": completed_health, "jobs": jobs})
        if any(
            value in rendered
            for value in ("source_ref", "source_fingerprint", "relative_path", "root_id", "local.pdf")
        ):
            raise RuntimeError("installed App response exposed source authority")
        if lease_token in rendered or aggregate_preview_digest in rendered:
            raise RuntimeError("installed App response exposed trusted Parse approval facts")

        uncertain_upload = _multipart_request(
            opener,
            origin + "/api/intake/upload",
            metadata={
                "idempotency_key": "installed-wheel-upload-2",
                "requested_operation": "basic_review_memory",
                "document_route": "review",
                "route_reason": None,
                "bibliography": {
                    "title": "Synthetic installed App uncertain review",
                    "authors": [],
                    "year": 2026,
                    "doi": None,
                },
            },
            pdf=_synthetic_uncertain_pdf_bytes(),
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        if uncertain_upload["status"] != "accepted":
            raise RuntimeError("installed App uncertain review upload was not accepted")
        _wait_operation(opener, origin)
        jobs = _json_request(opener, origin + "/api/intake/jobs?page_size=10")
        uncertain_job = next(
            item for item in jobs["jobs"]
            if item["current_node"] == "trusted_parse_authority_review"
        )
        uncertain_preparation = _json_request(
            opener,
            origin + f"/api/intake/jobs/{uncertain_job['job_id']}/trusted-parse/prepare",
            {
                "expected_state_id": uncertain_job["state_id"],
                "expected_state_digest": uncertain_job["state_digest"],
            },
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        _json_request(
            opener,
            origin + f"/api/intake/jobs/{uncertain_job['job_id']}/trusted-parse/approve",
            {
                "lease_token": uncertain_preparation["lease_token"],
                "aggregate_preview_digest": uncertain_preparation["aggregate_preview_digest"],
            },
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        _wait_operation(opener, origin)
        jobs = _json_request(opener, origin + "/api/intake/jobs?page_size=10")
        uncertain_job = next(
            item for item in jobs["jobs"]
            if item["current_node"] == "source_adequacy"
        )
        if (
            uncertain_job["status"] != "waiting_source"
            or uncertain_job["wait_reason"] != "source_incomplete"
        ):
            raise RuntimeError("installed App uncertainty did not stop at Source Adequacy")
        parsed_root = workspace / "knowledge" / "parsed"
        parsed_root.mkdir(parents=True, exist_ok=True)
        parsed_before_resolution = _tree_digest(parsed_root)
        context = _json_request(
            opener,
            origin + f"/api/intake/jobs/{uncertain_job['job_id']}/source-adequacy-resolution",
        )
        if (
            context["resolution_state"] != "review_required"
            or context["required_capability"] != "basic_paper_understanding"
            or context["hard_failure"] is not False
        ):
            raise RuntimeError("installed App Source Adequacy context is not resolvable")
        expected_state = {
            "expected_state_id": uncertain_job["state_id"],
            "expected_state_digest": uncertain_job["state_digest"],
        }
        opened = _json_request(
            opener,
            origin + f"/api/intake/jobs/{uncertain_job['job_id']}/source-adequacy-resolution/open",
            expected_state,
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        confirmation_id = opened.get("confirmation", {}).get("confirmation_id")
        if not isinstance(confirmation_id, str) or len(confirmation_id) < 32:
            raise RuntimeError("installed App source review omitted its opaque confirmation")
        decision = _json_request(
            opener,
            origin + f"/api/intake/jobs/{uncertain_job['job_id']}/source-adequacy-resolution/decide",
            {
                **expected_state,
                "action": "accept_uncertainty",
                "confirmation_id": confirmation_id,
            },
            headers={"Origin": origin, "X-RKB-CSRF": str(csrf)},
        )
        if decision["resolution_state"] != "continued":
            raise RuntimeError("installed App Source Adequacy continuation did not complete")
        _wait_operation(opener, origin)
        jobs = _json_request(opener, origin + "/api/intake/jobs?page_size=10")
        completed_review = next(
            item for item in jobs["jobs"]
            if item["job_id"] == uncertain_job["job_id"]
        )
        if (
            completed_review["status"] != "completed"
            or completed_review["current_node"] != "review_semantic_gate"
        ):
            raise RuntimeError("installed App review did not reach its semantic gate")
        if _tree_digest(parsed_root) != parsed_before_resolution:
            raise RuntimeError("installed App Source Adequacy continuation invoked a second Parse")
        resolution_rendered = json.dumps(
            {
                "upload": uncertain_upload,
                "context": context,
                "opened": opened,
                "decision": decision,
                "job": completed_review,
            }
        )
        if any(
            value in resolution_rendered
            for value in (
                "source_ref",
                "source_fingerprint",
                "relative_path",
                "root_id",
                "ignored.pdf",
                "pdfplumber",
                "trusted_parse_receipt",
            )
        ):
            raise RuntimeError("installed App Source Adequacy response exposed private authority")
        spool = state_root / "upload-spool"
        if spool.exists() and list(spool.iterdir()):
            raise RuntimeError("installed App leaked its upload spool")
        _json_request(
            opener,
            origin + "/api/shutdown",
            {},
            headers={"Origin": origin, "X-RKB-CSRF": csrf},
        )
        if process.wait(timeout=10) != 0:
            raise RuntimeError("installed launcher did not stop cleanly")
        if token in log_path.read_text(encoding="utf-8") or token in url:
            raise RuntimeError("startup token escaped its console-only boundary")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
    print(
        json.dumps(
            {
                "status": "success",
                "installed_frontend": True,
                "dynamic_loopback": True,
                "deterministic_intake": True,
            }
        )
    )
    return 0


def _reachable_assets(frontend_root: Path, packaged_assets: set[str]) -> set[str]:
    index_content = (frontend_root / "index.html").read_text(encoding="utf-8")
    pending = {
        match.removeprefix("/assets/")
        for match in re.findall(r'(?:src|href)="(/assets/[^"]+)"', index_content)
    }
    reachable: set[str] = set()
    while pending:
        asset = pending.pop()
        if asset in reachable:
            continue
        if asset not in packaged_assets:
            raise RuntimeError("installed production frontend references a missing asset")
        reachable.add(asset)
        if Path(asset).suffix.lower() not in {".css", ".html", ".js", ".mjs"}:
            continue
        content = (frontend_root / "assets" / asset).read_text(encoding="utf-8")
        pending.update(
            match.removeprefix("/assets/")
            for match in re.findall(r"/assets/[A-Za-z0-9._/-]+", content)
            if match.removeprefix("/assets/") not in reachable
        )
    return reachable


def _json_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    payload: dict[str, object] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = dict(headers or {})
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method="POST" if data is not None else "GET")
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _multipart_request(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    metadata: dict[str, object],
    pdf: bytes,
    headers: dict[str, str],
) -> dict[str, object]:
    boundary = "research-kb-installed-wheel"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="metadata"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            json.dumps(metadata, separators=(",", ":")).encode("utf-8"),
            b"\r\n",
            f"--{boundary}\r\n".encode("ascii"),
            b'Content-Disposition: form-data; name="file"; filename="ignored.pdf"\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            pdf,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    request_headers = {
        **headers,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    request = urllib.request.Request(url, data=body, headers=request_headers, method="POST")
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _wait_operation(opener: urllib.request.OpenerDirector, origin: str) -> dict[str, object]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        health = _json_request(opener, origin + "/api/health")
        if health["operation"]["state"] not in {"running", "building"}:
            return health
        time.sleep(0.05)
    raise RuntimeError("installed App intake did not finish")


def _synthetic_pdf_bytes() -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (Synthetic installed App intake.) Tj ET"
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


def _synthetic_uncertain_pdf_bytes() -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (Synthetic installed uncertain review.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>"
        ),
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Length 0 >>\nstream\n\nendstream",
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


def _tree_digest(root: Path) -> str:
    return sha256_path_nul_content_v1(root)


def _wait_ready(opener: urllib.request.OpenerDirector, url: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            _json_request(opener, url.rstrip("/") + "/api/runtime")
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    raise RuntimeError("installed launcher did not become ready")


def _read_value(process: subprocess.Popen[str], prefix: str) -> str:
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line.startswith(prefix):
        raise RuntimeError("installed launcher output contract changed")
    return line[len(prefix) :].strip()


if __name__ == "__main__":
    raise SystemExit(main())
