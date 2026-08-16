from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time

from conftest import EXPECTED_ORIGIN, AppHarness, synthetic_pdf_bytes
from research_kb.errors import OPERATION_CANCELLED, Diagnostic, ResearchKBError
from research_kb.services import PipelineJobService, TrustedParseIntakeApplicationService
from test_source_adequacy_resolution_api import FakeReaderLauncher


def _metadata(*, key: str, route: str | None = "primary", operation: str = "basic_paper_card") -> dict:
    return {
        "idempotency_key": key,
        "requested_operation": operation,
        "document_route": route,
        "route_reason": None,
        "bibliography": {"title": "Synthetic intake", "authors": [], "year": 2026, "doi": None},
    }


def _upload(harness: AppHarness, csrf: str, metadata: dict, *, extra: bool = False):
    files = [
        ("file", ("../../local.pdf", synthetic_pdf_bytes(), "application/pdf")),
        ("metadata", (None, json.dumps(metadata), "application/json")),
    ]
    if extra:
        files.append(("unexpected", (None, "x", "text/plain")))
    return harness.client.post(
        "/api/intake/upload",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        files=files,
    )


def _upload_pending_intake_job(
    harness: AppHarness,
    csrf: str,
    *,
    key: str,
) -> dict:
    before = {
        item["job_id"]
        for item in harness.client.get(
            "/api/intake/jobs",
            params={"page_size": 100},
        ).json()["jobs"]
    }
    uploaded = _upload(harness, csrf, _metadata(key=key))
    assert uploaded.status_code == 202, uploaded.text
    harness.wait_until_idle()
    after = harness.client.get(
        "/api/intake/jobs",
        params={"page_size": 100},
    )
    assert after.status_code == 200, after.text
    created = [item for item in after.json()["jobs"] if item["job_id"] not in before]
    assert len(created) == 1
    return created[0]


def _uncertain_pdf_bytes() -> bytes:
    stream = b"BT /F1 12 Tf 72 720 Td (Synthetic uncertain review text.) Tj ET"
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


def _prepare_and_approve(harness: AppHarness, csrf: str, job: dict) -> dict:
    prepared = harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/prepare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
        },
    )
    assert prepared.status_code == 200, prepared.text
    preview = prepared.json()
    assert preview["persistent_writes"] == 0
    assert preview["source"]["identity_status"] == "current"
    assert preview["allowed_operation"] == "parse_run"
    approved = harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/approve",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "lease_token": preview["lease_token"],
            "aggregate_preview_digest": preview["aggregate_preview_digest"],
        },
    )
    assert approved.status_code == 202, approved.text
    harness.wait_until_idle()
    return preview


def _scientific_output_digests(harness: AppHarness) -> dict[str, str]:
    from conftest import tree_digest

    knowledge = harness.workspace_root / "knowledge"
    return {
        name: tree_digest(knowledge / name)
        for name in (
            "paper_cards",
            "evidence",
            "review_memories",
            "review_queue",
            "questions",
            "step7",
        )
    }


def test_upload_job_list_detail_and_redaction(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    scientific_before = _scientific_output_digests(app_harness)
    accepted = _upload(app_harness, csrf, _metadata(key="api-upload-1"))

    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["operation"]["category"] == "intake"
    app_harness.wait_until_idle()
    listed = app_harness.client.get("/api/intake/jobs", params={"page_size": 10})
    assert listed.status_code == 200, listed.text
    job = listed.json()["jobs"][0]
    assert job["current_node"] == "trusted_parse_authority_primary"
    assert job["status"] == "waiting_user"
    assert job["wait_reason"] == "authority_required"
    assert not list((app_harness.workspace_root / "knowledge" / "parsed").glob("*.jsonl"))
    preview = _prepare_and_approve(app_harness, csrf, job)
    detail = app_harness.client.get(f"/api/intake/jobs/{job['job_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["pipeline"]["current_node"] == "primary_semantic_gate"
    assert detail.json()["source_adequacy"]["gate_status"] == "allowed"
    rendered = accepted.text + listed.text + detail.text + json.dumps(preview)
    for forbidden in ("source_ref", "source_fingerprint", "relative_path", "root_id", "local.pdf"):
        assert forbidden not in rendered
    spool = app_harness.config.state_root / "upload-spool"
    assert not spool.exists() or not list(spool.iterdir())
    assert _scientific_output_digests(app_harness) == scientific_before


def test_trusted_parse_uncertainty_resolution_continues_the_same_job_without_reparse(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.pdf_launcher = FakeReaderLauncher()
    metadata = _metadata(
        key="api-source-adequacy-uncertain",
        route="review",
        operation="basic_review_memory",
    )
    uploaded = app_harness.client.post(
        "/api/intake/upload",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        files=[
            ("file", ("uncertain-review.pdf", _uncertain_pdf_bytes(), "application/pdf")),
            ("metadata", (None, json.dumps(metadata), "application/json")),
        ],
    )
    assert uploaded.status_code == 202, uploaded.text
    app_harness.wait_until_idle()
    authority_job = app_harness.client.get(
        "/api/intake/jobs",
        params={"page_size": 10},
    ).json()["jobs"][0]
    _prepare_and_approve(app_harness, csrf, authority_job)

    jobs = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"]
    waiting_job = jobs[0]
    assert waiting_job["job_id"] == authority_job["job_id"]
    assert waiting_job["current_node"] == "source_adequacy"
    assert waiting_job["status"] == "waiting_source"
    assert waiting_job["wait_reason"] == "source_incomplete"
    parsed_before = sorted(
        (path.relative_to(app_harness.workspace_root).as_posix(), path.read_bytes())
        for path in (app_harness.workspace_root / "knowledge" / "parsed").glob("*.jsonl")
    )

    context_response = app_harness.client.get(
        f"/api/intake/jobs/{waiting_job['job_id']}/source-adequacy-resolution"
    )
    assert context_response.status_code == 200, context_response.text
    context = context_response.json()
    assert context["resolution_state"] == "review_required"
    assert context["required_capability"] == "basic_paper_understanding"
    assert context["hard_failure"] is False
    expected = {
        "expected_state_id": waiting_job["state_id"],
        "expected_state_digest": waiting_job["state_digest"],
    }
    opened = app_harness.client.post(
        f"/api/intake/jobs/{waiting_job['job_id']}/source-adequacy-resolution/open",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=expected,
    )
    assert opened.status_code == 200, opened.text
    confirmation_id = opened.json()["confirmation"]["confirmation_id"]
    decided = app_harness.client.post(
        f"/api/intake/jobs/{waiting_job['job_id']}/source-adequacy-resolution/decide",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            **expected,
            "action": "accept_uncertainty",
            "confirmation_id": confirmation_id,
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["resolution_state"] == "continued"

    completed = app_harness.client.get(f"/api/intake/jobs/{waiting_job['job_id']}").json()
    assert completed["pipeline"]["job_id"] == waiting_job["job_id"]
    assert completed["pipeline"]["status"] == "completed"
    assert completed["pipeline"]["current_node"] == "review_semantic_gate"
    parsed_after = sorted(
        (path.relative_to(app_harness.workspace_root).as_posix(), path.read_bytes())
        for path in (app_harness.workspace_root / "knowledge" / "parsed").glob("*.jsonl")
    )
    assert parsed_after == parsed_before
    rendered = context_response.text + opened.text + decided.text + json.dumps(completed)
    for forbidden in (
        "source_ref",
        "source_fingerprint",
        "relative_path",
        "root_id",
        "uncertain-review.pdf",
        "pdfplumber",
        "trusted_parse_receipt",
    ):
        assert forbidden not in rendered


def test_upload_security_and_exact_multipart_shape(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    missing_origin = app_harness.client.post(
        "/api/intake/upload",
        headers={"X-RKB-CSRF": csrf},
        files={"file": ("source.pdf", synthetic_pdf_bytes(), "application/pdf")},
    )
    missing_csrf = app_harness.client.post(
        "/api/intake/upload",
        headers={"Origin": EXPECTED_ORIGIN},
        files={"file": ("source.pdf", synthetic_pdf_bytes(), "application/pdf")},
    )
    extra = _upload(app_harness, csrf, _metadata(key="api-extra"), extra=True)
    json_only = app_harness.client.post(
        "/api/intake/upload",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=_metadata(key="api-json"),
    )

    assert missing_origin.status_code == 403
    assert missing_csrf.status_code == 401
    assert extra.status_code == 400
    assert json_only.status_code == 415


def test_watched_inbox_start_and_job_cas_controls(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    inbox = app_harness.workspace_root / "sources" / "inbox"
    review = inbox / "synthetic-review.pdf"
    review.write_bytes(synthetic_pdf_bytes("Synthetic review background."))
    old = time.time() - 120
    os.utime(review, (old, old))

    scan = app_harness.client.get(
        "/api/intake/inbox",
        params={"max_entries": 10, "min_stable_age_seconds": 5},
    )
    assert scan.status_code == 200, scan.text
    candidate = scan.json()["candidates"][0]
    started = app_harness.client.post(
        "/api/intake/inbox/start",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "candidate_token": candidate["candidate_token"],
            **_metadata(key="api-inbox-1", route="review", operation="basic_review_memory"),
            "min_stable_age_seconds": 5,
        },
    )
    assert started.status_code == 202, started.text
    app_harness.wait_until_idle()
    jobs = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"]
    review_job = next(
        item for item in jobs if item["current_node"] == "trusted_parse_authority_review"
    )
    _prepare_and_approve(app_harness, csrf, review_job)
    jobs = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"]
    review_job = next(item for item in jobs if item["current_node"] == "review_semantic_gate")
    assert review_job["status"] == "completed"

    waiting = _upload(app_harness, csrf, _metadata(key="api-wait-1", route=None))
    assert waiting.status_code == 202
    app_harness.wait_until_idle()
    jobs = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"]
    authority_job = next(
        item for item in jobs if item["current_node"] == "trusted_parse_authority_undecided"
    )
    _prepare_and_approve(app_harness, csrf, authority_job)
    jobs = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"]
    wait_job = next(item for item in jobs if item["wait_reason"] == "route_ambiguous")
    resumed = app_harness.client.post(
        f"/api/intake/jobs/{wait_job['job_id']}/resume",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "expected_state_id": wait_job["state_id"],
            "expected_state_digest": wait_job["state_digest"],
            "requested_operation": "basic_paper_card",
            "document_route": "primary",
            "route_reason": None,
            "bibliography": {"title": "Synthetic intake", "authors": [], "year": 2026, "doi": None},
        },
    )
    assert resumed.status_code == 202, resumed.text
    app_harness.wait_until_idle()
    detail = app_harness.client.get(f"/api/intake/jobs/{wait_job['job_id']}").json()
    assert detail["pipeline"]["status"] == "completed", json.dumps(
        {"detail": detail, "health": app_harness.client.get("/api/health").json()},
        sort_keys=True,
    )


def test_intake_limits_and_path_shaped_ids_fail_closed(app_harness: AppHarness) -> None:
    app_harness.open_workspace()
    too_many = app_harness.client.get("/api/intake/jobs", params={"page_size": 101})
    path_id = app_harness.client.get("/api/intake/jobs/../private")

    assert too_many.status_code == 400
    assert path_id.status_code in {400, 404}
    assert "private" not in path_id.text


def test_filtered_intake_jobs_use_exact_catalog_cursor_and_exclude_other_job_kinds(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    service = PipelineJobService(runtime.session._layout)
    expected_ids = {
        _upload_pending_intake_job(
            app_harness,
            csrf,
            key=f"filtered-intake-{index}",
        )["job_id"]
        for index in range(2)
    }
    service.create(
        requested_route="semantic_processing",
        requested_depth="primary_semantic_bundle",
        current_node="semantic_processing",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["agent_task_create"],
            "captured_at": "2026-08-09T00:00:00Z",
        },
        idempotency_key="filtered-intake-semantic-control",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    service.create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="registry",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["register_by_reference"],
            "captured_at": "2026-08-09T00:00:00Z",
        },
        idempotency_key="filtered-intake-registry-control",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    rebuilt = app_harness.client.post(
        "/api/catalog/rebuild",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    assert rebuilt.status_code == 202, rebuilt.text
    assert app_harness.wait_until_idle()["projection_state"] == "current"

    params = {
        "page_size": 1,
        "requested_route": "local_source",
        "requested_depth": "semantic_gate",
    }
    first = app_harness.client.get("/api/intake/jobs", params=params)
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert len(first_payload["jobs"]) == 1
    assert first_payload["next_cursor"]
    second = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": first_payload["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["next_cursor"] is None
    returned = first_payload["jobs"] + second_payload["jobs"]
    assert {item["job_id"] for item in returned} == expected_ids
    assert all(
        item["requested_route"] == "local_source"
        and item["requested_depth"] == "semantic_gate"
        for item in returned
    )


def test_filtered_intake_jobs_reject_partial_invalid_and_stale_filters(
    app_harness: AppHarness,
) -> None:
    app_harness.open_workspace()
    partial = app_harness.client.get(
        "/api/intake/jobs",
        params={"requested_route": "local_source"},
    )
    invalid = app_harness.client.get(
        "/api/intake/jobs",
        params={
            "requested_route": "semantic_processing",
            "requested_depth": "primary_semantic_bundle",
        },
    )
    runtime = app_harness.client.app.state.runtime
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "stale"}
    stale = app_harness.client.get(
        "/api/intake/jobs",
        params={
            "requested_route": "local_source",
            "requested_depth": "semantic_gate",
        },
    )

    assert partial.status_code == 400
    assert partial.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER"
    assert invalid.status_code == 400
    assert invalid.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER"
    assert stale.status_code == 200, stale.text
    assert stale.json()["jobs"] == []


def test_filtered_intake_jobs_missing_projection_returns_empty_success(
    app_harness: AppHarness,
) -> None:
    app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "missing"}

    response = app_harness.client.get(
        "/api/intake/jobs",
        params={
            "requested_route": "local_source",
            "requested_depth": "semantic_gate",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["jobs"] == []
    assert payload["next_cursor"] is None
    assert payload["projection_state"] == "missing"


def test_filtered_intake_jobs_direct_mode_filters_and_paginates_without_duplicates(
    app_harness: AppHarness,
) -> None:
    app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    service = PipelineJobService(runtime.session._layout)
    authority = {
        "actor": "user",
        "granted_operations": ["advance_deterministic_trunk"],
        "captured_at": "2026-08-09T00:00:00Z",
    }
    expected_ids = {
        service.create(
            requested_route="local_source",
            requested_depth="semantic_gate",
            current_node="source_check",
            input_refs=[],
            authority_snapshot=authority,
            idempotency_key=f"direct-filter-{index}",
            actor="user",
            fixture_origin="synthetic_from_scratch",
        ).state["job_id"]
        for index in range(3)
    }
    service.create(
        requested_route="semantic_processing",
        requested_depth="primary_semantic_bundle",
        current_node="semantic_processing",
        input_refs=[],
        authority_snapshot=authority,
        idempotency_key="direct-filter-control",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "stale"}

    params = {
        "page_size": 1,
        "requested_route": "local_source",
        "requested_depth": "semantic_gate",
    }
    returned: list[dict] = []
    cursor = None
    while True:
        request_params = {**params}
        if cursor is not None:
            request_params["cursor"] = cursor
        response = app_harness.client.get("/api/intake/jobs", params=request_params)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["jobs"]) <= 1
        returned.extend(payload["jobs"])
        cursor = payload["next_cursor"]
        if cursor is None:
            break

    assert {item["job_id"] for item in returned} == expected_ids
    assert len(returned) == len(expected_ids)
    assert all(
        item["requested_route"] == "local_source"
        and item["requested_depth"] == "semantic_gate"
        for item in returned
    )


def test_filtered_intake_jobs_cursor_mode_sticks_and_catalog_cursor_fails_closed(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    for index in range(2):
        _upload_pending_intake_job(
            app_harness,
            csrf,
            key=f"cursor-mode-direct-{index}",
        )
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "stale"}
    params = {
        "page_size": 1,
        "requested_route": "local_source",
        "requested_depth": "semantic_gate",
    }
    direct_first = app_harness.client.get("/api/intake/jobs", params=params)
    assert direct_first.status_code == 200, direct_first.text
    direct_cursor = direct_first.json()["next_cursor"]
    assert direct_cursor

    rebuilt = app_harness.client.post(
        "/api/catalog/rebuild",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    assert rebuilt.status_code == 202, rebuilt.text
    assert app_harness.wait_until_idle()["projection_state"] == "current"
    direct_second = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": direct_cursor},
    )
    assert direct_second.status_code == 200, direct_second.text
    assert direct_second.json()["jobs"]

    catalog_first = app_harness.client.get("/api/intake/jobs", params=params)
    assert catalog_first.status_code == 200, catalog_first.text
    catalog_cursor = catalog_first.json()["next_cursor"]
    assert catalog_cursor
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "stale"}
    catalog_second = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": catalog_cursor},
    )
    assert catalog_second.status_code == 409
    assert catalog_second.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER-PROJECTION"


def test_filtered_intake_jobs_reject_tampered_and_cross_workspace_cursors(
    app_harness: AppHarness,
) -> None:
    app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    service = PipelineJobService(runtime.session._layout)
    service.create(
        requested_route="local_source",
        requested_depth="semantic_gate",
        current_node="source_check",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["advance_deterministic_trunk"],
            "captured_at": "2026-08-09T00:00:00Z",
        },
        idempotency_key="cursor-security",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "stale"}
    params = {
        "page_size": 1,
        "requested_route": "local_source",
        "requested_depth": "semantic_gate",
    }
    first = app_harness.client.get("/api/intake/jobs", params=params)
    assert first.status_code == 200, first.text
    assert first.json()["next_cursor"] is None

    valid = runtime._encode_intake_filter_cursor(mode="direct", inner_cursor="job_00000000")
    malformed_response = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": "not+a+cursor"},
    )
    assert malformed_response.status_code == 400
    assert malformed_response.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER-CURSOR"

    tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")
    tampered_response = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": tampered},
    )
    assert tampered_response.status_code == 400
    assert tampered_response.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER-CURSOR"

    invalid_mode_payload = {
        "version": 1,
        "mode": "other",
        "workspace_option_id": runtime.active_option_id,
        "inner_cursor": "job_00000000",
    }
    invalid_mode_bytes = json.dumps(
        invalid_mode_payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    invalid_mode_signature = hmac.new(
        runtime._intake_filter_cursor_key,
        invalid_mode_bytes,
        hashlib.sha256,
    ).digest()
    invalid_mode_cursor = base64.urlsafe_b64encode(
        invalid_mode_bytes + invalid_mode_signature
    ).decode("ascii").rstrip("=")
    invalid_mode_response = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": invalid_mode_cursor},
    )
    assert invalid_mode_response.status_code == 400
    assert invalid_mode_response.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER-CURSOR"

    runtime.active_option_id = "other-workspace"
    cross_workspace = app_harness.client.get(
        "/api/intake/jobs",
        params={**params, "cursor": valid},
    )
    assert cross_workspace.status_code == 400
    assert cross_workspace.json()["diagnostic"]["code"] == "RKBAPP-INTAKE-FILTER-CURSOR"


def test_trusted_parse_contract_rejects_sensitive_or_stale_browser_input(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    assert _upload(app_harness, csrf, _metadata(key="api-trusted-contract")).status_code == 202
    app_harness.wait_until_idle()
    job = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"][0]
    endpoint = f"/api/intake/jobs/{job['job_id']}/trusted-parse/prepare"

    extra = app_harness.client.post(
        endpoint,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
            "source_ref": {"root_id": "private", "relative_path": "paper.pdf"},
        },
    )
    missing_origin = app_harness.client.post(
        endpoint,
        headers={"X-RKB-CSRF": csrf},
        json={
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
        },
    )
    path_job = app_harness.client.post(
        "/api/intake/jobs/../private/trusted-parse/prepare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
        },
    )

    assert extra.status_code == 422
    assert missing_origin.status_code == 403
    assert path_job.status_code in {400, 404}
    assert "paper.pdf" not in extra.text


def test_active_trusted_parse_cancel_is_signal_only_and_prevents_promotion(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    worker_started = threading.Event()

    def cancellable_worker(_request, *, cancel_check=None):
        worker_started.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if cancel_check is not None and cancel_check():
                raise ResearchKBError(
                    Diagnostic(
                        OPERATION_CANCELLED,
                        "parser-worker",
                        None,
                        "/cancel",
                        "synthetic cancellation",
                    )
                )
            time.sleep(0.01)
        raise AssertionError("trusted Parse cancellation was not delivered to the worker")

    runtime.trusted_parse = TrustedParseIntakeApplicationService(
        worker_runner=cancellable_worker
    )
    assert _upload(app_harness, csrf, _metadata(key="api-trusted-cancel")).status_code == 202
    app_harness.wait_until_idle()
    job = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"][0]
    prepared_response = app_harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/prepare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
        },
    )
    assert prepared_response.status_code == 200, prepared_response.text
    prepared = prepared_response.json()
    approval_payload = {
        "lease_token": prepared["lease_token"],
        "aggregate_preview_digest": prepared["aggregate_preview_digest"],
    }
    approved = app_harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/approve",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=approval_payload,
    )
    assert approved.status_code == 202, approved.text
    assert worker_started.wait(5), "trusted Parse worker did not start"

    duplicate = app_harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/approve",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=approval_payload,
    )
    cancelled = app_harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/cancel",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
        },
    )

    assert duplicate.status_code == 202, duplicate.text
    assert duplicate.json() == approved.json()
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["cancel_outcome"] == "accepted"
    health = app_harness.wait_until_idle()
    assert health["operation"]["state"] == "current"
    detail = app_harness.client.get(f"/api/intake/jobs/{job['job_id']}").json()
    assert detail["pipeline"]["status"] == "cancelled"
    assert not list((app_harness.workspace_root / "knowledge" / "parsed").glob("*.jsonl"))


def test_trusted_parse_restart_loses_lease_but_allows_fresh_preparation(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    assert _upload(app_harness, csrf, _metadata(key="api-trusted-restart")).status_code == 202
    app_harness.wait_until_idle()
    job = app_harness.client.get("/api/intake/jobs", params={"page_size": 10}).json()["jobs"][0]
    endpoint = f"/api/intake/jobs/{job['job_id']}/trusted-parse/prepare"
    expected = {
        "expected_state_id": job["state_id"],
        "expected_state_digest": job["state_digest"],
    }
    first_response = app_harness.client.post(
        endpoint,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=expected,
    )
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    runtime.trusted_parse_leases.clear()

    stale = app_harness.client.post(
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/approve",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={
            "lease_token": first["lease_token"],
            "aggregate_preview_digest": first["aggregate_preview_digest"],
        },
    )
    refreshed = app_harness.client.post(
        endpoint,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=expected,
    )

    assert stale.status_code == 409
    assert stale.json()["diagnostic"]["code"] == "RKBAPP-TRUSTED-PARSE-STALE"
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["lease_token"] != first["lease_token"]


def test_core_failure_remains_visible_and_conservatively_marks_catalog_stale(
    app_harness: AppHarness,
    monkeypatch,
) -> None:
    csrf = app_harness.open_workspace()
    rebuilt = app_harness.client.post(
        "/api/catalog/rebuild",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={},
    )
    assert rebuilt.status_code == 202
    assert app_harness.wait_until_idle()["projection_state"] == "current"
    runtime = app_harness.client.app.state.runtime

    def fail_after_possible_progress(*_args, **_kwargs):
        raise ResearchKBError(
            Diagnostic("RKBC-017", "deterministic-intake", None, "", "private detail")
        )

    monkeypatch.setattr(runtime.intake, "start_upload", fail_after_possible_progress)
    accepted = _upload(app_harness, csrf, _metadata(key="api-failure-1"))
    assert accepted.status_code == 202
    health = app_harness.wait_until_idle()

    assert health["operation"]["state"] == "failed"
    assert health["operation"]["diagnostic_code"] == "RKBC-017"
    assert health["projection_state"] == "stale"
    assert "private detail" not in json.dumps(health)
