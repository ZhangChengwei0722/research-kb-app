from __future__ import annotations

from conftest import EXPECTED_ORIGIN, AppHarness


class FakeOrganizationService:
    def list_directions(self, session, *, page_size: int, cursor: str | None):
        return {
            "status": "success",
            "directions": [
                {
                    "direction_id": "direction_11111111-1111-4111-8111-111111111111",
                    "name": "Synthetic direction",
                    "scope": "Synthetic scope.",
                    "status": "active",
                    "gap_notes": [],
                    "revision_id": "orgrev_11111111-1111-4111-8111-111111111111",
                    "links_count": 0,
                }
            ],
            "next_cursor": None,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def show_direction(self, session, target_id: str):
        return {"status": "success", "direction": {"direction_id": target_id}, "persistent_writes": 0}

    def list_field_map_entries(self, session, *, page_size: int, cursor: str | None):
        return {"status": "success", "field_map_entries": [], "next_cursor": None, "persistent_writes": 0}

    def show_field_map_entry(self, session, target_id: str):
        return {"status": "success", "field_map_entry": {"field_map_entry_id": target_id}, "persistent_writes": 0}

    def list_questions(self, session, *, page_size: int, cursor: str | None):
        return {"status": "success", "questions": [], "next_cursor": None, "persistent_writes": 0}

    def show_question(self, session, target_id: str):
        return {"status": "success", "question": {"question_id": target_id}, "persistent_writes": 0}

    def show_paper_context(self, session, paper_id: str):
        return {"status": "success", "paper_id": paper_id, "directions": [], "persistent_writes": 0}


class FakeOrganizationAgentService:
    task = {
        "task_id": "task_22222222-2222-4222-8222-222222222222",
        "state_id": "taskstate_33333333-3333-4333-8333-333333333333",
        "state_digest": "a" * 64,
        "task_kind": "organization_proposal",
        "status": "created",
    }

    def create_organization_proposal(self, session, request):
        assert request["paper_ids"] == ["paper_44444444-4444-4444-8444-444444444444"]
        return {"status": "success", "task": dict(self.task), "persistent_writes": 1}

    def approve_organization_result(self, session, task_id: str, expected_state):
        return {
            "status": "success",
            "task": {**self.task, "task_id": task_id, "status": "approved"},
            "organization": {
                "target_kind": "direction",
                "target_id": "direction_11111111-1111-4111-8111-111111111111",
                "revision_id": "orgrev_11111111-1111-4111-8111-111111111111",
                "revision_number": 1,
                "content_digest": "b" * 64,
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


def test_organization_reads_create_and_dedicated_approval(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.organization = FakeOrganizationService()
    runtime.agent = FakeOrganizationAgentService()

    listed = app_harness.client.get("/api/organization/directions", params={"page_size": 20})
    assert listed.status_code == 200, listed.text
    assert listed.json()["directions"][0]["name"] == "Synthetic direction"
    shown = app_harness.client.get(
        "/api/organization/directions/direction_11111111-1111-4111-8111-111111111111"
    )
    assert shown.status_code == 200, shown.text
    assert app_harness.client.get("/api/organization/field-map-entries").status_code == 200
    assert app_harness.client.get("/api/organization/questions").status_code == 200

    request = {
        "target_kind": "direction",
        "target_id": None,
        "proposal_goal": "Create one bounded synthetic direction.",
        "paper_ids": ["paper_44444444-4444-4444-8444-444444444444"],
        "include_review_background": False,
        "executor_id": "codex_cli",
        "approved_content_classes": [
            "paper_card_content",
            "research_routing_context",
            "operational_context",
        ],
        "idempotency_key": "synthetic-organization-api",
    }
    assert app_harness.client.post("/api/organization/proposals", json=request).status_code in {401, 403}
    invalid = _post(app_harness, csrf, "/api/organization/proposals", {**request, "filesystem_path": "C:/private"})
    assert invalid.status_code == 422
    created = _post(app_harness, csrf, "/api/organization/proposals", request)
    assert created.status_code == 200, created.text
    app_harness.wait_until_idle()

    task = created.json()["task"]
    expected = {
        "expected_state_id": task["state_id"],
        "expected_state_digest": task["state_digest"],
    }
    generic = _post(
        app_harness,
        csrf,
        f"/api/agent/tasks/{task['task_id']}/approve",
        expected,
    )
    assert generic.status_code == 409
    approved = _post(
        app_harness,
        csrf,
        f"/api/organization/proposals/{task['task_id']}/approve",
        expected,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["canonical_scientific_write"] is True
    app_harness.wait_until_idle()


def test_organization_api_rejects_path_shaped_ids(app_harness: AppHarness) -> None:
    app_harness.open_workspace()

    response = app_harness.client.get("/api/organization/directions/C:%5Cprivate")

    assert response.status_code == 400
