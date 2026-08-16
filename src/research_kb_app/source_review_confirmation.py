from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from research_kb_app.errors import AppOperationError


SOURCE_REVIEW_CONFIRMATION_TTL_SECONDS = 10 * 60
MAX_SOURCE_REVIEW_CONFIRMATIONS_PER_SESSION = 8
SOURCE_REVIEW_SUBJECT_KINDS = frozenset({"agent_task", "intake_job"})


@dataclass(frozen=True, slots=True)
class SourceReviewConfirmation:
    confirmation_id: str
    browser_session_id: str
    workspace_option_id: str
    subject_kind: str
    subject_id: str
    subject_state_id: str
    subject_state_digest: str
    basis_profile_id: str
    basis_profile_digest: str
    expires_at: float

    @property
    def task_id(self) -> str:
        return self.subject_id

    @property
    def task_state_id(self) -> str:
        return self.subject_state_id

    @property
    def task_state_digest(self) -> str:
        return self.subject_state_digest


class SourceReviewConfirmationRegistry:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        self._clock = clock
        self._token_factory = token_factory
        self._entries: dict[str, SourceReviewConfirmation] = {}

    @property
    def count(self) -> int:
        self._purge_expired()
        return len(self._entries)

    def issue(
        self,
        *,
        browser_session_id: str,
        workspace_option_id: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        subject_state_id: str | None = None,
        subject_state_digest: str | None = None,
        task_id: str | None = None,
        task_state_id: str | None = None,
        task_state_digest: str | None = None,
        basis_profile_id: str,
        basis_profile_digest: str,
    ) -> dict[str, object]:
        subject_kind, subject_id, subject_state_id, subject_state_digest = _normalize_subject(
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_state_id=subject_state_id,
            subject_state_digest=subject_state_digest,
            task_id=task_id,
            task_state_id=task_state_id,
            task_state_digest=task_state_digest,
        )
        self._purge_expired()
        count = sum(
            item.browser_session_id == browser_session_id
            for item in self._entries.values()
        )
        if count >= MAX_SOURCE_REVIEW_CONFIRMATIONS_PER_SESSION:
            raise AppOperationError(
                "RKBAPP-SOURCE-REVIEW-CAP",
                "Too many source review confirmations are active",
                status_code=429,
            )
        confirmation_id = self._token_factory()
        if not confirmation_id or confirmation_id in self._entries:
            raise AppOperationError(
                "RKBAPP-SOURCE-REVIEW-COLLISION",
                "A unique source review confirmation could not be issued",
                status_code=500,
            )
        self._entries[confirmation_id] = SourceReviewConfirmation(
            confirmation_id=confirmation_id,
            browser_session_id=browser_session_id,
            workspace_option_id=workspace_option_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_state_id=subject_state_id,
            subject_state_digest=subject_state_digest,
            basis_profile_id=basis_profile_id,
            basis_profile_digest=basis_profile_digest,
            expires_at=self._clock() + SOURCE_REVIEW_CONFIRMATION_TTL_SECONDS,
        )
        return {
            "confirmation_id": confirmation_id,
            "expires_in_seconds": SOURCE_REVIEW_CONFIRMATION_TTL_SECONDS,
        }

    def require(
        self,
        confirmation_id: str,
        *,
        browser_session_id: str,
        workspace_option_id: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        subject_state_id: str | None = None,
        subject_state_digest: str | None = None,
        task_id: str | None = None,
        task_state_id: str | None = None,
        task_state_digest: str | None = None,
        basis_profile_id: str,
        basis_profile_digest: str,
    ) -> SourceReviewConfirmation:
        subject_kind, subject_id, subject_state_id, subject_state_digest = _normalize_subject(
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_state_id=subject_state_id,
            subject_state_digest=subject_state_digest,
            task_id=task_id,
            task_state_id=task_state_id,
            task_state_digest=task_state_digest,
        )
        entry = self._entries.get(confirmation_id)
        if entry is None:
            raise _not_found()
        if self._clock() >= entry.expires_at:
            self._entries.pop(confirmation_id, None)
            raise AppOperationError(
                "RKBAPP-SOURCE-REVIEW-EXPIRED",
                "The source review confirmation expired; open the document again",
                status_code=410,
            )
        if entry != SourceReviewConfirmation(
            confirmation_id=confirmation_id,
            browser_session_id=browser_session_id,
            workspace_option_id=workspace_option_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_state_id=subject_state_id,
            subject_state_digest=subject_state_digest,
            basis_profile_id=basis_profile_id,
            basis_profile_digest=basis_profile_digest,
            expires_at=entry.expires_at,
        ):
            raise _not_found()
        return entry

    def consume(self, confirmation_id: str) -> None:
        self._entries.pop(confirmation_id, None)

    def invalidate_subject(self, subject_kind: str, subject_id: str) -> None:
        if subject_kind not in SOURCE_REVIEW_SUBJECT_KINDS:
            raise AppOperationError(
                "RKBAPP-SOURCE-REVIEW-SUBJECT",
                "The source review subject kind is not supported",
                status_code=400,
            )
        for confirmation_id in [
            key
            for key, value in self._entries.items()
            if value.subject_kind == subject_kind and value.subject_id == subject_id
        ]:
            self._entries.pop(confirmation_id, None)

    def invalidate_task(self, task_id: str) -> None:
        self.invalidate_subject("agent_task", task_id)

    def clear(self) -> None:
        self._entries.clear()

    def _purge_expired(self) -> None:
        now = self._clock()
        for key in [key for key, value in self._entries.items() if now >= value.expires_at]:
            self._entries.pop(key, None)


def _not_found() -> AppOperationError:
    return AppOperationError(
        "RKBAPP-SOURCE-REVIEW-NOT-FOUND",
        "The source review confirmation is unavailable",
        status_code=404,
    )


def _normalize_subject(
    *,
    subject_kind: str | None,
    subject_id: str | None,
    subject_state_id: str | None,
    subject_state_digest: str | None,
    task_id: str | None,
    task_state_id: str | None,
    task_state_digest: str | None,
) -> tuple[str, str, str, str]:
    legacy_values = (task_id, task_state_id, task_state_digest)
    if subject_kind is None:
        if any(value is None for value in legacy_values):
            raise AppOperationError(
                "RKBAPP-SOURCE-REVIEW-SUBJECT",
                "The source review subject binding is incomplete",
                status_code=400,
            )
        subject_kind = "agent_task"
        subject_id = task_id
        subject_state_id = task_state_id
        subject_state_digest = task_state_digest
    elif any(value is not None for value in legacy_values):
        raise AppOperationError(
            "RKBAPP-SOURCE-REVIEW-SUBJECT",
            "The source review subject binding is ambiguous",
            status_code=400,
        )

    if subject_kind not in SOURCE_REVIEW_SUBJECT_KINDS or any(
        not isinstance(value, str) or not value
        for value in (subject_id, subject_state_id, subject_state_digest)
    ):
        raise AppOperationError(
            "RKBAPP-SOURCE-REVIEW-SUBJECT",
            "The source review subject binding is invalid",
            status_code=400,
        )
    return subject_kind, subject_id, subject_state_id, subject_state_digest


__all__ = [
    "MAX_SOURCE_REVIEW_CONFIRMATIONS_PER_SESSION",
    "SOURCE_REVIEW_CONFIRMATION_TTL_SECONDS",
    "SOURCE_REVIEW_SUBJECT_KINDS",
    "SourceReviewConfirmation",
    "SourceReviewConfirmationRegistry",
]
