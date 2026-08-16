from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from research_kb.errors import Diagnostic, ResearchKBError

from conftest import EXPECTED_ORIGIN, AppHarness
from research_kb_app.errors import AppOperationError
from research_kb_app.external_reader import ExternalReaderLauncher
from research_kb_app.source_review_confirmation import SourceReviewConfirmationRegistry
from test_source_adequacy_resolution_api import (
    FakeReaderLauncher,
    resolution_harness,
)


JOB_ID = "pipelinejob_intake_source_review"
JOB_STATE_ID = "pipelinejobstate_intake_source_review"
JOB_STATE_DIGEST = "e" * 64
PROFILE_ID = "sourceadequacyprofile_intake_source_review"
PROFILE_DIGEST = "f" * 64
EXPECTED_STATE = {
    "expected_state_id": JOB_STATE_ID,
    "expected_state_digest": JOB_STATE_DIGEST,
}


class FakeIntakeResolutionService:
    def __init__(
        self,
        private_path: Path,
        *,
        interrupt_once: bool = False,
        accepted_resolution_state: str = "accepted_continuation_required",
    ) -> None:
        self.private_path = private_path
        self.interrupt_once = interrupt_once
        self.accepted_resolution_state = accepted_resolution_state
        self.decision_action: str | None = None
        self.decide_calls = 0

    def show_context(self, _session, job_id: str):
        assert job_id == JOB_ID
        state = "review_required"
        if self.decision_action == "accept_uncertainty":
            state = self.accepted_resolution_state
        elif self.decision_action == "remediation_required":
            state = "remediation_required"
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": "1.23",
            "resolution_registry_version": "intake-source-adequacy-resolution-v1",
            "resolution_state": state,
            "job": {
                "job_id": JOB_ID,
                "state_id": JOB_STATE_ID,
                "state_digest": JOB_STATE_DIGEST,
                "status": "waiting_source",
                "current_node": "source_adequacy",
                "wait_reason": "source_incomplete",
            },
            "paper_id": "paper_intake_source_review",
            "basis_profile_id": PROFILE_ID,
            "requested_operation": "basic_review_memory",
            "required_capability": "basic_paper_understanding",
            "document_route": "review",
            "route_reason": None,
            "machine_status": "uncertain",
            "hard_failure": False,
            "freshness": "current",
            "source_availability": "available",
            "known_limitations": ["reading_order_uncertain"],
            "recommended_actions": ["review_source"],
            "allowed_actions": ["accept_uncertainty", "remediation_required"],
            "source_review_required": state == "review_required",
            "decision_action": self.decision_action,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def prepare_source_review(self, _session, job_id: str, expected_state: dict[str, str]):
        assert job_id == JOB_ID
        assert expected_state == {"state_id": JOB_STATE_ID, "state_digest": JOB_STATE_DIGEST}
        return SimpleNamespace(
            handle=SimpleNamespace(basis_profile_digest=PROFILE_DIGEST),
            descriptor={"basis_profile_id": PROFILE_ID},
        )

    @contextmanager
    def open_source_review(self, _session, _handle):
        yield SimpleNamespace(path=self.private_path)

    def decide_and_continue(
        self,
        _session,
        job_id: str,
        expected_state: dict[str, str],
        action: str,
        attestation: str | None = None,
    ):
        assert job_id == JOB_ID
        assert expected_state == {"state_id": JOB_STATE_ID, "state_digest": JOB_STATE_DIGEST}
        if action == "accept_uncertainty":
            assert attestation in {None, "basic_source_reviewed"}
        else:
            assert attestation is None
        self.decide_calls += 1
        if self.decision_action is None:
            self.decision_action = action
            if self.interrupt_once:
                self.interrupt_once = False
                raise ResearchKBError(
                    Diagnostic(
                        code="RKBC-036",
                        record_kind="pipeline_job",
                        record_id=JOB_ID,
                        json_path="/",
                        message="private continuation interruption detail",
                    )
                )
            writes = 3 if action == "accept_uncertainty" else 1
        else:
            assert self.decision_action == action
            writes = 0
        return {
            "status": "success",
            "interface_version": "1.0",
            "application_service_interface_version": "1.23",
            "resolution_state": "continued" if action == "accept_uncertainty" else "remediation_required",
            "job": {
                "job_id": JOB_ID,
                "state_id": "pipelinejobstate_intake_source_review_completed",
                "state_digest": "d" * 64,
                "status": "completed" if action == "accept_uncertainty" else "waiting_user",
                "current_node": "review_semantic_gate" if action == "accept_uncertainty" else "source_adequacy",
                "wait_reason": None if action == "accept_uncertainty" else "source_adequacy_uncertain",
            },
            "paper_id": "paper_intake_source_review",
            "requested_operation": "basic_review_memory",
            "required_capability": "basic_paper_understanding",
            "basis_profile_id": PROFILE_ID,
            "successor_profile_id": "sourceadequacyprofile_intake_successor",
            "decision_action": action,
            "document_route": "review",
            "route_reason": None,
            "refresh_required": action != "accept_uncertainty",
            "persistent_writes": writes,
            "canonical_scientific_write": False,
        }


def _post(
    harness: AppHarness,
    csrf: str | None,
    suffix: str,
    payload: dict,
    *,
    origin: str = EXPECTED_ORIGIN,
    job_id: str = JOB_ID,
):
    headers = {"Origin": origin}
    if csrf is not None:
        headers["X-RKB-CSRF"] = csrf
    return harness.client.post(
        f"/api/intake/jobs/{job_id}/source-adequacy-resolution/{suffix}",
        headers=headers,
        json=payload,
    )


def test_confirmation_registry_binds_closed_intake_subject_and_invalidates_only_that_job() -> None:
    registry = SourceReviewConfirmationRegistry(
        token_factory=lambda: "confirmation-intake-" + "1" * 32,
    )
    issued = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-a",
        subject_kind="intake_job",
        subject_id=JOB_ID,
        subject_state_id=JOB_STATE_ID,
        subject_state_digest=JOB_STATE_DIGEST,
        basis_profile_id=PROFILE_ID,
        basis_profile_digest=PROFILE_DIGEST,
    )
    confirmation_id = str(issued["confirmation_id"])
    accepted = registry.require(
        confirmation_id,
        browser_session_id="browser-a",
        workspace_option_id="workspace-a",
        subject_kind="intake_job",
        subject_id=JOB_ID,
        subject_state_id=JOB_STATE_ID,
        subject_state_digest=JOB_STATE_DIGEST,
        basis_profile_id=PROFILE_ID,
        basis_profile_digest=PROFILE_DIGEST,
    )
    assert accepted.subject_kind == "intake_job"
    assert accepted.subject_id == JOB_ID

    registry.invalidate_subject("agent_task", JOB_ID)
    assert registry.count == 1
    registry.invalidate_subject("intake_job", JOB_ID)
    assert registry.count == 0
    with pytest.raises(AppOperationError):
        registry.issue(
            browser_session_id="browser-a",
            workspace_option_id="workspace-a",
            subject_kind="unknown",
            subject_id=JOB_ID,
            subject_state_id=JOB_STATE_ID,
            subject_state_digest=JOB_STATE_DIGEST,
            basis_profile_id=PROFILE_ID,
            basis_profile_digest=PROFILE_DIGEST,
        )


def test_app_api_runtime_uses_fail_closed_external_reader_guard(
    resolution_harness: AppHarness,
) -> None:
    launcher = resolution_harness.client.app.state.runtime.pdf_launcher
    assert not isinstance(launcher, ExternalReaderLauncher)

    with pytest.raises(
        AssertionError,
        match="External PDF reader launch is forbidden in App API tests",
    ):
        launcher.launch(Path("C:/not-opened-by-tests.pdf"))


def test_intake_resolution_routes_are_closed_serialized_and_private_safe(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    harness = resolution_harness
    private_path = tmp_path / "private" / "intake-secret.pdf"
    assert harness.client.get(
        f"/api/intake/jobs/{JOB_ID}/source-adequacy-resolution"
    ).status_code == 401

    csrf = harness.open_workspace()
    runtime = harness.client.app.state.runtime
    runtime.intake_source_adequacy_resolution = FakeIntakeResolutionService(private_path)
    runtime.pdf_launcher = FakeReaderLauncher(fail=True)

    context = harness.client.get(f"/api/intake/jobs/{JOB_ID}/source-adequacy-resolution")
    assert context.status_code == 200, context.text
    assert context.json()["resolution_state"] == "review_required"

    missing_csrf = _post(harness, None, "open", EXPECTED_STATE)
    bad_origin = _post(harness, csrf, "open", EXPECTED_STATE, origin="http://attacker.invalid")
    unknown_field = _post(harness, csrf, "open", {**EXPECTED_STATE, "source_path": str(private_path)})
    unknown_action = _post(
        harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "override_hard_failure"},
    )
    path_shaped = _post(harness, csrf, "open", EXPECTED_STATE, job_id="..:private")
    assert missing_csrf.status_code == 401
    assert bad_origin.status_code in {401, 403}
    assert unknown_field.status_code == 422
    assert unknown_action.status_code == 422
    assert path_shaped.status_code == 400

    failed_open = _post(harness, csrf, "open", EXPECTED_STATE)
    assert failed_open.status_code == 500
    assert failed_open.json()["diagnostic"]["code"] == "RKBAPP-INTERNAL"
    rendered = "\n".join(
        response.text
        for response in (context, missing_csrf, bad_origin, unknown_field, unknown_action, path_shaped, failed_open)
    )
    assert str(private_path) not in rendered
    assert private_path.name not in rendered


def test_intake_accept_requires_open_then_replays_committed_action_without_confirmation(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    harness = resolution_harness
    csrf = harness.open_workspace()
    runtime = harness.client.app.state.runtime
    resolution = FakeIntakeResolutionService(
        tmp_path / "private" / "intake-secret.pdf",
        interrupt_once=True,
    )
    runtime.intake_source_adequacy_resolution = resolution
    runtime.pdf_launcher = FakeReaderLauncher()

    missing_review = _post(
        harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "accept_uncertainty"},
    )
    assert missing_review.status_code == 409
    assert missing_review.json()["diagnostic"]["code"] == "RKBAPP-SOURCE-REVIEW-REQUIRED"

    opened = _post(harness, csrf, "open", EXPECTED_STATE)
    assert opened.status_code == 200, opened.text
    confirmation_id = opened.json()["confirmation"]["confirmation_id"]
    assert str(resolution.private_path) not in opened.text

    interrupted = _post(
        harness,
        csrf,
        "decide",
        {
            **EXPECTED_STATE,
            "action": "accept_uncertainty",
            "confirmation_id": confirmation_id,
        },
    )
    assert interrupted.status_code == 409
    assert interrupted.json()["diagnostic"]["code"] == "RKBC-036"
    assert runtime.source_review_confirmations.count == 0

    recovered = _post(
        harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "accept_uncertainty"},
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["resolution_state"] == "continued"
    assert recovered.json()["operation"]["category"] == "intake"
    assert resolution.decide_calls == 2
    assert recovered.json()["canonical_scientific_write"] is False
    assert "private continuation interruption detail" not in interrupted.text + recovered.text


def test_intake_remediation_never_accepts_confirmation(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    harness = resolution_harness
    csrf = harness.open_workspace()
    runtime = harness.client.app.state.runtime
    resolution = FakeIntakeResolutionService(tmp_path / "private" / "intake-secret.pdf")
    runtime.intake_source_adequacy_resolution = resolution
    runtime._try_schedule_rebuild = lambda: None

    rejected_confirmation = _post(
        harness,
        csrf,
        "decide",
        {
            **EXPECTED_STATE,
            "action": "remediation_required",
            "confirmation_id": "confirmation-" + "x" * 32,
        },
    )
    assert rejected_confirmation.status_code == 400

    remediated = _post(
        harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "remediation_required"},
    )
    assert remediated.status_code == 200, remediated.text
    assert remediated.json()["resolution_state"] == "remediation_required"
    assert remediated.json()["operation"]["category"] == "intake"
    assert remediated.json()["canonical_scientific_write"] is False


def test_intake_continuation_in_progress_replays_without_a_second_confirmation(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    harness = resolution_harness
    csrf = harness.open_workspace()
    runtime = harness.client.app.state.runtime
    resolution = FakeIntakeResolutionService(
        tmp_path / "private" / "intake-secret.pdf",
        accepted_resolution_state="continuation_in_progress",
    )
    resolution.decision_action = "accept_uncertainty"
    runtime.intake_source_adequacy_resolution = resolution

    recovered = _post(
        harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "accept_uncertainty"},
    )

    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["resolution_state"] == "continued"
    assert recovered.json()["operation"]["category"] == "intake"
    assert resolution.decide_calls == 1
    assert runtime.source_review_confirmations.count == 0


@pytest.mark.parametrize(
    ("action", "expected_writes"),
    [("accept_uncertainty", 3), ("remediation_required", 1)],
)
def test_intake_persistent_resolution_marks_catalog_stale_and_schedules_one_rebuild(
    resolution_harness: AppHarness,
    tmp_path: Path,
    action: str,
    expected_writes: int,
) -> None:
    harness = resolution_harness
    csrf = harness.open_workspace()
    runtime = harness.client.app.state.runtime
    resolution = FakeIntakeResolutionService(tmp_path / "private" / "intake-secret.pdf")
    runtime.intake_source_adequacy_resolution = resolution
    runtime.pdf_launcher = FakeReaderLauncher()
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "current"}
    scheduled: list[str] = []
    runtime._try_schedule_rebuild = lambda: scheduled.append("rebuild")

    payload = {**EXPECTED_STATE, "action": action}
    if action == "accept_uncertainty":
        opened = _post(harness, csrf, "open", EXPECTED_STATE)
        assert opened.status_code == 200, opened.text
        payload["confirmation_id"] = opened.json()["confirmation"]["confirmation_id"]

    response = _post(harness, csrf, "decide", payload)

    assert response.status_code == 200, response.text
    assert response.json()["persistent_writes"] == expected_writes
    assert response.json()["operation"]["category"] == "intake"
    assert runtime.catalog_status()["projection_state"] == "stale"
    assert scheduled == ["rebuild"]


def test_intake_exact_resolution_replay_keeps_catalog_current_without_rebuild(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    harness = resolution_harness
    csrf = harness.open_workspace()
    runtime = harness.client.app.state.runtime
    resolution = FakeIntakeResolutionService(tmp_path / "private" / "intake-secret.pdf")
    resolution.decision_action = "accept_uncertainty"
    runtime.intake_source_adequacy_resolution = resolution
    runtime._catalog_status = {**runtime._catalog_status, "projection_state": "current"}
    scheduled: list[str] = []
    runtime._try_schedule_rebuild = lambda: scheduled.append("rebuild")

    response = _post(
        harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "accept_uncertainty"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["persistent_writes"] == 0
    assert response.json()["operation"]["category"] == "intake"
    assert runtime.catalog_status()["projection_state"] == "current"
    assert scheduled == []
