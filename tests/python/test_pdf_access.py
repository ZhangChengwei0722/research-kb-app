from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from research_kb.errors import Diagnostic, ResearchKBError
from research_kb.services import OpenedEvidenceSource, PreparedEvidenceSource

from conftest import EXPECTED_ORIGIN, AppHarness, synthetic_pdf_bytes, tree_digest
from research_kb_app.errors import AppOperationError, public_core_error
from research_kb_app.external_reader import ExternalReaderLauncher
from research_kb_app.pdf_access import PdfHandleRegistry, RangeNotSatisfiable, resolve_byte_range


EVIDENCE_ID = "evidence_20cbe39d-3cba-4ba8-980f-bc6399026bf6"


@pytest.mark.parametrize(
    ("code", "status", "message"),
    [
        ("RKBC-014", 409, "source changed"),
        ("RKBC-029", 415, "supported PDF"),
        ("RKBC-030", 413, "size budget"),
    ],
)
def test_pdf_core_errors_have_bounded_public_mapping(
    code: str,
    status: int,
    message: str,
) -> None:
    error = ResearchKBError(
        Diagnostic(
            code=code,
            record_kind="source",
            record_id="source-private",
            json_path="/",
            message="private internal message",
        )
    )

    actual_status, actual_code, public_message = public_core_error(error)

    assert (actual_status, actual_code) == (status, code)
    assert message in public_message
    assert "private internal message" not in public_message


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_pdf_handle_registry_binds_session_workspace_ttl_and_cap() -> None:
    clock = MutableClock()
    tokens = iter(f"opaque-{index}" for index in range(20))
    registry = PdfHandleRegistry(clock=clock, token_factory=lambda: next(tokens))

    issued = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-a",
        core_handle=object(),
        descriptor={"evidence_id": EVIDENCE_ID, "pdf_page": 2},
    )

    assert issued == {
        "status": "success",
        "handle_id": "opaque-0",
        "evidence_id": EVIDENCE_ID,
        "pdf_page": 2,
        "expires_in_seconds": 900,
    }
    entry = registry.require("opaque-0", "browser-a", "workspace-a")
    assert entry.descriptor["evidence_id"] == EVIDENCE_ID
    with pytest.raises(AppOperationError) as wrong_session:
        registry.require("opaque-0", "browser-b", "workspace-a")
    assert wrong_session.value.status_code == 404

    for index in range(1, 16):
        registry.issue(
            browser_session_id="browser-a",
            workspace_option_id="workspace-a",
            core_handle=object(),
            descriptor={"evidence_id": f"evidence-{index}", "pdf_page": 1},
        )
    with pytest.raises(AppOperationError) as capped:
        registry.issue(
            browser_session_id="browser-a",
            workspace_option_id="workspace-a",
            core_handle=object(),
            descriptor={"evidence_id": "evidence-over-cap", "pdf_page": 1},
        )
    assert capped.value.status_code == 429

    clock.value += 901
    with pytest.raises(AppOperationError) as expired:
        registry.require("opaque-0", "browser-a", "workspace-a")
    assert expired.value.status_code == 410
    registry.clear()
    assert registry.count == 0


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, (0, 9, 200)),
        ("bytes=2-5", (2, 5, 206)),
        ("bytes=6-", (6, 9, 206)),
        ("bytes=-3", (7, 9, 206)),
        ("bytes=0-99", (0, 9, 206)),
    ],
)
def test_single_range_resolution(header: str | None, expected: tuple[int, int, int]) -> None:
    resolved = resolve_byte_range(header, 10)

    assert (resolved.start, resolved.end, resolved.status_code) == expected
    assert resolved.length == resolved.end - resolved.start + 1


@pytest.mark.parametrize(
    "header",
    ["items=0-1", "bytes=", "bytes=0-1,3-4", "bytes=8-2", "bytes=10-", "bytes=-0"],
)
def test_invalid_or_multiple_range_is_not_satisfiable(header: str) -> None:
    with pytest.raises(RangeNotSatisfiable) as rejected:
        resolve_byte_range(header, 10)

    assert rejected.value.total_size == 10


def test_external_reader_uses_trusted_updf_then_system_without_shell(tmp_path: Path) -> None:
    pdf = (tmp_path / "source.pdf").resolve()
    pdf.write_bytes(synthetic_pdf_bytes())
    updf = Path("C:/Program Files (x86)/UPDF/UPDF.exe")
    launched: list[tuple[list[str], bool]] = []
    system_opened: list[Path] = []
    launcher = ExternalReaderLauncher(
        platform="win32",
        path_exists=lambda path: path == updf,
        process_launcher=lambda args, **kwargs: launched.append((list(args), kwargs["shell"])),
        system_opener=lambda path: system_opened.append(path),
    )

    result = launcher.launch(pdf)

    assert result.reader == "updf"
    assert launched == [([str(updf), str(pdf)], False)]
    assert system_opened == []

    fallback = ExternalReaderLauncher(
        platform="win32",
        path_exists=lambda _: False,
        process_launcher=lambda *_args, **_kwargs: pytest.fail("process launcher was used"),
        system_opener=lambda path: system_opened.append(path),
    ).launch(pdf)
    assert fallback.reader == "system"
    assert system_opened == [pdf]


class FakeReadingService:
    def __init__(self, pdf_path: Path, payload: bytes):
        self.pdf_path = pdf_path
        self.payload = payload
        self.open_calls = 0

    def prepare_evidence_source(self, _session, evidence_id: str) -> PreparedEvidenceSource:
        return PreparedEvidenceSource(
            handle=SimpleNamespace(evidence_id=evidence_id),
            descriptor={
                "status": "success",
                "interface_version": "1.0",
                "application_service_interface_version": "1.8",
                "evidence_id": evidence_id,
                "paper_id": "paper-synthetic",
                "pdf_page": 1,
                "locator": "page:1:block:1",
                "media_type": "application/pdf",
                "size_bytes": len(self.payload),
                "source_currentness": "current",
                "persistent_writes": 0,
                "canonical_scientific_write": False,
            },
        )

    def open_evidence_source(self, _session, _handle) -> OpenedEvidenceSource:
        self.open_calls += 1
        return OpenedEvidenceSource(
            stream=io.BytesIO(self.payload),
            path=self.pdf_path,
            size_bytes=len(self.payload),
            pdf_page=1,
            locator="page:1:block:1",
        )


class FakeLauncher:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def launch(self, path: Path):
        self.paths.append(path)
        return SimpleNamespace(reader="updf", page_targeting="manual")


def test_pdf_http_routes_are_session_bound_ranged_revalidated_and_zero_write(
    app_harness: AppHarness,
    tmp_path: Path,
) -> None:
    payload = synthetic_pdf_bytes("Synthetic Evidence PDF interaction.")
    source = (tmp_path / "evidence-source.pdf").resolve()
    source.write_bytes(payload)
    unauthenticated = app_harness.client.post(
        f"/api/reading/evidence/{EVIDENCE_ID}/source-handle",
        headers={"Origin": EXPECTED_ORIGIN},
        json={},
    )
    assert unauthenticated.status_code == 401

    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    fake_reading = FakeReadingService(source, payload)
    fake_launcher = FakeLauncher()
    runtime.reading = fake_reading
    runtime.pdf_launcher = fake_launcher
    before = tree_digest(app_harness.workspace_root / "knowledge")

    missing_csrf = app_harness.client.post(
        f"/api/reading/evidence/{EVIDENCE_ID}/source-handle",
        headers={"Origin": EXPECTED_ORIGIN},
        json={},
    )
    wrong_origin = app_harness.client.post(
        f"/api/reading/evidence/{EVIDENCE_ID}/source-handle",
        headers={"Origin": "http://attacker.invalid", "X-RKB-CSRF": csrf},
        json={},
    )
    path_shaped_evidence = app_harness.client.post(
        "/api/reading/evidence/evidence_../source-handle",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    path_shaped_handle = app_harness.client.get("/api/reading/pdf/opaque..handle")
    assert missing_csrf.status_code == 401
    assert wrong_origin.status_code == 403
    assert path_shaped_evidence.status_code == 400
    assert path_shaped_handle.status_code == 400

    issued = app_harness.client.post(
        f"/api/reading/evidence/{EVIDENCE_ID}/source-handle",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    assert issued.status_code == 200, issued.text
    handle_id = issued.json()["handle_id"]
    assert str(source) not in issued.text

    full = app_harness.client.get(f"/api/reading/pdf/{handle_id}")
    partial = app_harness.client.get(
        f"/api/reading/pdf/{handle_id}",
        headers={"Range": "bytes=5-14"},
    )
    suffix = app_harness.client.get(
        f"/api/reading/pdf/{handle_id}",
        headers={"Range": "bytes=-8"},
    )
    head = app_harness.client.head(f"/api/reading/pdf/{handle_id}")
    invalid = app_harness.client.get(
        f"/api/reading/pdf/{handle_id}",
        headers={"Range": "bytes=0-1,3-4"},
    )
    external_missing_csrf = app_harness.client.post(
        f"/api/reading/pdf/{handle_id}/open",
        headers={"Origin": EXPECTED_ORIGIN},
        json={},
    )
    opened = app_harness.client.post(
        f"/api/reading/pdf/{handle_id}/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )

    assert full.status_code == 200 and full.content == payload
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["cache-control"] == "no-store"
    assert partial.status_code == 206 and partial.content == payload[5:15]
    assert partial.headers["content-range"] == f"bytes 5-14/{len(payload)}"
    assert suffix.status_code == 206 and suffix.content == payload[-8:]
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(len(payload))
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == f"bytes */{len(payload)}"
    assert external_missing_csrf.status_code == 401
    assert opened.json() == {
        "status": "success",
        "reader": "updf",
        "page_targeting": "manual",
        "pdf_page": 1,
        "locator": "page:1:block:1",
    }
    assert fake_launcher.paths == [source]
    assert fake_reading.open_calls == 6
    assert str(source) not in "\n".join(
        [full.text, partial.text, suffix.text, head.text, invalid.text, opened.text]
    )
    assert tree_digest(app_harness.workspace_root / "knowledge") == before
    assert runtime.pdf_handles.count == 1

    reopened = app_harness.client.post(
        "/api/workspaces/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"option_id": "p2-small"},
    )
    assert reopened.status_code == 200
    assert runtime.pdf_handles.count == 0
    assert app_harness.client.get(f"/api/reading/pdf/{handle_id}").status_code == 404
