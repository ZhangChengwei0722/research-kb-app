from __future__ import annotations

import json
import sys
from pathlib import Path

from research_kb.services import ResearchOrganizationService
from research_kb.workspace import WorkspaceLayout


QUESTION_ID = "question_272dfde3-ef0f-4205-b9a1-65623487637d"


def main() -> int:
    layout = WorkspaceLayout.load(Path(sys.argv[1]).resolve())
    service = ResearchOrganizationService(layout)
    question = service.read_question(QUESTION_ID)
    if question["compatibility_source"] == "p7_revision":
        print(json.dumps({"status": "already_current", "question_id": QUESTION_ID}))
        return 0

    factual_links = [
        {
            "paper_id": link["paper_id"],
            "selected_card_unit_ids": link["selected_card_unit_ids"],
            "role_in_question": link["role_in_question"],
            "relevance_rationale": link["relevance_rationale"],
            "boundary_refs": link["boundary_refs"],
        }
        for link in question["paper_links"]
    ]
    bundle, transaction = service.promote_question(
        {
            "question_text": question["question_text"],
            "scope": question["scope"],
            "mapping_status": question["mapping_status"],
            "factual_links": factual_links,
            "background_links": [],
        },
        question_id=QUESTION_ID,
        approval={
            "receipt_id": "p8-e2e-current-question",
            "approved_by": "user",
            "approved_at": "2026-08-04T00:00:00Z",
            "origin": "user_authored",
        },
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    print(
        json.dumps(
            {
                "status": "created",
                "question_id": QUESTION_ID,
                "revision_id": bundle["active_revision_id"],
                "persistent_writes": 0 if transaction is None else 1,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
