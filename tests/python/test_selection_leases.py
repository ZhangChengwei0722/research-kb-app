from __future__ import annotations

from pathlib import Path

import pytest

from research_kb_app.errors import AppOperationError
from research_kb_app.selection_leases import SelectionLeaseRegistry


def test_selection_lease_is_opaque_bound_and_single_use(tmp_path: Path) -> None:
    now = [100.0]
    selected = tmp_path / "selected"
    selected.mkdir()
    registry = SelectionLeaseRegistry(monotonic_clock=lambda: now[0], ttl_seconds=10)

    lease = registry.issue(
        browser_session_id="browser-a",
        profile_id="profile-a",
        purpose="local_inbox",
        path=selected,
        display_label="selected",
        capability_facts={"filesystem": "NTFS"},
    )

    assert str(selected) not in str(lease.public())
    with pytest.raises(AppOperationError):
        registry.claim(
            lease.lease_id,
            browser_session_id="browser-b",
            profile_id="profile-a",
            purpose="local_inbox",
        )
    claimed = registry.claim(
        lease.lease_id,
        browser_session_id="browser-a",
        profile_id="profile-a",
        purpose="local_inbox",
    )
    assert claimed.path == selected.resolve()
    registry.finish(lease.lease_id, succeeded=True)
    with pytest.raises(AppOperationError):
        registry.claim(
            lease.lease_id,
            browser_session_id="browser-a",
            profile_id="profile-a",
            purpose="local_inbox",
        )


def test_replacement_expiry_and_restart_invalidate_selection(tmp_path: Path) -> None:
    now = [100.0]
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry = SelectionLeaseRegistry(monotonic_clock=lambda: now[0], ttl_seconds=10)
    one = registry.issue(
        browser_session_id="browser-a",
        profile_id="profile-a",
        purpose="workspace_parent",
        path=first,
        display_label="first",
        capability_facts={},
    )
    two = registry.issue(
        browser_session_id="browser-a",
        profile_id="profile-a",
        purpose="workspace_parent",
        path=second,
        display_label="second",
        capability_facts={},
    )

    with pytest.raises(AppOperationError):
        registry.claim(
            one.lease_id,
            browser_session_id="browser-a",
            profile_id="profile-a",
            purpose="workspace_parent",
        )
    now[0] = 111.0
    with pytest.raises(AppOperationError):
        registry.claim(
            two.lease_id,
            browser_session_id="browser-a",
            profile_id="profile-a",
            purpose="workspace_parent",
        )

    three = registry.issue(
        browser_session_id="browser-c",
        profile_id="profile-a",
        purpose="workspace_parent",
        path=first,
        display_label="first",
        capability_facts={},
    )
    registry.clear()
    with pytest.raises(AppOperationError):
        registry.claim(
            three.lease_id,
            browser_session_id="browser-c",
            profile_id="profile-a",
            purpose="workspace_parent",
        )


def test_source_root_selections_can_form_one_bounded_multi_root_request(tmp_path: Path) -> None:
    registry = SelectionLeaseRegistry(monotonic_clock=lambda: 10.0)
    first_path = tmp_path / "source-a"
    second_path = tmp_path / "source-b"
    first_path.mkdir()
    second_path.mkdir()
    first = registry.issue(
        browser_session_id="browser-a",
        profile_id="default",
        purpose="source_root",
        path=first_path,
        display_label="Source A",
        capability_facts={"local": True},
    )
    second = registry.issue(
        browser_session_id="browser-a",
        profile_id="default",
        purpose="source_root",
        path=second_path,
        display_label="Source B",
        capability_facts={"local": True},
    )

    assert registry.claim(
        first.lease_id,
        browser_session_id="browser-a",
        profile_id="default",
        purpose="source_root",
    ).path == first_path
    assert registry.claim(
        second.lease_id,
        browser_session_id="browser-a",
        profile_id="default",
        purpose="source_root",
    ).path == second_path
