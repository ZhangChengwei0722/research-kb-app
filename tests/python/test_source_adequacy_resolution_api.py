from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from research_kb.errors import Diagnostic, ResearchKBError
from research_kb.services import WorkspaceBootstrapService

from conftest import (
    EXPECTED_HOST,
    EXPECTED_ORIGIN,
    STARTUP_TOKEN,
    AppHarness,
    portable_fixture_root,
)
from research_kb_app.api import create_app
from research_kb_app.compatibility import load_compatibility
from research_kb_app.config import load_app_config
from research_kb_app.errors import AppOperationError
from research_kb_app.source_review_confirmation import (
    SOURCE_REVIEW_CONFIRMATION_TTL_SECONDS,
    SourceReviewConfirmationRegistry,
)


TASK_ID = "agenttask_source_review"
TASK_STATE_ID = "agenttaskstate_source_review"
TASK_STATE_DIGEST = "a" * 64
PROFILE_ID = "sourceadequacyprofile_source_review"
PROFILE_DIGEST = "c" * 64
EXPECTED_STATE = {
    "expected_state_id": TASK_STATE_ID,
    "expected_state_digest": TASK_STATE_DIGEST,
}


@pytest.fixture
def resolution_harness(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    shutil.copytree(portable_fixture_root(), workspace_root)
    workspace_config = workspace_root / "workspace.yaml"
    workspace_payload = yaml.safe_load(workspace_config.read_text(encoding="utf-8"))
    workspace_payload["workspace"]["local_inbox"] = "./sources/inbox"
    workspace_config.write_text(
        yaml.safe_dump(workspace_payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    (workspace_root / "sources" / "inbox").mkdir(parents=True, exist_ok=True)
    assert WorkspaceBootstrapService(workspace_config).run().exit_code == 0

    frontend_root = tmp_path / "frontend"
    frontend_root.mkdir()
    (frontend_root / "index.html").write_text("<!doctype html><title>test</title>\n", encoding="utf-8")
    config_path = tmp_path / "app-config.json"
    config_path.write_text(
        json.dumps(
            {
                "contract_version": "research-kb-app-config@1.0",
                "workspaces": [{
                    "option_id": "p2-small",
                    "label": "P2 Small Synthetic",
                    "config_path": str(workspace_config.resolve()),
                }],
                "state_root": str((tmp_path / "app-state").resolve()),
                "log_root": str((tmp_path / "app-state" / "logs").resolve()),
                "frontend_root": str(frontend_root.resolve()),
                "request_budgets": {
                    "max_body_bytes": 4096,
                    "max_query_bytes": 1024,
                    "max_page_size": 100,
                    "request_timeout_seconds": 30,
                },
            }
        ),
        encoding="utf-8",
    )
    compatibility = replace(
        load_compatibility(),
        application_service_interface_version="1.22",
        package_version="0.1.1.dev2026080803",
    )
    app = create_app(
        load_app_config(config_path),
        compatibility,
        startup_token=STARTUP_TOKEN,
        expected_host=EXPECTED_HOST,
        expected_origin=EXPECTED_ORIGIN,
    )
    with TestClient(app, base_url=EXPECTED_ORIGIN, raise_server_exceptions=False) as client:
        yield AppHarness(load_app_config(config_path), client, workspace_root)


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _registry_issue(registry: SourceReviewConfirmationRegistry, *, task_id: str = TASK_ID):
    return registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-a",
        task_id=task_id,
        task_state_id=TASK_STATE_ID,
        task_state_digest=TASK_STATE_DIGEST,
        basis_profile_id=PROFILE_ID,
        basis_profile_digest=PROFILE_DIGEST,
    )


def _registry_require(
    registry: SourceReviewConfirmationRegistry,
    confirmation_id: str,
    **overrides: str,
):
    values = {
        "browser_session_id": "browser-a",
        "workspace_option_id": "workspace-a",
        "task_id": TASK_ID,
        "task_state_id": TASK_STATE_ID,
        "task_state_digest": TASK_STATE_DIGEST,
        "basis_profile_id": PROFILE_ID,
        "basis_profile_digest": PROFILE_DIGEST,
        **overrides,
    }
    return registry.require(confirmation_id, **values)


def test_source_review_confirmation_is_bound_expiring_single_use_and_task_invalidated() -> None:
    clock = MutableClock()
    tokens = iter("confirmation-" + str(index).zfill(32) for index in range(10))
    registry = SourceReviewConfirmationRegistry(clock=clock, token_factory=lambda: next(tokens))
    issued = _registry_issue(registry)
    confirmation_id = str(issued["confirmation_id"])

    assert _registry_require(registry, confirmation_id).task_id == TASK_ID
    for field, value in (
        ("browser_session_id", "browser-b"),
        ("workspace_option_id", "workspace-b"),
        ("task_id", "agenttask_other"),
        ("task_state_id", "agenttaskstate_other"),
        ("task_state_digest", "b" * 64),
        ("basis_profile_id", "sourceadequacyprofile_other"),
        ("basis_profile_digest", "d" * 64),
    ):
        with pytest.raises(AppOperationError) as mismatch:
            _registry_require(registry, confirmation_id, **{field: value})
        assert mismatch.value.status_code == 404

    registry.consume(confirmation_id)
    with pytest.raises(AppOperationError) as reused:
        _registry_require(registry, confirmation_id)
    assert reused.value.status_code == 404

    expiring_id = str(_registry_issue(registry)["confirmation_id"])
    clock.value += SOURCE_REVIEW_CONFIRMATION_TTL_SECONDS
    with pytest.raises(AppOperationError) as expired:
        _registry_require(registry, expiring_id)
    assert expired.value.status_code == 410

    first = str(_registry_issue(registry)["confirmation_id"])
    other = str(_registry_issue(registry, task_id="agenttask_other")["confirmation_id"])
    registry.invalidate_task(TASK_ID)
    with pytest.raises(AppOperationError):
        _registry_require(registry, first)
    assert _registry_require(registry, other, task_id="agenttask_other").task_id == "agenttask_other"


class FakeResolutionService:
    def __init__(self, private_path: Path) -> None:
        self.private_path = private_path
        self.decision_action: str | None = None
        self.decide_calls = 0

    def show_context(self, _session, task_id: str):
        assert task_id == TASK_ID
        state = "review_required"
        if self.decision_action == "accept_uncertainty":
            state = "accepted_refresh_required"
        elif self.decision_action == "remediation_required":
            state = "remediation_refresh_required"
        return {
            "status": "success",
            "application_service_interface_version": "1.22",
            "resolution_registry_version": "source-adequacy-resolution-v1",
            "resolution_state": state,
            "task": {
                "task_id": TASK_ID,
                "state_id": TASK_STATE_ID,
                "state_digest": TASK_STATE_DIGEST,
                "task_kind": "review_semantic_processing",
                "status": "submitted",
            },
            "paper_id": "paper_source_review",
            "job_id": "pipelinejob_source_review",
            "basis_profile_id": PROFILE_ID,
            "requested_operation": "continuous_text_evidence",
            "required_capability": "continuous_text_citation",
            "machine_status": "uncertain",
            "hard_failure": False,
            "freshness": "current",
            "known_limitations": ["reading_order_uncertain"],
            "recommended_actions": ["review_reading_order"],
            "allowed_actions": ["accept_uncertainty", "remediation_required"],
            "source_review_required": state == "review_required",
            "decision_action": self.decision_action,
            "persistent_writes": 0,
            "canonical_scientific_write": False,
        }

    def prepare_source_review(self, _session, task_id: str, expected_state: dict[str, str]):
        assert task_id == TASK_ID
        assert expected_state == {"state_id": TASK_STATE_ID, "state_digest": TASK_STATE_DIGEST}
        return SimpleNamespace(
            handle=SimpleNamespace(basis_profile_digest=PROFILE_DIGEST),
            descriptor={"basis_profile_id": PROFILE_ID},
        )

    @contextmanager
    def open_source_review(self, _session, _handle):
        yield SimpleNamespace(path=self.private_path)

    def decide(
        self,
        _session,
        task_id: str,
        expected_state: dict[str, str],
        basis_profile_id: str,
        action: str,
        attestation: str | None = None,
    ):
        assert task_id == TASK_ID
        assert expected_state == {"state_id": TASK_STATE_ID, "state_digest": TASK_STATE_DIGEST}
        assert basis_profile_id == PROFILE_ID
        if action == "accept_uncertainty":
            assert attestation == "reading_order_reviewed"
        else:
            assert attestation is None
        self.decide_calls += 1
        if self.decision_action is None:
            self.decision_action = action
            writes = 1
        else:
            assert self.decision_action == action
            writes = 0
        return {
            "status": "success",
            "decision_action": action,
            "persistent_writes": writes,
            "canonical_scientific_write": False,
        }


class FakeReaderLauncher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.paths: list[Path] = []

    def launch(self, path: Path):
        self.paths.append(path)
        if self.fail:
            raise RuntimeError(f"reader failed for private path {path}")
        return SimpleNamespace(reader="updf")


class InterruptingAgentService:
    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh_review_task(self, _session, task_id: str, expected_state: dict[str, str]):
        assert task_id == TASK_ID
        assert expected_state == {"state_id": TASK_STATE_ID, "state_digest": TASK_STATE_DIGEST}
        self.refresh_calls += 1
        if self.refresh_calls == 1:
            raise ResearchKBError(
                Diagnostic(
                    code="RKBC-036",
                    record_kind="agent_task",
                    record_id=TASK_ID,
                    json_path="/",
                    message="private refresh interruption detail",
                )
            )
        return {
            "status": "success",
            "task": {"task_id": TASK_ID, "status": "superseded"},
            "successor_task": {"task_id": "agenttask_successor", "status": "created"},
            "persistent_writes": 1,
            "canonical_scientific_write": False,
        }


def _post(harness: AppHarness, csrf: str | None, suffix: str, payload: dict, *, origin: str = EXPECTED_ORIGIN):
    headers = {"Origin": origin}
    if csrf is not None:
        headers["X-RKB-CSRF"] = csrf
    return harness.client.post(
        f"/api/agent/tasks/{TASK_ID}/source-adequacy-resolution/{suffix}",
        headers=headers,
        json=payload,
    )


def test_source_adequacy_resolution_routes_are_closed_and_do_not_leak_private_source(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    app_harness = resolution_harness
    private_path = tmp_path / "private" / "secret-source.pdf"
    missing_session = app_harness.client.get(
        f"/api/agent/tasks/{TASK_ID}/source-adequacy-resolution"
    )
    assert missing_session.status_code == 401

    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    runtime.source_adequacy_resolution = FakeResolutionService(private_path)
    runtime.pdf_launcher = FakeReaderLauncher(fail=True)

    context = app_harness.client.get(
        f"/api/agent/tasks/{TASK_ID}/source-adequacy-resolution"
    )
    assert context.status_code == 200, context.text
    assert context.json()["resolution_state"] == "review_required"

    missing_csrf = _post(app_harness, None, "open", EXPECTED_STATE)
    bad_origin = _post(app_harness, csrf, "open", EXPECTED_STATE, origin="http://attacker.invalid")
    unknown_field = _post(app_harness, csrf, "open", {**EXPECTED_STATE, "source_path": str(private_path)})
    unknown_action = _post(
        app_harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "override_hard_failure"},
    )
    assert missing_csrf.status_code == 401
    assert bad_origin.status_code in {401, 403}
    assert unknown_field.status_code == 422
    assert unknown_action.status_code == 422

    failed_open = _post(app_harness, csrf, "open", EXPECTED_STATE)
    assert failed_open.status_code == 500
    assert failed_open.json()["diagnostic"]["code"] == "RKBAPP-INTERNAL"

    rendered = "\n".join(
        [context.text, missing_csrf.text, bad_origin.text, unknown_field.text, unknown_action.text, failed_open.text]
    )
    for forbidden in (str(private_path), private_path.name, "private refresh interruption detail"):
        assert forbidden not in rendered


def test_resolution_decision_recovers_after_committed_decision_and_refresh_interruption(
    resolution_harness: AppHarness,
    tmp_path: Path,
) -> None:
    app_harness = resolution_harness
    csrf = app_harness.open_workspace()
    runtime = app_harness.client.app.state.runtime
    resolution = FakeResolutionService(tmp_path / "private" / "secret-source.pdf")
    launcher = FakeReaderLauncher()
    agent = InterruptingAgentService()
    runtime.source_adequacy_resolution = resolution
    runtime.pdf_launcher = launcher
    runtime.agent = agent

    opened = _post(app_harness, csrf, "open", EXPECTED_STATE)
    assert opened.status_code == 200, opened.text
    confirmation_id = opened.json()["confirmation"]["confirmation_id"]
    assert launcher.paths == [resolution.private_path]
    assert str(resolution.private_path) not in opened.text

    first = _post(
        app_harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "accept_uncertainty", "confirmation_id": confirmation_id},
    )
    assert first.status_code == 409, first.text
    assert first.json()["diagnostic"]["code"] == "RKBC-036"
    assert resolution.decision_action == "accept_uncertainty"
    assert runtime.source_review_confirmations.count == 0

    recovered = _post(
        app_harness,
        csrf,
        "decide",
        {**EXPECTED_STATE, "action": "accept_uncertainty"},
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["successor_task"]["task_id"] == "agenttask_successor"
    assert resolution.decide_calls == 2
    assert agent.refresh_calls == 2
    assert recovered.json()["canonical_scientific_write"] is False

    rendered = first.text + recovered.text
    for forbidden in (str(resolution.private_path), resolution.private_path.name, "private refresh interruption detail"):
        assert forbidden not in rendered
