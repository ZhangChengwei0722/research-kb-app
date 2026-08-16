from __future__ import annotations

import json
from pathlib import Path

import yaml

from conftest import EXPECTED_ORIGIN, AppHarness, tree_digest


PRIMARY_PAPER_ID = "paper_08c0dd81-5b44-4d2f-9d32-662fb3e15ae5"
SECOND_PAPER_ID = "paper_f8daed20-fcf0-4ed8-9795-694bd631def9"
REVIEW_PAPER_ID = "paper_c5743fa9-6803-4e6a-9928-46b07399d761"
REQUIRED_CLASSES = ["canonical_evidence", "operational_context", "paper_card_content"]
SCIENTIFIC_ROOTS = (
    "paper_cards",
    "evidence",
    "primary_bundles",
    "review_memories",
    "review_bundles",
    "review_queue",
    "questions",
    "step7",
)


def _enable_query_policy(harness: AppHarness) -> None:
    config_path = harness.workspace_root / "workspace.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["agent_policy"] = {
        "registry_version": "p5c-v1",
        "allowed_content_classes": [
            "metadata",
            "canonical_evidence",
            "paper_card_content",
            "review_background",
            "research_routing_context",
            "operational_context",
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


def _expected(task: dict) -> dict[str, str]:
    return {
        "expected_state_id": task["state_id"],
        "expected_state_digest": task["state_digest"],
    }


def _create_payload(**overrides: object) -> dict:
    payload = {
        "query_type": "selected_paper_comparison",
        "query_text": "What common pattern do these selected records show?",
        "paper_ids": [SECOND_PAPER_ID, PRIMARY_PAPER_ID],
        "include_review_background": False,
        "include_routing_context": False,
        "executor_id": "codex_cli",
        "approved_content_classes": [*REQUIRED_CLASSES, "metadata"],
        "idempotency_key": "p5c-query-api",
    }
    payload.update(overrides)
    return payload


def _scientific_digests(workspace_root: Path) -> dict[str, str]:
    return {
        name: tree_digest(workspace_root / "knowledge" / name)
        for name in SCIENTIFIC_ROOTS
    }


def _cross_paper_report(task: dict, handoff: dict) -> dict:
    papers = handoff["payload"]["primary_papers"]
    refs = []
    for paper in papers:
        unit = paper["card_units"][0]
        refs.append(
            {
                "paper_id": paper["paper_id"],
                "card_unit_id": unit["unit_id"],
                "evidence_ids": [unit["evidence_ids"][0]],
            }
        )
    return {
        "contract_version": "p5c-knowledge-query-report@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "query_type": "selected_paper_comparison",
        "answer_blocks": [
            {
                "block_role": "cross_paper_synthesis",
                "text": "<script>Both fabricated records retain one bounded pattern.</script>",
                "support_refs": refs,
                "background_refs": [],
                "background_only": False,
            }
        ],
        "unresolved_items": [],
        "persistence_status": "report_only",
        "canonical_scientific_write": False,
    }


def test_query_handoff_preview_and_accept_are_operational_only(app_harness: AppHarness) -> None:
    _enable_query_policy(app_harness)
    csrf = app_harness.open_workspace()
    scientific_before = _scientific_digests(app_harness.workspace_root)
    sources_before = tree_digest(app_harness.workspace_root / "sources")
    jobs_before = app_harness.client.get("/api/intake/jobs", params={"page_size": 100}).json()["jobs"]

    created = _post(app_harness, csrf, "/api/knowledge-queries", _create_payload())
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    assert task["task_kind"] == "knowledge_query_report"
    assert task["paper_id"] is None
    assert task["job_id"] is None
    app_harness.wait_until_idle()

    inspected = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/inspect-handoff",
        {**_expected(task), "executor_id": "codex_cli"},
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["handoff_preview"]["payload"]["operational_context"][
        "canonical_scientific_write"
    ] is False

    handed_off = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/handoff",
        {**_expected(task), "executor_id": "codex_cli"},
    )
    assert handed_off.status_code == 200, handed_off.text
    leased = handed_off.json()["task"]
    report = _cross_paper_report(leased, handed_off.json()["handoff"])
    app_harness.wait_until_idle()

    submitted = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/submit",
        {**_expected(leased), "result": report},
    )
    assert submitted.status_code == 200, submitted.text
    submitted_task = submitted.json()["task"]
    app_harness.wait_until_idle()

    preview = app_harness.client.get(f"/api/agent/tasks/{task['task_id']}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["candidate"]["retention_class"] == "current_task_report"
    assert preview.json()["candidate"]["answer_blocks"][0]["text"].startswith("<script>")

    wrong_approval = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/approve",
        _expected(submitted_task),
    )
    assert wrong_approval.status_code == 409

    accepted = _post(
        app_harness,
        csrf,
        f"/api/knowledge-queries/{task['task_id']}/accept-report",
        _expected(submitted_task),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["task"]["status"] == "approved"
    assert accepted.json()["canonical_scientific_write"] is False
    app_harness.wait_until_idle()

    jobs_after = app_harness.client.get("/api/intake/jobs", params={"page_size": 100}).json()["jobs"]
    assert jobs_after == jobs_before
    assert _scientific_digests(app_harness.workspace_root) == scientific_before
    assert tree_digest(app_harness.workspace_root / "sources") == sources_before
    rendered = "\n".join(
        (created.text, inspected.text, handed_off.text, submitted.text, preview.text, accepted.text)
    )
    for forbidden in ("source_ref", "source_fingerprint", "relative_path", str(app_harness.workspace_root)):
        assert forbidden not in rendered


def test_query_creation_is_strict_authenticated_and_cardinality_bound(app_harness: AppHarness) -> None:
    _enable_query_policy(app_harness)
    unauthenticated = app_harness.client.post(
        "/api/knowledge-queries",
        headers={"Origin": EXPECTED_ORIGIN},
        json=_create_payload(),
    )
    assert unauthenticated.status_code == 401
    csrf = app_harness.open_workspace()

    missing_csrf = app_harness.client.post(
        "/api/knowledge-queries",
        headers={"Origin": EXPECTED_ORIGIN},
        json=_create_payload(),
    )
    path_shaped = _post(
        app_harness,
        csrf,
        "/api/knowledge-queries",
        _create_payload(paper_ids=[PRIMARY_PAPER_ID, "paper_.."]),
    )
    wrong_single = _post(
        app_harness,
        csrf,
        "/api/knowledge-queries",
        _create_payload(query_type="methods", paper_ids=[PRIMARY_PAPER_ID, SECOND_PAPER_ID]),
    )
    wrong_multi = _post(
        app_harness,
        csrf,
        "/api/knowledge-queries",
        _create_payload(query_type="trend_problem_discussion", paper_ids=[PRIMARY_PAPER_ID]),
    )
    unknown = _post(
        app_harness,
        csrf,
        "/api/knowledge-queries",
        {**_create_payload(), "canonical_write": True},
    )
    assert missing_csrf.status_code == 401
    assert path_shaped.status_code == 400
    assert wrong_single.status_code in {400, 409, 422}
    assert wrong_multi.status_code in {400, 409, 422}
    assert unknown.status_code == 422


def test_all_six_query_types_create_with_their_supported_cardinality(app_harness: AppHarness) -> None:
    _enable_query_policy(app_harness)
    csrf = app_harness.open_workspace()
    cases = (
        ("single_paper_explanation", [PRIMARY_PAPER_ID]),
        ("seven_section_overview", [PRIMARY_PAPER_ID]),
        ("methods", [PRIMARY_PAPER_ID]),
        ("selected_paper_comparison", [PRIMARY_PAPER_ID, SECOND_PAPER_ID]),
        ("trend_problem_discussion", [PRIMARY_PAPER_ID, SECOND_PAPER_ID]),
        ("evidence_find", [PRIMARY_PAPER_ID]),
    )

    for index, (query_type, paper_ids) in enumerate(cases, start=1):
        created = _post(
            app_harness,
            csrf,
            "/api/knowledge-queries",
            _create_payload(
                query_type=query_type,
                query_text=f"Bounded synthetic request for {query_type}.",
                paper_ids=paper_ids,
                idempotency_key=f"p5c-query-type-{index}",
            ),
        )
        assert created.status_code == 200, created.text
        task = created.json()["task"]
        assert task["query_type"] == query_type
        assert task["paper_ids"] == paper_ids
        assert task["job_id"] is None
        app_harness.wait_until_idle()


def test_changed_source_rejects_query_result_before_staging(app_harness: AppHarness) -> None:
    _enable_query_policy(app_harness)
    csrf = app_harness.open_workspace()
    created = _post(
        app_harness,
        csrf,
        "/api/knowledge-queries",
        _create_payload(
            query_type="single_paper_explanation",
            query_text="Explain the selected fabricated paper.",
            paper_ids=[PRIMARY_PAPER_ID],
            idempotency_key="p5c-stale-query",
        ),
    ).json()["task"]
    app_harness.wait_until_idle()
    handed_off = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{created['task_id']}/handoff",
        {**_expected(created), "executor_id": "codex_cli"},
    )
    assert handed_off.status_code == 200, handed_off.text
    leased = handed_off.json()["task"]
    payload = handed_off.json()["handoff"]["payload"]
    app_harness.wait_until_idle()
    paper = payload["primary_papers"][0]
    unit = paper["card_units"][0]
    result = {
        "contract_version": "p5c-knowledge-query-report@1.0",
        "task_id": leased["task_id"],
        "input_basis_digest": leased["input_basis_digest"],
        "query_type": "single_paper_explanation",
        "answer_blocks": [
            {
                "block_role": "factual",
                "text": "The fabricated record contains one retained statement.",
                "support_refs": [
                    {
                        "paper_id": paper["paper_id"],
                        "card_unit_id": unit["unit_id"],
                        "evidence_ids": [unit["evidence_ids"][0]],
                    }
                ],
                "background_refs": [],
                "background_only": False,
            }
        ],
        "unresolved_items": [],
        "persistence_status": "report_only",
        "canonical_scientific_write": False,
    }

    source = app_harness.workspace_root / "sources" / "source-00000002.txt"
    source.write_text("Changed synthetic source.", encoding="utf-8", newline="\n")
    submitted = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{created['task_id']}/submit",
        {**_expected(leased), "result": result},
    )
    assert submitted.status_code == 409
    assert "Changed synthetic source" not in submitted.text


def test_query_background_refs_cannot_be_used_as_factual_support(app_harness: AppHarness) -> None:
    _enable_query_policy(app_harness)
    csrf = app_harness.open_workspace()
    created = _post(
        app_harness,
        csrf,
        "/api/knowledge-queries",
        _create_payload(
            query_type="evidence_find",
            query_text="Find support and show review context.",
            paper_ids=[PRIMARY_PAPER_ID, REVIEW_PAPER_ID],
            include_review_background=True,
            approved_content_classes=[*REQUIRED_CLASSES, "metadata", "review_background"],
            idempotency_key="p5c-background-query",
        ),
    )
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    app_harness.wait_until_idle()
    handed_off = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/handoff",
        {**_expected(task), "executor_id": "codex_cli"},
    )
    assert handed_off.status_code == 200, handed_off.text
    leased = handed_off.json()["task"]
    payload = handed_off.json()["handoff"]["payload"]
    app_harness.wait_until_idle()
    review = payload["review_background"][0]
    review_unit = review["review_units"][0]
    invalid = {
        "contract_version": "p5c-knowledge-query-report@1.0",
        "task_id": leased["task_id"],
        "input_basis_digest": leased["input_basis_digest"],
        "query_type": "evidence_find",
        "answer_blocks": [
            {
                "block_role": "background",
                "text": "Review-only orientation.",
                "support_refs": [],
                "background_refs": [
                    {
                        "paper_id": review["paper_id"],
                        "review_memory_id": review["review_memory_id"],
                        "review_unit_id": review_unit["review_unit_id"],
                    }
                ],
                "background_only": False,
            }
        ],
        "unresolved_items": [],
        "persistence_status": "report_only",
        "canonical_scientific_write": False,
    }
    submitted = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/submit",
        {**_expected(leased), "result": invalid},
    )
    assert submitted.status_code in {400, 409, 422}
