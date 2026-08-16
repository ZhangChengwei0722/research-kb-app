from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from research_kb.services import TrustedParseIntakePreparation

from research_kb_app.errors import AppOperationError


TRUSTED_PARSE_LEASE_SECONDS = 10 * 60
MAX_TRUSTED_PARSE_LEASES = 64
TrustedParseLeaseState = Literal[
    "prepared",
    "running",
    "completed",
    "failed",
    "invalidated",
    "expired",
]


@dataclass(frozen=True, slots=True)
class TrustedParseLease:
    token: str
    browser_session_id: str
    workspace_option_id: str
    job_id: str
    job_state_id: str
    job_state_digest: str
    aggregate_preview_digest: str
    preparation: TrustedParseIntakePreparation
    expires_at: float
    state: TrustedParseLeaseState = "prepared"
    current_result: dict[str, Any] | None = None
    diagnostic_code: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedParseLeaseStart:
    outcome: Literal["start", "running", "completed"]
    lease: TrustedParseLease


class TrustedParseLeaseRegistry:
    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        ttl_seconds: int = TRUSTED_PARSE_LEASE_SECONDS,
        max_leases: int = MAX_TRUSTED_PARSE_LEASES,
    ) -> None:
        if ttl_seconds <= 0 or max_leases <= 0:
            raise ValueError("trusted Parse lease limits must be positive")
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._token_factory = token_factory
        self._ttl_seconds = ttl_seconds
        self._max_leases = max_leases
        self._lock = threading.RLock()
        self._leases: dict[str, TrustedParseLease] = {}

    @property
    def count(self) -> int:
        with self._lock:
            self._expire()
            return sum(item.state in {"prepared", "running"} for item in self._leases.values())

    def issue(
        self,
        *,
        browser_session_id: str,
        workspace_option_id: str,
        preparation: TrustedParseIntakePreparation,
    ) -> TrustedParseLease:
        if not browser_session_id or not workspace_option_id:
            raise AppOperationError(
                "RKBAPP-TRUSTED-PARSE-BINDING",
                "Trusted Parse preparation binding is incomplete",
            )
        core_expiry = _expiry_timestamp(preparation.expires_at)
        remaining = core_expiry - self._wall_clock()
        if remaining <= 0:
            raise AppOperationError(
                "RKBAPP-TRUSTED-PARSE-EXPIRED",
                "Trusted Parse preparation has expired",
                status_code=410,
            )
        with self._lock:
            self._expire()
            if len(self._leases) >= self._max_leases and not any(
                item.state not in {"prepared", "running"} for item in self._leases.values()
            ):
                raise AppOperationError(
                    "RKBAPP-TRUSTED-PARSE-LIMIT",
                    "Too many trusted Parse preparations are active",
                    status_code=429,
                )
            token = f"trusted_parse_{self._token_factory()}"
            if token in self._leases or len(token) > 128:
                raise AppOperationError(
                    "RKBAPP-TRUSTED-PARSE-COLLISION",
                    "A unique trusted Parse preparation could not be issued",
                    status_code=500,
                )
            while len(self._leases) >= self._max_leases:
                self._evict_oldest_terminal()
            for existing_token, item in tuple(self._leases.items()):
                if (
                    item.browser_session_id == browser_session_id
                    and item.workspace_option_id == workspace_option_id
                    and item.job_id == preparation.job_id
                    and item.state == "prepared"
                ):
                    self._leases[existing_token] = replace(item, state="invalidated")
            lease = TrustedParseLease(
                token=token,
                browser_session_id=browser_session_id,
                workspace_option_id=workspace_option_id,
                job_id=preparation.job_id,
                job_state_id=preparation.job_state_id,
                job_state_digest=preparation.job_state_digest,
                aggregate_preview_digest=preparation.preparation_digest,
                preparation=preparation,
                expires_at=self._monotonic_clock() + min(float(self._ttl_seconds), remaining),
            )
            self._leases[token] = lease
            return lease

    def begin(
        self,
        token: str,
        *,
        browser_session_id: str,
        workspace_option_id: str,
        job_id: str,
        aggregate_preview_digest: str,
    ) -> TrustedParseLeaseStart:
        with self._lock:
            lease = self._require(
                token,
                browser_session_id=browser_session_id,
                workspace_option_id=workspace_option_id,
                job_id=job_id,
                aggregate_preview_digest=aggregate_preview_digest,
            )
            if lease.state == "prepared":
                running = replace(lease, state="running")
                self._leases[token] = running
                return TrustedParseLeaseStart("start", running)
            if lease.state == "running":
                return TrustedParseLeaseStart("running", lease)
            if lease.state == "completed":
                return TrustedParseLeaseStart("completed", lease)
            raise _stale()

    def set_current_result(self, token: str, result: dict[str, Any]) -> None:
        with self._lock:
            lease = self._leases.get(token)
            if lease is None or lease.state != "running":
                raise _stale()
            self._leases[token] = replace(lease, current_result=dict(result))

    def complete(self, token: str, result: dict[str, Any]) -> None:
        with self._lock:
            lease = self._leases.get(token)
            if lease is None or lease.state != "running":
                raise _stale()
            self._leases[token] = replace(
                lease,
                state="completed",
                current_result=dict(result),
                diagnostic_code=None,
            )

    def fail(self, token: str, diagnostic_code: str) -> None:
        with self._lock:
            lease = self._leases.get(token)
            if lease is None or lease.state != "running":
                return
            self._leases[token] = replace(
                lease,
                state="failed",
                current_result=None,
                diagnostic_code=diagnostic_code,
            )

    def restore_prepared(self, token: str) -> None:
        with self._lock:
            lease = self._leases.get(token)
            if lease is None or lease.state != "running" or lease.current_result is not None:
                return
            self._leases[token] = replace(lease, state="prepared")

    def clear(self) -> None:
        with self._lock:
            for token, lease in tuple(self._leases.items()):
                if lease.state in {"prepared", "running"}:
                    self._leases[token] = replace(lease, state="invalidated", current_result=None)

    def _require(
        self,
        token: str,
        *,
        browser_session_id: str,
        workspace_option_id: str,
        job_id: str,
        aggregate_preview_digest: str,
    ) -> TrustedParseLease:
        self._expire()
        lease = self._leases.get(token)
        valid = (
            lease is not None
            and lease.state in {"prepared", "running", "completed"}
            and secrets.compare_digest(lease.browser_session_id, browser_session_id)
            and secrets.compare_digest(lease.workspace_option_id, workspace_option_id)
            and secrets.compare_digest(lease.job_id, job_id)
            and secrets.compare_digest(lease.aggregate_preview_digest, aggregate_preview_digest)
        )
        if not valid:
            raise _stale()
        return lease

    def _expire(self) -> None:
        now = self._monotonic_clock()
        for token, lease in tuple(self._leases.items()):
            if lease.state == "prepared" and lease.expires_at <= now:
                self._leases[token] = replace(lease, state="expired")

    def _evict_oldest_terminal(self) -> None:
        for token, lease in tuple(self._leases.items()):
            if lease.state not in {"prepared", "running"}:
                del self._leases[token]
                return
        raise AppOperationError(
            "RKBAPP-TRUSTED-PARSE-LIMIT",
            "Too many trusted Parse preparations are active",
            status_code=429,
        )


def _expiry_timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise AppOperationError(
            "RKBAPP-TRUSTED-PARSE-EXPIRY",
            "Trusted Parse preparation expiry is invalid",
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _stale() -> AppOperationError:
    return AppOperationError(
        "RKBAPP-TRUSTED-PARSE-STALE",
        "Trusted Parse preparation is expired, stale, or mismatched",
        status_code=409,
    )


__all__ = [
    "MAX_TRUSTED_PARSE_LEASES",
    "TRUSTED_PARSE_LEASE_SECONDS",
    "TrustedParseLease",
    "TrustedParseLeaseRegistry",
    "TrustedParseLeaseStart",
]
