from __future__ import annotations

from typing import Any

import pytest
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError

from conftest import EXPECTED_ORIGIN, AppHarness


TAG_ID = "tag_11111111-1111-4111-8111-111111111111"
TAG_REVISION_ID = "tagrev_22222222-2222-4222-8222-222222222222"
TAG_LINK_REVISION_ID = "taglinkrev_33333333-3333-4333-8333-333333333333"
PAPER_ID = "paper_44444444-4444-4444-8444-444444444444"


class FakeTagService:
    def __init__(self) -> None:
        self.session = None
        self.requests: list[tuple[str, dict[str, Any], str | None]] = []
        self.mutation_result = "committed"

    def _bind(self, session) -> None:
        if self.session is None:
            self.session = session
        assert session is self.session

    def limits(self, session):
        self._bind(session)
        return {"max_page_size": 100, "target_kinds": ["paper", "direction", "field_map_entry", "question"]}

    def list_tags(self, session, *, include_archived: bool, page_size: int, cursor: str | None):
        self._bind(session)
        self.requests.append(("list", {"include_archived": include_archived, "page_size": page_size}, cursor))
        return {
            "status": "success",
            "tags": [{"tag_id": TAG_ID, "name": "Synthetic Tag", "status": "active"}],
            "next_cursor": None,
            "persistent_writes": 0,
        }

    def show_tag(self, session, tag_id: str):
        self._bind(session)
        return {
            "status": "success",
            "tag": {"tag_id": tag_id, "name": "Synthetic Tag", "status": "active"},
            "assignments": [],
            "persistent_writes": 0,
        }

    def list_target_tags(self, session, *, target_kind: str, target_id: str):
        self._bind(session)
        return {
            "status": "success",
            "target_kind": target_kind,
            "target_id": target_id,
            "target_availability": "unavailable",
            "tags": [{"tag_id": TAG_ID, "name": "Synthetic Tag"}],
            "persistent_writes": 0,
        }

    def promote_tag(self, session, request):
        self._bind(session)
        self.requests.append(("promote", dict(request), None))
        assert request["receipt_id"].startswith("app-tag-")
        assert len(request["receipt_id"]) <= 200
        assert "actor" not in request and "authority" not in request
        return {
            "status": "success",
            "result": self.mutation_result,
            "tag": {"tag_id": request.get("tag_id", TAG_ID), "revision_id": TAG_REVISION_ID},
            "persistent_writes": 1 if self.mutation_result == "committed" else 0,
            "canonical_scientific_write": False,
        }

    def set_assignment(self, session, request):
        self._bind(session)
        self.requests.append(("assignment", dict(request), None))
        assert request["receipt_id"].startswith("app-tag-")
        return {
            "status": "success",
            "result": self.mutation_result,
            "assignment": {
                "tag_id": request["tag_id"],
                "target_kind": request["target_kind"],
                "target_id": request["target_id"],
                "state": request["state"],
                "revision_id": TAG_LINK_REVISION_ID,
            },
            "persistent_writes": 1 if self.mutation_result == "committed" else 0,
            "canonical_scientific_write": False,
        }


class FakeCatalogQuery:
    def __init__(self) -> None:
        self.filters: dict[str, Any] | None = None

    def search(self, **filters):
        self.filters = filters
        return {"status": "success", "items": [], "next_cursor": None}


class FailingTagService(FakeTagService):
    def promote_tag(self, session, request):
        self._bind(session)
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                "tag-application-request",
                None,
                "/name",
                "synthetic rejection",
            )
        )


def _post(harness: AppHarness, csrf: str, path: str, payload: dict[str, Any]):
    return harness.client.post(
        path,
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json=payload,
    )


def test_tag_reads_are_session_bound_and_catalog_forwards_tag_filter(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    tags = FakeTagService()
    query = FakeCatalogQuery()
    runtime.tags = tags
    runtime.query = query

    listed = app_harness.client.get("/api/tags", params={"include_archived": True, "page_size": 25})
    shown = app_harness.client.get(f"/api/tags/{TAG_ID}")
    target = app_harness.client.get(f"/api/tag-targets/paper/{PAPER_ID}")
    catalog = app_harness.client.get("/api/catalog/items", params={"tag_id": TAG_ID, "page_size": 10})

    assert listed.status_code == 200, listed.text
    assert shown.status_code == 200, shown.text
    assert target.status_code == 200, target.text
    assert target.json()["target_availability"] == "unavailable"
    assert "path" not in target.text.lower()
    assert catalog.status_code == 200, catalog.text
    assert query.filters is not None and query.filters["tag_id"] == TAG_ID
    assert tags.session is runtime.session
    assert csrf


def test_tag_promote_and_assignment_use_backend_receipts_and_tag_operation(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    tags = FakeTagService()
    runtime.tags = tags
    scheduled: list[str] = []
    runtime._try_schedule_rebuild = lambda: scheduled.append("rebuild")

    create = _post(
        app_harness,
        csrf,
        "/api/tags/promote",
        {"name": "Synthetic Tag", "description": "Bounded.", "aliases": ["ST"], "status": "active"},
    )
    revise = _post(
        app_harness,
        csrf,
        "/api/tags/promote",
        {"tag_id": TAG_ID, "name": "Renamed", "expected_revision_id": TAG_REVISION_ID},
    )
    archive = _post(
        app_harness,
        csrf,
        "/api/tags/promote",
        {"tag_id": TAG_ID, "status": "archived", "expected_revision_id": TAG_REVISION_ID},
    )
    assign = _post(
        app_harness,
        csrf,
        "/api/tag-assignments",
        {"tag_id": TAG_ID, "target_kind": "paper", "target_id": PAPER_ID, "state": "assigned"},
    )
    remove = _post(
        app_harness,
        csrf,
        "/api/tag-assignments",
        {
            "tag_id": TAG_ID,
            "target_kind": "paper",
            "target_id": PAPER_ID,
            "state": "removed",
            "expected_revision_id": TAG_LINK_REVISION_ID,
        },
    )

    for response in (create, revise, archive, assign, remove):
        assert response.status_code == 200, response.text
        assert response.json()["operation"]["category"] == "tag"
    assert len(scheduled) == 5
    assert runtime.catalog_status()["projection_state"] == "stale"
    assert len({request[1]["receipt_id"] for request in tags.requests if request[0] != "list"}) == 5


def test_tag_no_change_does_not_mark_catalog_stale_or_schedule_rebuild(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    tags = FakeTagService()
    tags.mutation_result = "no_change"
    runtime.tags = tags
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "current"}
    scheduled: list[str] = []
    runtime._try_schedule_rebuild = lambda: scheduled.append("rebuild")

    response = _post(
        app_harness,
        csrf,
        "/api/tags/promote",
        {"tag_id": TAG_ID, "name": "Synthetic Tag", "expected_revision_id": TAG_REVISION_ID},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == "no_change"
    assert runtime.catalog_status()["projection_state"] == "current"
    assert scheduled == []


def test_rejected_tag_mutation_does_not_mark_catalog_stale_or_schedule_rebuild(
    app_harness: AppHarness,
) -> None:
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.tags = FailingTagService()
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "current"}
    scheduled: list[str] = []
    runtime._try_schedule_rebuild = lambda: scheduled.append("rebuild")

    response = _post(app_harness, csrf, "/api/tags/promote", {"name": "Rejected Tag"})

    assert response.status_code == 400
    assert runtime.catalog_status()["projection_state"] == "current"
    assert scheduled == []


@pytest.mark.parametrize("forbidden", ["receipt_id", "actor", "authority"])
def test_tag_mutations_reject_browser_authority_fields(app_harness: AppHarness, forbidden: str) -> None:
    csrf = app_harness.open_workspace()
    payload = {"name": "Synthetic Tag", forbidden: "browser-controlled"}

    response = _post(app_harness, csrf, "/api/tags/promote", payload)

    assert response.status_code == 422


def test_tag_mutations_require_origin_csrf_and_closed_models(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()
    payload = {"name": "Synthetic Tag"}

    missing_origin = app_harness.client.post(
        "/api/tags/promote",
        headers={"X-RKB-CSRF": csrf},
        json=payload,
    )
    missing_csrf = app_harness.client.post(
        "/api/tags/promote",
        headers={"Origin": EXPECTED_ORIGIN},
        json=payload,
    )
    unknown = _post(app_harness, csrf, "/api/tag-assignments", {
        "tag_id": TAG_ID,
        "target_kind": "paper",
        "target_id": PAPER_ID,
        "state": "assigned",
        "filesystem_path": "C:/private.pdf",
    })
    bad_host = app_harness.client.get("/api/tags", headers={"Host": "localhost"})

    assert missing_origin.status_code == 403
    assert missing_csrf.status_code == 401
    assert unknown.status_code == 422
    assert bad_host.status_code == 400


@pytest.mark.parametrize(
    ("path", "method", "payload"),
    [
        ("/api/tags/C:%5Cprivate", "get", None),
        ("/api/tag-targets/paper/C:%5Cprivate", "get", None),
        ("/api/tags/promote", "post", {"tag_id": "../tag", "status": "archived"}),
        (
            "/api/tag-assignments",
            "post",
            {"tag_id": TAG_ID, "target_kind": "paper", "target_id": "C:/private", "state": "assigned"},
        ),
    ],
)
def test_tag_api_rejects_path_shaped_ids(
    app_harness: AppHarness,
    path: str,
    method: str,
    payload: dict[str, Any] | None,
) -> None:
    csrf = app_harness.open_workspace()

    if method == "get":
        response = app_harness.client.get(path)
    else:
        response = _post(app_harness, csrf, path, payload or {})

    assert response.status_code == 400
