from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from research_kb_app.errors import AppOperationError


SELECTION_LEASE_SECONDS = 600
MAX_SELECTION_LEASES = 64
MULTI_SELECTION_PURPOSES = frozenset({"source_root"})
SELECTION_PURPOSES = frozenset(
    {
        "workspace_parent",
        "existing_workspace_config",
        "source_root",
        "local_inbox",
        "obsidian_vault",
        "task_package_destination",
        "backup_destination",
    }
)


@dataclass(frozen=True, slots=True)
class SelectionLease:
    lease_id: str
    browser_session_id: str
    profile_id: str
    purpose: str
    path: Path
    display_label: str
    capability_facts: dict[str, Any]
    expires_at: float
    security_basis_digest: str | None = None
    state: str = "active"

    def public(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.monotonic() if now is None else now
        return {
            "lease_id": self.lease_id,
            "purpose": self.purpose,
            "display_label": self.display_label,
            "capability_facts": dict(self.capability_facts),
            "expires_in_seconds": max(0, int(self.expires_at - current)),
        }


class SelectionLeaseRegistry:
    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        ttl_seconds: int = SELECTION_LEASE_SECONDS,
        max_leases: int = MAX_SELECTION_LEASES,
    ) -> None:
        if ttl_seconds <= 0 or max_leases <= 0:
            raise ValueError("selection lease limits must be positive")
        self._clock = monotonic_clock
        self._ttl_seconds = ttl_seconds
        self._max_leases = max_leases
        self._lock = threading.RLock()
        self._leases: dict[str, SelectionLease] = {}

    def issue(
        self,
        *,
        browser_session_id: str,
        profile_id: str,
        purpose: str,
        path: Path,
        display_label: str,
        capability_facts: dict[str, Any],
        security_basis_digest: str | None = None,
    ) -> SelectionLease:
        if purpose not in SELECTION_PURPOSES:
            raise AppOperationError("RKBAPP-SELECTION-PURPOSE", "Folder selection purpose is not supported", status_code=400)
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_dir():
            raise AppOperationError("RKBAPP-SELECTION-TARGET", "Selected folder is not available", status_code=400)
        if not browser_session_id or not profile_id or not display_label:
            raise AppOperationError("RKBAPP-SELECTION-BINDING", "Folder selection binding is incomplete", status_code=400)
        now = self._clock()
        with self._lock:
            self._discard_expired(now)
            for lease_id, lease in tuple(self._leases.items()):
                if (
                    lease.browser_session_id == browser_session_id
                    and lease.profile_id == profile_id
                    and lease.purpose == purpose
                    and purpose not in MULTI_SELECTION_PURPOSES
                    and lease.state in {"active", "claimed"}
                ):
                    self._leases[lease_id] = replace(lease, state="invalidated")
            live = sum(lease.state in {"active", "claimed"} for lease in self._leases.values())
            if live >= self._max_leases:
                raise AppOperationError("RKBAPP-SELECTION-LIMIT", "Too many folder selections are active")
            lease = SelectionLease(
                lease_id=f"selection_{secrets.token_hex(24)}",
                browser_session_id=browser_session_id,
                profile_id=profile_id,
                purpose=purpose,
                path=resolved,
                display_label=display_label,
                capability_facts=dict(capability_facts),
                expires_at=now + self._ttl_seconds,
                security_basis_digest=security_basis_digest,
            )
            self._leases[lease.lease_id] = lease
            return lease

    def public(self, lease_id: str, *, browser_session_id: str, profile_id: str) -> dict[str, Any]:
        with self._lock:
            lease = self._require(lease_id, browser_session_id, profile_id, purpose=None, state="active")
            return lease.public(now=self._clock())

    def claim(
        self,
        lease_id: str,
        *,
        browser_session_id: str,
        profile_id: str,
        purpose: str,
    ) -> SelectionLease:
        with self._lock:
            lease = self._require(lease_id, browser_session_id, profile_id, purpose=purpose, state="active")
            claimed = replace(lease, state="claimed")
            self._leases[lease_id] = claimed
            return claimed

    def finish(self, lease_id: str, *, succeeded: bool, invalidate: bool = False) -> None:
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None or lease.state != "claimed":
                raise AppOperationError("RKBAPP-SELECTION-STATE", "Folder selection is not claimed")
            state = "consumed" if succeeded else ("invalidated" if invalidate else "active")
            self._leases[lease_id] = replace(lease, state=state)

    def invalidate_session(self, browser_session_id: str) -> None:
        with self._lock:
            for lease_id, lease in tuple(self._leases.items()):
                if lease.browser_session_id == browser_session_id and lease.state in {"active", "claimed"}:
                    self._leases[lease_id] = replace(lease, state="invalidated")

    def clear(self) -> None:
        with self._lock:
            for lease_id, lease in tuple(self._leases.items()):
                if lease.state in {"active", "claimed"}:
                    self._leases[lease_id] = replace(lease, state="invalidated")

    def _require(
        self,
        lease_id: str,
        browser_session_id: str,
        profile_id: str,
        *,
        purpose: str | None,
        state: str,
    ) -> SelectionLease:
        now = self._clock()
        self._discard_expired(now)
        lease = self._leases.get(lease_id)
        valid = (
            lease is not None
            and lease.state == state
            and lease.expires_at > now
            and secrets.compare_digest(lease.browser_session_id, browser_session_id)
            and secrets.compare_digest(lease.profile_id, profile_id)
            and (purpose is None or lease.purpose == purpose)
        )
        if not valid:
            raise AppOperationError("RKBAPP-SELECTION-STALE", "Folder selection is expired, stale, or mismatched")
        return lease

    def _discard_expired(self, now: float) -> None:
        for lease_id, lease in tuple(self._leases.items()):
            if lease.state in {"active", "claimed"} and lease.expires_at <= now:
                self._leases[lease_id] = replace(lease, state="expired")


__all__ = [
    "MAX_SELECTION_LEASES",
    "MULTI_SELECTION_PURPOSES",
    "SELECTION_LEASE_SECONDS",
    "SELECTION_PURPOSES",
    "SelectionLease",
    "SelectionLeaseRegistry",
]
