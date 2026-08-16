from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from research_kb.bundle import load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.services import PipelineJobService, SourceAdequacyService
from research_kb.workspace import WorkspaceLayout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace_config", type=Path)
    parser.add_argument("paper_title")
    args = parser.parse_args()

    layout = WorkspaceLayout.load(args.workspace_config.resolve())
    entries = load_workspace_entries(layout)
    papers = [
        item
        for item in records_of_kind(entries, "registry-paper")
        if item["bibliography"]["title"] == args.paper_title
    ]
    if len(papers) != 1:
        raise RuntimeError("Synthetic paper selection is not unique")
    paper_id = papers[0]["paper_id"]
    service = SourceAdequacyService(layout)
    existing_gate = service.gate(
        paper_id=paper_id,
        requested_operation="continuous_text_evidence",
    )
    if existing_gate["status"] == "allowed":
        print(json.dumps({
            "status": "success",
            "paper_id": paper_id,
            "profile_id": existing_gate["profile_id"],
            "capability_status": existing_gate["capability_status"],
        }, sort_keys=True))
        return 0

    jobs = PipelineJobService(layout)
    created = jobs.create(
        requested_route="semantic_processing",
        requested_depth="semantic_gate",
        current_node="source_adequacy_review",
        input_refs=[paper_id],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["assess_source_adequacy"],
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        idempotency_key=f"p5b-e2e-source-adequacy:{paper_id}",
        actor="user",
    )
    current_job = jobs.transition(
        created.state["job_id"],
        expected_state_id=created.state["state_id"],
        expected_state_digest=canonical_digest(created.state),
        status="running",
        current_node="source_adequacy_assessment",
        wait_reason=None,
        output_refs=[],
        retry_increment=0,
        recovery_action=None,
        actor="user",
    ).state
    basis_profile_id = existing_gate["profile_id"]
    capability_status = existing_gate["capability_status"]
    if basis_profile_id is None:
        basis = service.assess(
            paper_id=paper_id,
            job_id=current_job["job_id"],
            requested_operation="continuous_text_evidence",
            actor="cli",
        ).profile
        basis_profile_id = basis["profile_id"]
        capability_status = basis["capabilities"]["continuous_text_citation"]["status"]
    if capability_status != "uncertain":
        raise RuntimeError("Synthetic Source Adequacy basis is not uncertain")

    result = service.assess(
        paper_id=paper_id,
        job_id=current_job["job_id"],
        requested_operation="continuous_text_evidence",
        actor="user",
        basis_profile_id=basis_profile_id,
        user_decision={
            "decision": "accept_uncertainty",
            "capabilities": ["continuous_text_citation"],
            "reason": "Synthetic single-line PDF accepted only for deterministic P5-B browser validation.",
        },
    )
    jobs.transition(
        current_job["job_id"],
        expected_state_id=current_job["state_id"],
        expected_state_digest=canonical_digest(current_job),
        status="completed",
        current_node="source_adequacy_accepted",
        wait_reason=None,
        output_refs=[basis_profile_id, result.profile["profile_id"]],
        retry_increment=0,
        recovery_action=None,
        actor="user",
    )
    print(json.dumps({
        "status": "success",
        "paper_id": paper_id,
        "profile_id": result.profile["profile_id"],
        "capability_status": result.profile["capabilities"]["continuous_text_citation"]["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
