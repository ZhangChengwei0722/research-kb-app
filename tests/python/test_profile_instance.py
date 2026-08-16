from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
import os
from pathlib import Path

import pytest

from research_kb_app.errors import AppOperationError
from research_kb_app.lifecycle_receipts import AppendOnlyReceiptStore
from research_kb_app.profile_instance import (
    ProcessIdentity,
    ProfileInstanceGuard,
    current_process_identity,
    process_identity_is_live,
)


NOW = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
IDENTITY = ProcessIdentity(1234, 987654321, "a" * 64)


def _guard(root: Path, *, live: bool = False) -> ProfileInstanceGuard:
    receipts = AppendOnlyReceiptStore(root / "receipts", "profile-instance", clock=lambda: NOW)
    return ProfileInstanceGuard(
        root / "runtime",
        "managed-profile:default",
        mutex_factory=lambda _key: nullcontext(),
        receipt_store=receipts,
        identity_provider=lambda: IDENTITY,
        identity_matcher=lambda _identity: live,
        clock=lambda: NOW,
    )


def test_profile_instance_receipt_is_private_append_only_and_removed_on_clean_exit(tmp_path: Path) -> None:
    guard = _guard(tmp_path)

    with guard:
        assert guard.current_path.is_file()

    assert not guard.current_path.exists()
    records = guard.receipt_store.read_all()
    assert [item["payload"]["event"] for item in records] == [
        "profile_instance_acquired",
        "profile_instance_released",
    ]
    assert [item["sequence"] for item in records] == [1, 2]
    assert "managed-profile:default" not in str(records)


def test_profile_instance_reclaims_only_a_proven_stale_receipt(tmp_path: Path) -> None:
    crashed = _guard(tmp_path)
    crashed.__enter__()
    crashed._release_mutex(None, None, None)
    assert crashed.current_path.is_file()

    replacement = _guard(tmp_path, live=False)
    with replacement:
        events = [item["payload"]["event"] for item in replacement.receipt_store.read_all()]
        assert "stale_instance_recovered" in events


def test_profile_instance_fails_closed_when_recorded_process_identity_is_live(tmp_path: Path) -> None:
    crashed = _guard(tmp_path)
    crashed.__enter__()
    crashed._release_mutex(None, None, None)

    with pytest.raises(AppOperationError) as caught:
        _guard(tmp_path, live=True).__enter__()

    assert caught.value.code == "RKBAPP-INSTANCE-ACTIVE"
    assert crashed.current_path.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity live cell")
def test_live_current_process_identity_round_trip() -> None:
    identity = current_process_identity()

    assert identity.pid == os.getpid()
    assert process_identity_is_live(identity) is True
