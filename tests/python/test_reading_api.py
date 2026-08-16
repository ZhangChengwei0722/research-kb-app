from __future__ import annotations

from conftest import EXPECTED_ORIGIN, AppHarness, tree_digest


PRIMARY_PAPER_ID = "paper_08c0dd81-5b44-4d2f-9d32-662fb3e15ae5"
SECOND_PAPER_ID = "paper_f8daed20-fcf0-4ed8-9795-694bd631def9"
REVIEW_PAPER_ID = "paper_c5743fa9-6803-4e6a-9928-46b07399d761"
EVIDENCE_ID = "evidence_20cbe39d-3cba-4ba8-980f-bc6399026bf6"


def test_reading_routes_are_authenticated_id_only_and_zero_write(app_harness: AppHarness) -> None:
    unauthenticated = app_harness.client.get(f"/api/reading/papers/{PRIMARY_PAPER_ID}")
    unauthenticated_evidence = app_harness.client.get(f"/api/reading/evidence/{EVIDENCE_ID}")
    unauthenticated_compare = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"paper_ids": [SECOND_PAPER_ID, PRIMARY_PAPER_ID]},
    )
    assert unauthenticated.status_code == 401
    assert unauthenticated_evidence.status_code == 401
    assert unauthenticated_compare.status_code == 401

    csrf = app_harness.open_workspace()
    before = tree_digest(app_harness.workspace_root / "knowledge")

    primary = app_harness.client.get(f"/api/reading/papers/{PRIMARY_PAPER_ID}")
    review = app_harness.client.get(f"/api/reading/papers/{REVIEW_PAPER_ID}")
    evidence = app_harness.client.get(f"/api/reading/evidence/{EVIDENCE_ID}")
    compared = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"paper_ids": [SECOND_PAPER_ID, PRIMARY_PAPER_ID]},
    )

    assert primary.status_code == 200, primary.text
    assert len(primary.json()["primary"]["paper_card"]["sections"]) == 7
    assert primary.json()["persistent_writes"] == 0
    assert review.status_code == 200, review.text
    assert review.json()["review"]["review_memory"]["background_only"] is True
    assert review.json()["review"]["factual_support_eligible"] is False
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["evidence"]["quote"].startswith("The fabricated intervention")
    assert compared.status_code == 200, compared.text
    assert [item["paper"]["paper_id"] for item in compared.json()["papers"]] == [
        SECOND_PAPER_ID,
        PRIMARY_PAPER_ID,
    ]
    assert compared.json()["semantic_comparison"] is None

    rendered = "\n".join((primary.text, review.text, evidence.text, compared.text))
    for forbidden in ("source_ref", "source_fingerprint", str(app_harness.workspace_root)):
        assert forbidden not in rendered
    assert tree_digest(app_harness.workspace_root / "knowledge") == before


def test_reading_compare_and_ids_fail_closed(app_harness: AppHarness) -> None:
    csrf = app_harness.open_workspace()

    path_shaped = app_harness.client.get("/api/reading/papers/paper_..")
    path_shaped_evidence = app_harness.client.get("/api/reading/evidence/evidence_..")
    missing_csrf = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN},
        json={"paper_ids": [SECOND_PAPER_ID, PRIMARY_PAPER_ID]},
    )
    duplicate = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"paper_ids": [PRIMARY_PAPER_ID, PRIMARY_PAPER_ID]},
    )
    oversized = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"paper_ids": [PRIMARY_PAPER_ID] * 5},
    )
    undersized = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"paper_ids": [PRIMARY_PAPER_ID]},
    )
    path_shaped_compare = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"paper_ids": [PRIMARY_PAPER_ID, "paper_.."]},
    )
    unknown_field = app_harness.client.post(
        "/api/reading/compare",
        headers={"Origin": EXPECTED_ORIGIN, "X-RKB-CSRF": csrf},
        json={"paper_ids": [SECOND_PAPER_ID, PRIMARY_PAPER_ID], "semantic_comparison": True},
    )

    assert path_shaped.status_code == 400
    assert path_shaped_evidence.status_code == 400
    assert missing_csrf.status_code == 401
    assert duplicate.status_code in {400, 409, 422}
    assert oversized.status_code == 422
    assert undersized.status_code == 422
    assert path_shaped_compare.status_code == 400
    assert unknown_field.status_code == 422
