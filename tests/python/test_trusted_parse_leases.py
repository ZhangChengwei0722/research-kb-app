from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from research_kb.services import TrustedParseIntakePreparation
from research_kb.trusted_parse_authority import TrustedParseAuthorityPreview

from research_kb_app.errors import AppOperationError
from research_kb_app.trusted_parse_leases import TrustedParseLeaseRegistry


def _preparation(*, expires_at: str, job_id: str = "job_1234") -> TrustedParseIntakePreparation:
    preview = TrustedParseAuthorityPreview(
        "authority_1234",
        "authoritystate_1234",
        "b" * 64,
        {"expires_at": expires_at},
    )
    return TrustedParseIntakePreparation(
        "workspace-option",
        "workspace_1234",
        job_id,
        "jobstate_1234",
        "a" * 64,
        "primary",
        "paper_1234",
        {"root_id": "source", "relative_path": "source.pdf"},
        "c" * 64,
        "source.pdf",
        1024,
        {"adapter": "pdfplumber-text-flow", "version": "0.11.10"},
        "local-pdf-default-v1",
        "trusted-parse-v1",
        "parse_run",
        expires_at,
        preview,
        False,
        None,
        "absent",
        None,
        "d" * 64,
    )


def test_lease_is_session_workspace_job_and_digest_bound() -> None:
    now = datetime.now(timezone.utc)
    registry = TrustedParseLeaseRegistry(token_factory=lambda: "a" * 43)
    preparation = _preparation(expires_at=(now + timedelta(minutes=5)).isoformat())
    lease = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=preparation,
    )

    started = registry.begin(
        lease.token,
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        job_id="job_1234",
        aggregate_preview_digest="d" * 64,
    )
    assert started.outcome == "start"

    duplicate = registry.begin(
        lease.token,
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        job_id="job_1234",
        aggregate_preview_digest="d" * 64,
    )
    assert duplicate.outcome == "running"

    for field, value in (
        ("browser_session_id", "browser-b"),
        ("workspace_option_id", "workspace-b"),
        ("job_id", "job_9999"),
        ("aggregate_preview_digest", "e" * 64),
    ):
        values = {
            "browser_session_id": "browser-a",
            "workspace_option_id": "workspace-option",
            "job_id": "job_1234",
            "aggregate_preview_digest": "d" * 64,
        }
        values[field] = value
        with pytest.raises(AppOperationError) as stale:
            registry.begin(lease.token, **values)
        assert stale.value.code == "RKBAPP-TRUSTED-PARSE-STALE"


def test_completed_retry_returns_bounded_current_result_and_clear_invalidates() -> None:
    now = datetime.now(timezone.utc)
    tokens = iter(("b" * 43, "c" * 43))
    registry = TrustedParseLeaseRegistry(token_factory=lambda: next(tokens))
    lease = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(expires_at=(now + timedelta(minutes=5)).isoformat()),
    )
    values = {
        "browser_session_id": "browser-a",
        "workspace_option_id": "workspace-option",
        "job_id": "job_1234",
        "aggregate_preview_digest": "d" * 64,
    }
    registry.begin(lease.token, **values)
    registry.complete(lease.token, {"status": "accepted", "trusted_parse_outcome": "continued"})
    completed = registry.begin(lease.token, **values)

    assert completed.outcome == "completed"
    assert completed.lease.current_result == {
        "status": "accepted",
        "trusted_parse_outcome": "continued",
    }

    replacement = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(expires_at=(now + timedelta(minutes=5)).isoformat()),
    )
    registry.clear()
    with pytest.raises(AppOperationError):
        registry.begin(replacement.token, **values)


def test_oldest_terminal_lease_is_evicted_before_capacity_rejection() -> None:
    now = datetime.now(timezone.utc)
    tokens = iter(("d" * 43, "e" * 43, "f" * 43))
    registry = TrustedParseLeaseRegistry(
        token_factory=lambda: next(tokens),
        max_leases=2,
    )

    first = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            job_id="job-1",
        ),
    )
    first_values = {
        "browser_session_id": "browser-a",
        "workspace_option_id": "workspace-option",
        "job_id": "job-1",
        "aggregate_preview_digest": "d" * 64,
    }
    registry.begin(first.token, **first_values)
    registry.complete(first.token, {"status": "first"})
    assert registry.begin(first.token, **first_values).outcome == "completed"

    second = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            job_id="job-2",
        ),
    )
    second_values = {
        "browser_session_id": "browser-a",
        "workspace_option_id": "workspace-option",
        "job_id": "job-2",
        "aggregate_preview_digest": "d" * 64,
    }
    registry.begin(second.token, **second_values)
    registry.complete(second.token, {"status": "second"})

    third = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            job_id="job-3",
        ),
    )

    assert len(registry._leases) == 2
    assert first.token not in registry._leases
    assert second.token in registry._leases
    assert third.token in registry._leases
    assert registry.begin(second.token, **second_values).outcome == "completed"
    with pytest.raises(AppOperationError) as evicted:
        registry.begin(first.token, **first_values)
    assert evicted.value.code == "RKBAPP-TRUSTED-PARSE-STALE"


def test_active_leases_are_protected_when_registry_is_at_capacity() -> None:
    now = datetime.now(timezone.utc)
    tokens = iter(("g" * 43, "h" * 43, "i" * 43))
    registry = TrustedParseLeaseRegistry(
        token_factory=lambda: next(tokens),
        max_leases=2,
    )
    running = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            job_id="job-running",
        ),
    )
    registry.begin(
        running.token,
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        job_id="job-running",
        aggregate_preview_digest="d" * 64,
    )
    prepared = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            job_id="job-prepared",
        ),
    )

    with pytest.raises(AppOperationError) as limited:
        registry.issue(
            browser_session_id="browser-a",
            workspace_option_id="workspace-option",
            preparation=_preparation(
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                job_id="job-rejected",
            ),
        )

    assert limited.value.code == "RKBAPP-TRUSTED-PARSE-LIMIT"
    assert set(registry._leases) == {running.token, prepared.token}
    assert registry._leases[running.token].state == "running"
    assert registry._leases[prepared.token].state == "prepared"


def test_expired_core_or_app_lease_fails_closed() -> None:
    wall_now = datetime.now(timezone.utc).timestamp()
    with pytest.raises(AppOperationError) as expired_core:
        TrustedParseLeaseRegistry(wall_clock=lambda: wall_now).issue(
            browser_session_id="browser-a",
            workspace_option_id="workspace-option",
            preparation=_preparation(
                expires_at=datetime.fromtimestamp(wall_now - 1, timezone.utc).isoformat()
            ),
        )
    assert expired_core.value.code == "RKBAPP-TRUSTED-PARSE-EXPIRED"

    monotonic = [10.0]
    registry = TrustedParseLeaseRegistry(
        monotonic_clock=lambda: monotonic[0],
        wall_clock=lambda: wall_now,
        token_factory=lambda: "c" * 43,
        ttl_seconds=2,
    )
    lease = registry.issue(
        browser_session_id="browser-a",
        workspace_option_id="workspace-option",
        preparation=_preparation(
            expires_at=datetime.fromtimestamp(wall_now + 60, timezone.utc).isoformat()
        ),
    )
    monotonic[0] = 13.0
    with pytest.raises(AppOperationError):
        registry.begin(
            lease.token,
            browser_session_id="browser-a",
            workspace_option_id="workspace-option",
            job_id="job_1234",
            aggregate_preview_digest="d" * 64,
        )
