from __future__ import annotations

from tests.python.conftest import EXPECTED_ORIGIN, AppHarness


QUESTION_ID = "question_11111111-1111-4111-8111-111111111111"
PAPER_ID = "paper_22222222-2222-4222-8222-222222222222"
CRITERIA_ID = "screeningcriteria_33333333-3333-4333-8333-333333333333"
CRITERIA_REVISION_ID = "screeningcriteriarev_44444444-4444-4444-8444-444444444444"
DECISION_ID = "screeningdecision_55555555-5555-4555-8555-555555555555"
CRITERION_ID = "screeningcriterion_66666666-6666-4666-8666-666666666666"
TASK_ID = "agenttask_77777777-7777-4777-8777-777777777777"
STATE_ID = "agenttaskstate_88888888-8888-4888-8888-888888888888"


class FakeScreeningService:
    criteria = {
        "criteria_id": CRITERIA_ID,
        "question_id": QUESTION_ID,
        "title": "Synthetic criteria",
        "scope": "Synthetic scope.",
        "inclusion_criteria": [{"criterion_id": CRITERION_ID, "text": "Include synthetic papers."}],
        "exclusion_criteria": [],
        "notes": "",
        "status": "active",
        "revision_id": CRITERIA_REVISION_ID,
        "criteria_digest": "c" * 64,
    }

    def limits(self, session):
        return {"status": "success", "max_page_size": 100, "outcomes": ["included", "excluded"]}

    def list_criteria(self, session, **kwargs):
        return {"status": "success", "criteria": [dict(self.criteria)], "next_cursor": None, "persistent_writes": 0}

    def show_criteria(self, session, criteria_id: str):
        return {"status": "success", "criteria": {**self.criteria, "criteria_id": criteria_id}, "persistent_writes": 0}

    def promote_criteria(self, session, request):
        assert request["receipt_id"].startswith("app-screening-")
        return {"status": "success", "result": "committed", "criteria": dict(self.criteria), "persistent_writes": 1, "canonical_scientific_write": False}

    def list_decisions(self, session, **kwargs):
        return {"status": "success", "decisions": [], "next_cursor": None, "persistent_writes": 0}

    def show_decision(self, session, decision_id: str):
        return {"status": "success", "decision": {"decision_id": decision_id}, "persistent_writes": 0}

    def promote_decision(self, session, request):
        assert request["receipt_id"].startswith("app-screening-")
        return {"status": "success", "result": "committed", "decision": {"decision_id": DECISION_ID, "outcome": request["outcome"]}, "persistent_writes": 1, "canonical_scientific_write": False}


class FakeScreeningAgentService:
    task = {
        "task_id": TASK_ID,
        "state_id": STATE_ID,
        "state_digest": "a" * 64,
        "task_kind": "question_screening_criteria_proposal",
        "status": "created",
    }

    def create_question_screening_criteria_proposal(self, session, request):
        assert request["question_id"] == QUESTION_ID
        return {"status": "success", "task": dict(self.task), "persistent_writes": 1, "canonical_scientific_write": False}

    def create_question_screening_decision_proposal(self, session, request):
        return {"status": "success", "task": {**self.task, "task_kind": "question_screening_decision_proposal"}, "persistent_writes": 1, "canonical_scientific_write": False}

    def approve_question_screening_result(self, session, task_id: str, expected_state):
        return {"status": "success", "task": {**self.task, "task_id": task_id, "status": "approved"}, "screening": {"screening_kind": "criteria", "record_id": CRITERIA_ID}, "persistent_writes": 2, "canonical_scientific_write": False}

    def show_task(self, session, task_id: str):
        return {"current_task": {**self.task, "task_id": task_id}}


def _post(harness: AppHarness, csrf: str, path: str, payload: dict):
    return harness.client.post(
        path,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=payload,
    )


def test_manual_screening_reads_and_writes_are_session_bound(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.screening = FakeScreeningService()

    listed = app_harness.client.get("/api/screening/criteria", params={"question_id": QUESTION_ID})
    assert listed.status_code == 200
    assert listed.json()["criteria"][0]["criteria_id"] == CRITERIA_ID

    criteria_request = {
        "question_id": QUESTION_ID,
        "title": "Synthetic criteria",
        "scope": "Synthetic scope.",
        "inclusion_criteria": [{"text": "Include synthetic papers."}],
        "exclusion_criteria": [],
        "notes": "",
        "status": "active",
    }
    assert app_harness.client.post("/api/screening/criteria", json=criteria_request).status_code in {401, 403}
    promoted = _post(app_harness, csrf, "/api/screening/criteria", criteria_request)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["canonical_scientific_write"] is False
    app_harness.wait_until_idle()

    decision_request = {
        "question_id": QUESTION_ID,
        "paper_id": PAPER_ID,
        "outcome": "included",
        "criteria_revision_id": CRITERIA_REVISION_ID,
        "criteria_digest": "c" * 64,
        "criterion_dispositions": [{"criterion_id": CRITERION_ID, "disposition": "met", "rationale": "Synthetic match."}],
        "basis_scope": "metadata",
        "rationale": "Synthetic inclusion.",
        "known_limitations": [],
    }
    decision = _post(app_harness, csrf, "/api/screening/decisions", decision_request)
    assert decision.status_code == 200, decision.text
    assert decision.json()["decision"]["outcome"] == "included"
    app_harness.wait_until_idle()


def test_screening_proposal_uses_dedicated_approval(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.agent = FakeScreeningAgentService()

    request = {
        "question_id": QUESTION_ID,
        "criteria_id": None,
        "proposal_goal": "Propose bounded criteria.",
        "executor_id": "codex_cli",
        "approved_content_classes": ["operational_context", "research_routing_context"],
        "idempotency_key": "screening-criteria-test",
    }
    created = _post(app_harness, csrf, "/api/screening/proposals/criteria", request)
    assert created.status_code == 200, created.text
    app_harness.wait_until_idle()
    task = created.json()["task"]
    expected = {"expected_state_id": task["state_id"], "expected_state_digest": task["state_digest"]}

    generic = _post(app_harness, csrf, f"/api/agent/tasks/{TASK_ID}/approve", expected)
    assert generic.status_code == 409
    approved = _post(app_harness, csrf, f"/api/screening/proposals/{TASK_ID}/approve", expected)
    assert approved.status_code == 200, approved.text
    assert approved.json()["screening"]["record_id"] == CRITERIA_ID
    app_harness.wait_until_idle()


def test_screening_rejects_path_shaped_identifiers(app_harness: AppHarness) -> None:
    app_harness.open_workspace()
    response = app_harness.client.get("/api/screening/criteria/C:%5Cprivate")
    assert response.status_code == 400
