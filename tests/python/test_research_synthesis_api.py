from __future__ import annotations

import yaml
import pytest
from research_kb.services import ResearchOrganizationService

from tests.python.conftest import EXPECTED_ORIGIN, AppHarness, tree_digest


QUESTION_ID = "question_11111111-1111-4111-8111-111111111111"
CANDIDATE_ID = "step7candidate_22222222-2222-4222-8222-222222222222"
TASK_ID = "agenttask_33333333-3333-4333-8333-333333333333"
STATE_ID = "agenttaskstate_44444444-4444-4444-8444-444444444444"
FIXTURE_QUESTION_ID = "question_272dfde3-ef0f-4205-b9a1-65623487637d"
FIXTURE_SYNTHESIS_ID = "synthesis_4c3b785e-4979-46f9-8162-23a628ddb1e3"
P8_CLASSES = [
    "metadata",
    "canonical_evidence",
    "paper_card_content",
    "review_background",
    "research_routing_context",
    "research_synthesis",
    "operational_context",
]


class FakeResearchSynthesisService:
    candidate = {
        "candidate_id": CANDIDATE_ID,
        "candidate_type": "synthesis",
        "question_id": QUESTION_ID,
        "title": "Synthetic candidate",
        "candidate_status": "candidate",
        "not_fact": True,
        "review_status": "ai_draft",
        "automation_status": "pending",
        "freshness": {"state": "current", "reasons": []},
    }

    def limits(self, session):
        return {
            "status": "success",
            "max_page_size": 100,
            "candidate_types": ["synthesis", "review_angle", "insight", "cross_view"],
            "maintenance_intents": ["append", "replace"],
            "persistent_writes": 0,
        }

    def list_candidates(self, session, **kwargs):
        assert kwargs == {
            "question_id": QUESTION_ID,
            "candidate_type": "synthesis",
            "freshness": "current",
            "page_size": 20,
            "cursor": None,
        }
        return {
            "status": "success",
            "candidates": [dict(self.candidate)],
            "next_cursor": None,
            "persistent_writes": 0,
        }

    def show_candidate(self, session, candidate_id: str):
        return {
            "status": "success",
            "candidate": {**self.candidate, "candidate_id": candidate_id},
            "persistent_writes": 0,
        }

    def question_context(self, session, question_id: str):
        return {
            "status": "success",
            "question": {"question_id": question_id, "question_text": "Synthetic question?"},
            "candidate_count": 1,
            "stale_candidate_count": 0,
            "persistent_writes": 0,
        }


class FakeResearchSynthesisAgentService:
    task = {
        "task_id": TASK_ID,
        "state_id": STATE_ID,
        "state_digest": "a" * 64,
        "task_kind": "research_synthesis_drafting",
        "status": "created",
    }

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def create_research_synthesis_proposal(self, session, request):
        assert request["question_id"] == QUESTION_ID
        assert request["candidate_type"] in {
            "synthesis",
            "review_angle",
            "insight",
            "cross_view",
        }
        assert request["maintenance_intent"] == "append"
        assert request["target_candidate_id"] is None
        self.requests.append(dict(request))
        return {
            "status": "success",
            "task": dict(self.task),
            "persistent_writes": 1,
            "canonical_scientific_write": False,
        }

    def approve_research_synthesis_result(self, session, task_id: str, expected_state):
        assert expected_state == {
            "state_id": STATE_ID,
            "state_digest": "a" * 64,
        }
        return {
            "status": "success",
            "task": {**self.task, "task_id": task_id, "status": "approved"},
            "research_synthesis": {
                "candidate_id": CANDIDATE_ID,
                "candidate_type": "synthesis",
            },
            "persistent_writes": 2,
            "canonical_scientific_write": True,
        }

    def show_task(self, session, task_id: str):
        return {"current_task": {**self.task, "task_id": task_id}}


def _post(harness: AppHarness, csrf: str, path: str, payload: dict):
    return harness.client.post(
        path,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=payload,
    )


def _create_request(**overrides: object) -> dict:
    request = {
        "question_id": QUESTION_ID,
        "candidate_type": "synthesis",
        "maintenance_intent": "append",
        "target_candidate_id": None,
        "maintenance_goal": "Create one bounded synthetic candidate.",
        "include_review_background": False,
        "executor_id": "codex_cli",
        "approved_content_classes": [
            "canonical_evidence",
            "metadata",
            "operational_context",
            "paper_card_content",
            "research_routing_context",
            "research_synthesis",
        ],
        "idempotency_key": "p8-research-synthesis-api",
    }
    request.update(overrides)
    return request


def _enable_p8_policy(harness: AppHarness) -> None:
    config_path = harness.workspace_root / "workspace.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["agent_policy"] = {
        "registry_version": "p8-v1",
        "allowed_content_classes": P8_CLASSES,
        "execution_scope": "cloud_allowed",
        "max_prompt_bytes": 2_097_152,
        "max_result_bytes": 1_048_576,
    }
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _refresh_fixture_question(harness: AppHarness) -> None:
    session = harness.client.app.state.runtime.session
    assert session is not None
    service = ResearchOrganizationService(session._layout)
    legacy = service.read_question(FIXTURE_QUESTION_ID)
    factual_links = [
        {
            "paper_id": link["paper_id"],
            "selected_card_unit_ids": link["selected_card_unit_ids"],
            "role_in_question": link["role_in_question"],
            "relevance_rationale": link["relevance_rationale"],
            "boundary_refs": link["boundary_refs"],
        }
        for link in legacy["paper_links"]
    ]
    service.promote_question(
        {
            "question_text": legacy["question_text"],
            "scope": legacy["scope"],
            "mapping_status": legacy["mapping_status"],
            "factual_links": factual_links,
            "background_links": [],
        },
        question_id=FIXTURE_QUESTION_ID,
        approval={
            "receipt_id": "p8-app-current-question",
            "approved_by": "user",
            "approved_at": "2026-08-04T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )


def _expected(task: dict) -> dict[str, str]:
    return {
        "expected_state_id": task["state_id"],
        "expected_state_digest": task["state_digest"],
    }


def _fixture_replacement_result(task: dict) -> dict:
    return {
        "contract_version": "p8-research-synthesis-proposal@1.0",
        "task_id": task["task_id"],
        "input_basis_digest": task["input_basis_digest"],
        "candidate_type": "synthesis",
        "maintenance_intent": "replace",
        "target_candidate_id": FIXTURE_SYNTHESIS_ID,
        "duplicate_disposition": "updates_target",
        "payload": {
            "question_id": FIXTURE_QUESTION_ID,
            "title": "Synthetic synthesis 1 revised through App",
            "candidate_status": "keep",
            "rejection_rationale": None,
            "analysis_operator": "aggregate",
            "trace_status": "traceable",
            "paper_card_base": [
                {
                    "paper_id": "paper_f8daed20-fcf0-4ed8-9795-694bd631def9",
                    "card_unit_ids": ["unit_34c69ef0-da0d-4754-a79f-7166f142b3c0"],
                },
                {
                    "paper_id": "paper_08c0dd81-5b44-4d2f-9d32-662fb3e15ae5",
                    "card_unit_ids": ["unit_a99702c0-9324-443e-ad51-07886ed0ceec"],
                },
            ],
            "missing_evidence": ["Independent fabricated replication"],
            "assumptions": ["The synthetic items are comparable"],
            "risk": ["The fixture has no external scientific meaning"],
            "testability": "Inspect the deterministic generated records.",
            "next_action": "Retain for P8 App validation.",
            "claim": "The fabricated records share one revised App-visible pattern.",
            "scope": "Generated fixture records only.",
            "agreement_pattern": "Both synthetic records use bounded text.",
            "conflict_pattern": "No scientific conflict is represented.",
            "boundary_statement": "This candidate is not a scientific fact.",
        },
    }


def test_research_synthesis_reads_create_and_dedicated_approval(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.research_synthesis = FakeResearchSynthesisService()
    runtime.agent = FakeResearchSynthesisAgentService()

    assert app_harness.client.get("/api/research-synthesis/limits").status_code == 200
    listed = app_harness.client.get(
        "/api/research-synthesis/candidates",
        params={
            "question_id": QUESTION_ID,
            "candidate_type": "synthesis",
            "freshness": "current",
            "page_size": 20,
        },
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["candidates"][0]["candidate_id"] == CANDIDATE_ID
    shown = app_harness.client.get(f"/api/research-synthesis/candidates/{CANDIDATE_ID}")
    assert shown.status_code == 200, shown.text
    context = app_harness.client.get(f"/api/research-synthesis/questions/{QUESTION_ID}/context")
    assert context.status_code == 200, context.text

    assert app_harness.client.post("/api/research-synthesis/proposals", json=_create_request()).status_code in {401, 403}
    invalid = _post(
        app_harness,
        csrf,
        "/api/research-synthesis/proposals",
        {**_create_request(), "filesystem_path": "C:/private"},
    )
    assert invalid.status_code == 422
    created = _post(app_harness, csrf, "/api/research-synthesis/proposals", _create_request())
    assert created.status_code == 200, created.text
    app_harness.wait_until_idle()
    task = created.json()["task"]
    expected = {
        "expected_state_id": task["state_id"],
        "expected_state_digest": task["state_digest"],
    }

    generic = _post(app_harness, csrf, f"/api/agent/tasks/{TASK_ID}/approve", expected)
    assert generic.status_code == 409
    approved = _post(
        app_harness,
        csrf,
        f"/api/research-synthesis/proposals/{TASK_ID}/approve",
        expected,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["research_synthesis"]["candidate_id"] == CANDIDATE_ID
    app_harness.wait_until_idle()


@pytest.mark.parametrize(
    "candidate_type",
    ["synthesis", "review_angle", "insight", "cross_view"],
)
def test_four_candidate_types_serialize_to_the_core_task_service(
    app_harness: AppHarness,
    candidate_type: str,
) -> None:
    csrf = app_harness.open_workspace()
    agent = FakeResearchSynthesisAgentService()
    app_harness.client.app.state.runtime.agent = agent

    created = _post(
        app_harness,
        csrf,
        "/api/research-synthesis/proposals",
        _create_request(
            candidate_type=candidate_type,
            idempotency_key=f"p8-{candidate_type}-serialization",
        ),
    )

    assert created.status_code == 200, created.text
    assert agent.requests[-1]["candidate_type"] == candidate_type
    app_harness.wait_until_idle()


def test_research_synthesis_rejects_path_shaped_identifiers_and_invalid_replace(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    assert app_harness.client.get("/api/research-synthesis/candidates/C:%5Cprivate").status_code == 400
    assert app_harness.client.get("/api/research-synthesis/questions/../context").status_code in {400, 404}

    invalid_question = _post(
        app_harness,
        csrf,
        "/api/research-synthesis/proposals",
        _create_request(question_id="question_.."),
    )
    invalid_target = _post(
        app_harness,
        csrf,
        "/api/research-synthesis/proposals",
        _create_request(
            maintenance_intent="replace",
            target_candidate_id="C:/private/candidate",
        ),
    )
    assert invalid_question.status_code == 400
    assert invalid_target.status_code == 400


def test_real_research_synthesis_replace_commits_only_through_dedicated_approval(
    app_harness: AppHarness,
) -> None:
    _enable_p8_policy(app_harness)
    csrf = app_harness.open_workspace()
    _refresh_fixture_question(app_harness)
    knowledge_root = app_harness.workspace_root / "knowledge"
    sources_before = tree_digest(app_harness.workspace_root / "sources")
    unchanged_roots = (
        "paper_cards",
        "evidence",
        "primary_bundles",
        "review_memories",
        "review_bundles",
        "review_queue",
        "questions",
    )
    scientific_before = {
        name: tree_digest(knowledge_root / name)
        for name in unchanged_roots
    }
    synthesis_path = knowledge_root / "step7" / "synthesis.jsonl"
    synthesis_before = synthesis_path.read_bytes()

    listed = app_harness.client.get(
        "/api/research-synthesis/candidates",
        params={"question_id": FIXTURE_QUESTION_ID, "page_size": 20},
    )
    assert listed.status_code == 200, listed.text
    assert {item["candidate_type"] for item in listed.json()["candidates"]} == {
        "synthesis",
        "review_angle",
        "insight",
        "cross_view",
    }

    created = _post(
        app_harness,
        csrf,
        "/api/research-synthesis/proposals",
        {
            "question_id": FIXTURE_QUESTION_ID,
            "candidate_type": "synthesis",
            "maintenance_intent": "replace",
            "target_candidate_id": FIXTURE_SYNTHESIS_ID,
            "maintenance_goal": "Revise the bounded synthetic synthesis through the App.",
            "include_review_background": False,
            "executor_id": "codex_cli",
            "approved_content_classes": P8_CLASSES,
            "idempotency_key": "p8-real-app-replace",
        },
    )
    assert created.status_code == 200, created.text
    task = created.json()["task"]
    app_harness.wait_until_idle()

    inspected = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/inspect-handoff",
        {**_expected(task), "executor_id": "codex_cli"},
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["handoff_preview"]["payload"]["review_background"] == []
    handed_off = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/handoff",
        {**_expected(task), "executor_id": "codex_cli"},
    )
    assert handed_off.status_code == 200, handed_off.text
    leased = handed_off.json()["task"]
    app_harness.wait_until_idle()

    submitted = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/submit",
        {**_expected(leased), "result": _fixture_replacement_result(leased)},
    )
    assert submitted.status_code == 200, submitted.text
    submitted_task = submitted.json()["task"]
    app_harness.wait_until_idle()
    preview = app_harness.client.get(f"/api/agent/tasks/{task['task_id']}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["candidate"]["approval_blocked"] is False

    generic = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/approve",
        _expected(submitted_task),
    )
    assert generic.status_code == 409
    approved = _post(
        app_harness,
        csrf,
        f"/api/research-synthesis/proposals/{task['task_id']}/approve",
        _expected(submitted_task),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["research_synthesis"]["candidate_id"] == FIXTURE_SYNTHESIS_ID
    app_harness.wait_until_idle()

    assert synthesis_path.read_bytes() != synthesis_before
    assert {
        name: tree_digest(knowledge_root / name)
        for name in unchanged_roots
    } == scientific_before
    assert tree_digest(app_harness.workspace_root / "sources") == sources_before
    assert str(app_harness.workspace_root) not in "\n".join(
        (created.text, inspected.text, handed_off.text, submitted.text, preview.text, approved.text)
    )
