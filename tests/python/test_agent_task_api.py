from __future__ import annotations

import json

import yaml

from conftest import EXPECTED_ORIGIN, AppHarness, synthetic_pdf_bytes


REVIEW_SECTIONS = (
    "review_objective_scope",
    "review_question_search_boundaries",
    "taxonomy_field_structure",
    "major_synthesis",
    "methods_metrics_guardrails",
    "gaps_frontiers",
    "primary_leads_reuse",
)
APPROVED_CLASSES = ["metadata", "operational_context", "parsed_excerpt"]


def _enable_agent_policy(harness: AppHarness) -> None:
    config_path = harness.workspace_root / "workspace.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["agent_policy"] = {
        "registry_version": "p4c-v1",
        "allowed_content_classes": [
            "metadata",
            "operational_context",
            "parsed_excerpt",
            "review_background",
        ],
        "execution_scope": "cloud_allowed",
        "max_prompt_bytes": 1_048_576,
        "max_result_bytes": 1_048_576,
    }
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _post(harness: AppHarness, csrf: str, path: str, payload: dict):
    return harness.client.post(
        path,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=payload,
    )


def _upload_review(harness: AppHarness, csrf: str) -> dict:
    metadata = {
        "idempotency_key": "p4d-review-intake",
        "requested_operation": "basic_review_memory",
        "document_route": "review",
        "route_reason": None,
        "bibliography": {
            "title": "Synthetic review for App handoff",
            "authors": [],
            "year": 2026,
            "doi": None,
        },
    }
    response = harness.client.post(
        "/api/intake/upload",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        files=[
            (
                "file",
                (
                    "review.pdf",
                    synthetic_pdf_bytes("Synthetic review background with no reusable units."),
                    "application/pdf",
                ),
            ),
            ("metadata", (None, json.dumps(metadata), "application/json")),
        ],
    )
    assert response.status_code == 202, response.text
    harness.wait_until_idle()
    jobs_response = harness.client.get("/api/intake/jobs", params={"page_size": 20})
    assert jobs_response.status_code == 200, jobs_response.text
    jobs = jobs_response.json()["jobs"]
    job = next(item for item in jobs if item["current_node"] == "trusted_parse_authority_review")

    prepared = _post(
        harness,
        csrf,
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/prepare",
        {
            "expected_state_id": job["state_id"],
            "expected_state_digest": job["state_digest"],
        },
    )
    assert prepared.status_code == 200, prepared.text
    preview = prepared.json()
    assert preview["persistent_writes"] == 0
    assert preview["source"]["identity_status"] == "current"
    assert preview["allowed_operation"] == "parse_run"

    approved = _post(
        harness,
        csrf,
        f"/api/intake/jobs/{job['job_id']}/trusted-parse/approve",
        {
            "lease_token": preview["lease_token"],
            "aggregate_preview_digest": preview["aggregate_preview_digest"],
        },
    )
    assert approved.status_code == 202, approved.text
    harness.wait_until_idle()

    jobs_response = harness.client.get("/api/intake/jobs", params={"page_size": 20})
    assert jobs_response.status_code == 200, jobs_response.text
    jobs = jobs_response.json()["jobs"]
    job = next(item for item in jobs if item["current_node"] == "review_semantic_gate")
    detail = harness.client.get(f"/api/intake/jobs/{job['job_id']}")
    assert detail.status_code == 200, detail.text
    return detail.json()


def _zero_unit_candidate(task: dict) -> dict:
    return {
        "contract_version": "p4c-review-semantic-candidate@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "review_subtype": "narrative_review",
        "review_subtype_source": "agent_high_confidence",
        "review_subtype_reason": "The synthetic document is a secondary synthesis.",
        "read_status": "targeted_read",
        "scope_tags": ["synthetic_review"],
        "one_sentence_reuse_value": "No reusable unit remains after provenance review.",
        "memory_value": {
            "status": "low_value",
            "reason": "The synthetic source is redundant.",
        },
        "coverage_limits": {
            "unread_sections": ["Synthetic appendix"],
            "weakly_read_sections": [],
            "reason": "The appendix was outside the targeted read.",
        },
        "sections": [
            {"section_id": section_id, "units": []}
            for section_id in REVIEW_SECTIONS
        ],
        "non_reusable_notes": [
            {
                "content": "<script>untrusted review text</script>",
                "reason": "promotional",
            }
        ],
    }


def test_review_agent_task_handoff_recovery_preview_and_approval(app_harness: AppHarness) -> None:
    _enable_agent_policy(app_harness)
    csrf = app_harness.open_workspace()
    detail = _upload_review(app_harness, csrf)
    job = detail["pipeline"]

    registry = app_harness.client.get("/api/agent/registry")
    assert registry.status_code == 200, registry.text
    assert registry.json()["registry_version"] == "p4c-v1"
    assert registry.json()["embedded_agent_runtime"] is False

    created = _post(
        app_harness,
        csrf,
        f"/api/intake/jobs/{job['job_id']}/agent-tasks",
        {
            "paper_id": detail["paper_id"],
            "task_kind": "review_semantic_processing",
            "executor_id": "codex_cli",
            "approved_content_classes": APPROVED_CLASSES,
            "idempotency_key": "p4d-review-task",
        },
    )
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    app_harness.wait_until_idle()

    inspected = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/inspect-handoff",
        {
            "expected_state_id": task["state_id"],
            "expected_state_digest": task["state_digest"],
            "executor_id": "codex_cli",
        },
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["persistent_writes"] == 0
    assert "prompt" not in inspected.json()["handoff_preview"]
    assert "lease" not in inspected.text

    handed_off = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/handoff",
        {
            "expected_state_id": task["state_id"],
            "expected_state_digest": task["state_digest"],
            "executor_id": "codex_cli",
        },
    )
    assert handed_off.status_code == 200, handed_off.text
    handoff = handed_off.json()
    assert "PAYLOAD_JSON" in handoff["handoff"]["prompt"]
    assert "lease" not in handoff
    app_harness.wait_until_idle()

    app_harness.client.app.state.runtime._agent_leases.clear()
    leased_task = handoff["task"]
    submitted = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/submit",
        {
            "expected_state_id": leased_task["state_id"],
            "expected_state_digest": leased_task["state_digest"],
            "result": _zero_unit_candidate(leased_task),
        },
    )
    assert submitted.status_code == 200, submitted.text
    submitted_task = submitted.json()["task"]
    assert submitted_task["status"] == "submitted"
    assert "lease" not in submitted.text
    app_harness.wait_until_idle()

    preview = app_harness.client.get(f"/api/agent/tasks/{task['task_id']}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["candidate"]["background_only"] is True
    assert preview.json()["candidate"]["non_reusable_notes"][0]["content"].startswith("<script>")

    approved = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/approve",
        {
            "expected_state_id": submitted_task["state_id"],
            "expected_state_digest": submitted_task["state_digest"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["review_bundle"]["review_unit_count"] == 0
    assert approved.json()["review_bundle"]["background_only"] is True
    app_harness.wait_until_idle()

    shown = app_harness.client.get(f"/api/agent/tasks/{task['task_id']}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["current_task"]["status"] == "approved"
    rendered = "\n".join(
        [
            registry.text,
            created.text,
            inspected.text,
            handed_off.text,
            submitted.text,
            preview.text,
            approved.text,
            shown.text,
        ]
    )
    for forbidden in ("source_ref", "relative_path", "root_id", str(app_harness.workspace_root)):
        assert forbidden not in rendered


def test_agent_result_endpoint_has_a_separate_bounded_envelope(app_harness: AppHarness) -> None:
    _enable_agent_policy(app_harness)
    csrf = app_harness.open_workspace()
    response = _post(
        app_harness,
        csrf,
        "/api/agent/tasks/AT-00000000000000000000000000/submit",
        {
            "expected_state_id": "ATS-00000000000000000000000000",
            "expected_state_digest": "0" * 64,
            "result": {"untrusted": "x" * 5000},
        },
    )
    assert response.status_code != 413
