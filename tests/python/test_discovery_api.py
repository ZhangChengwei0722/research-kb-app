from __future__ import annotations

from copy import deepcopy

from conftest import EXPECTED_ORIGIN, tree_digest


CANDIDATE_ID = "discovery_a1111111-1111-4111-8111-111111111111"
RESULT_KEY = "doi:10.0000/discovery.api"


def _report() -> dict:
    return {
        "status": "success",
        "interface_version": "1.0",
        "provider": "europe-pmc",
        "provider_api_version": "synthetic-6.9",
        "query": {
            "date_from": "2026-07-27",
            "date_until": "2026-08-03",
            "title_keywords": ["targeted degradation"],
            "abstract_keywords": ["delivery"],
            "keyword_mode": "any",
            "include_preprints": True,
            "max_results": 15,
        },
        "provider_hit_count": 1,
        "scanned_result_count": 1,
        "returned_result_count": 1,
        "truncated": False,
        "persistent_writes": 0,
        "results": [
            {
                "result_key": RESULT_KEY,
                "title": "Synthetic discovery API paper",
                "authors": ["Alpha Researcher"],
                "first_publication_date": "2026-08-01",
                "journal_or_server": "Synthetic Journal",
                "doi": "10.0000/discovery.api",
                "paper_type": "article",
                "publication_types": ["Journal Article"],
                "abstract": "Delivery for targeted degradation.",
                "matched_keywords": ["targeted degradation", "delivery"],
                "match_location": "both",
                "discovery_sources": [
                    {"provider": "europe-pmc", "source": "MED", "record_id": "API-1"}
                ],
                "full_text_status": "open_access",
                "version_relationship": {"status": "unresolved", "related_doi": None},
                "possible_duplicate_result_keys": [],
            }
        ],
    }


class FakeDiscoveryApplicationService:
    def __init__(self):
        self.report = _report()
        self.candidate = None
        self.calls = []

    def limits(self):
        return {
            "status": "success",
            "interface_version": "1.10",
            "provider": "europe-pmc",
            "max_results": 15,
            "max_date_span_days": 31,
            "max_page_size": 100,
        }

    def search(self, request):
        self.calls.append(("search", deepcopy(request)))
        return deepcopy(self.report)

    def select(self, session, report, result_keys, *, actor):
        self.calls.append(("select", deepcopy(report), list(result_keys), actor))
        self.candidate = {
            **deepcopy(report["results"][0]),
            "candidate_id": CANDIDATE_ID,
            "selection_status": "user_selected",
            "source_status": "metadata_only",
            "acquisition_status": "not_started",
            "not_evidence": True,
        }
        return {
            "status": "success",
            "result": "updated",
            "selected_candidate_ids": [CANDIDATE_ID],
            "created_candidate_ids": [CANDIDATE_ID],
            "updated_candidate_ids": [],
            "unchanged_candidate_ids": [],
            "persistent_writes": 1,
            "event_id": "event_a1111111-1111-4111-8111-111111111111",
            "target": "discovery/candidates.jsonl",
        }

    def list_candidates(self, session, *, page_size, cursor=None):
        values = [] if self.candidate is None else [self._summary()]
        return {
            "status": "success",
            "interface_version": "1.10",
            "candidate_count": len(values),
            "page_size": page_size,
            "candidates": values,
            "next_cursor": None,
            "persistent_writes": 0,
        }

    def show_candidate(self, session, candidate_id):
        return {"status": "success", "interface_version": "1.0", "candidate": deepcopy(self.candidate)}

    def resolve(self, session, candidate_id):
        self.calls.append(("resolve", candidate_id))
        return {
            "status": "success",
            "interface_version": "1.0",
            "candidate_id": candidate_id,
            "result_key": RESULT_KEY,
            "provider": "europe-pmc",
            "provider_api_version": "synthetic-6.9",
            "resolution_context_id": "resolution_sha256_" + "a" * 64,
            "resolution_status": "auto_acquisition_eligible",
            "provider_asset_ref": {"opaque": True},
            "access_basis": "repository_open_access",
            "license_observation": "provider_oa_policy_no_license_text",
            "manual_reason": None,
            "persistent_writes": 0,
        }

    def acquire(self, session, candidate_id, *, actor):
        self.calls.append(("acquire", candidate_id, actor))
        self.candidate["acquisition_status"] = "acquired"
        return {
            "status": "success",
            "interface_version": "1.0",
            "result": "updated",
            "candidate_id": candidate_id,
            "provider": "europe-pmc",
            "resolution_context_id": "resolution_sha256_" + "a" * 64,
            "source_ref": {"root_id": "sources", "relative_path": f"inbox/{candidate_id}.pdf"},
            "source_fingerprint": {"algorithm": "sha256", "value": "b" * 64},
            "content_size_bytes": 100,
            "content_type": "application/pdf",
            "persistent_writes": 2,
            "event_id": "event_b2222222-2222-4222-8222-222222222222",
        }

    def inspect_acquired(self, session, candidate_id):
        return {
            "status": "success",
            "interface_version": "1.0",
            "candidate_id": candidate_id,
            "source": {"root_id": "sources", "relative_path": f"inbox/{candidate_id}.pdf", "fingerprint_algorithm": "sha256"},
            "registration": {"state": "unregistered", "paper_ids": []},
            "domain_profile": {"id": "domain-alpha"},
            "registry_metadata": {"bibliography": {"title": self.candidate["title"]}},
            "persistent_writes": 0,
        }

    def _summary(self):
        return {
            "candidate_id": CANDIDATE_ID,
            "result_key": RESULT_KEY,
            "title": self.candidate["title"],
            "doi": self.candidate["doi"],
            "first_publication_date": self.candidate["first_publication_date"],
            "paper_type": self.candidate["paper_type"],
            "full_text_status": self.candidate["full_text_status"],
            "acquisition_status": self.candidate["acquisition_status"],
            "target_question_ids": [],
            "selection_context_count": 1,
        }


def test_discovery_search_selection_resolution_acquisition_are_separate(app_harness) -> None:
    csrf = app_harness.open_workspace()
    service = FakeDiscoveryApplicationService()
    app_harness.client.app.state.runtime.discovery = service
    headers = {"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf}
    before_search = tree_digest(app_harness.workspace_root)

    limits = app_harness.client.get("/api/discovery/limits")
    search = app_harness.client.post(
        "/api/discovery/search",
        headers=headers,
        json={"request_version": "1.0", **service.report["query"]},
    )

    assert limits.status_code == search.status_code == 200
    assert search.json()["persistent_writes"] == 0
    assert tree_digest(app_harness.workspace_root) == before_search
    assert app_harness.client.get("/api/discovery/candidates").json()["candidate_count"] == 0

    selected = app_harness.client.post(
        "/api/discovery/select",
        headers=headers,
        json={"report": search.json(), "result_keys": [RESULT_KEY]},
    )
    assert selected.status_code == 200
    assert selected.json()["selected_candidate_ids"] == [CANDIDATE_ID]
    assert service.calls[-1][-1] == "user"
    assert app_harness.client.get("/api/discovery/candidates").json()["candidate_count"] == 1

    resolved = app_harness.client.post(
        f"/api/discovery/candidates/{CANDIDATE_ID}/resolve",
        headers=headers,
        json={},
    )
    assert resolved.json()["resolution_status"] == "auto_acquisition_eligible"
    assert service.candidate["acquisition_status"] == "not_started"

    acquired = app_harness.client.post(
        f"/api/discovery/candidates/{CANDIDATE_ID}/acquire",
        headers=headers,
        json={},
    )
    assert acquired.json()["persistent_writes"] == 2
    assert "source_ref" not in acquired.json()
    assert "source_fingerprint" not in acquired.json()
    assert service.calls[-1] == ("acquire", CANDIDATE_ID, "user")

    handoff = app_harness.client.get(
        f"/api/discovery/candidates/{CANDIDATE_ID}/intake-handoff"
    )
    assert handoff.json()["registration"] == {"state": "unregistered", "paper_ids": []}
    assert "source" not in handoff.json()
    assert "registry_metadata" not in handoff.json()
    assert "paper_id" not in handoff.json()
    assert app_harness.client.get("/api/catalog/status").json()["projection_state"] == "missing"


def test_discovery_mutations_require_csrf_and_strict_bodies(app_harness) -> None:
    csrf = app_harness.open_workspace()
    service = FakeDiscoveryApplicationService()
    app_harness.client.app.state.runtime.discovery = service

    missing_csrf = app_harness.client.post(
        "/api/discovery/search",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"request_version": "1.0", **service.report["query"]},
    )
    extra_field = app_harness.client.post(
        "/api/discovery/search",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"request_version": "1.0", **service.report["query"], "provider_url": "https://example.invalid"},
    )
    bad_candidate = app_harness.client.get("/api/discovery/candidates/..%2Fsecret")

    assert missing_csrf.status_code == 401
    assert extra_field.status_code == 422
    assert bad_candidate.status_code in {400, 404}
