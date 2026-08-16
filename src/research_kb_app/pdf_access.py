from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from research_kb_app.errors import AppOperationError


PDF_HANDLE_TTL_SECONDS = 15 * 60
MAX_PDF_HANDLES_PER_SESSION = 16
PDF_STREAM_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PdfHandleEntry:
    handle_id: str
    browser_session_id: str
    workspace_option_id: str
    core_handle: Any
    descriptor: dict[str, Any]
    expires_at: float


class PdfHandleRegistry:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
    ) -> None:
        self._clock = clock
        self._token_factory = token_factory
        self._entries: dict[str, PdfHandleEntry] = {}

    @property
    def count(self) -> int:
        self._purge_expired()
        return len(self._entries)

    def issue(
        self,
        *,
        browser_session_id: str,
        workspace_option_id: str,
        core_handle: Any,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._purge_expired()
        session_count = sum(
            entry.browser_session_id == browser_session_id
            for entry in self._entries.values()
        )
        if session_count >= MAX_PDF_HANDLES_PER_SESSION:
            raise AppOperationError(
                "RKBAPP-PDF-HANDLE-CAP",
                "Too many PDF handles are active for this browser session",
                status_code=429,
            )
        handle_id = self._token_factory()
        if not handle_id or handle_id in self._entries:
            raise AppOperationError(
                "RKBAPP-PDF-HANDLE-COLLISION",
                "A unique PDF handle could not be issued",
                status_code=500,
            )
        copied = deepcopy(dict(descriptor))
        self._entries[handle_id] = PdfHandleEntry(
            handle_id=handle_id,
            browser_session_id=browser_session_id,
            workspace_option_id=workspace_option_id,
            core_handle=core_handle,
            descriptor=copied,
            expires_at=self._clock() + PDF_HANDLE_TTL_SECONDS,
        )
        return {
            "status": "success",
            "handle_id": handle_id,
            "evidence_id": copied["evidence_id"],
            "pdf_page": copied["pdf_page"],
            "expires_in_seconds": PDF_HANDLE_TTL_SECONDS,
        }

    def require(
        self,
        handle_id: str,
        browser_session_id: str,
        workspace_option_id: str,
    ) -> PdfHandleEntry:
        entry = self._entries.get(handle_id)
        if entry is None:
            raise _handle_not_found()
        if self._clock() >= entry.expires_at:
            self._entries.pop(handle_id, None)
            raise AppOperationError(
                "RKBAPP-PDF-HANDLE-EXPIRED",
                "The PDF handle expired; open the Evidence PDF again",
                status_code=410,
            )
        if (
            entry.browser_session_id != browser_session_id
            or entry.workspace_option_id != workspace_option_id
        ):
            raise _handle_not_found()
        return entry

    def clear(self) -> None:
        self._entries.clear()

    def _purge_expired(self) -> None:
        now = self._clock()
        for handle_id in [
            key for key, entry in self._entries.items() if now >= entry.expires_at
        ]:
            self._entries.pop(handle_id, None)


@dataclass(frozen=True, slots=True)
class ResolvedByteRange:
    start: int
    end: int
    total_size: int
    status_code: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str | None:
        if self.status_code != 206:
            return None
        return f"bytes {self.start}-{self.end}/{self.total_size}"


class RangeNotSatisfiable(AppOperationError):
    def __init__(self, total_size: int):
        self.total_size = total_size
        super().__init__(
            "RKBAPP-PDF-RANGE",
            "The requested PDF byte range is not satisfiable",
            status_code=416,
        )


def resolve_byte_range(header: str | None, total_size: int) -> ResolvedByteRange:
    if total_size <= 0:
        raise RangeNotSatisfiable(total_size)
    if header is None:
        return ResolvedByteRange(0, total_size - 1, total_size, 200)
    if not header.startswith("bytes=") or "," in header:
        raise RangeNotSatisfiable(total_size)
    value = header[6:].strip()
    if not value or "-" not in value:
        raise RangeNotSatisfiable(total_size)
    start_text, end_text = value.split("-", 1)
    if not start_text:
        suffix = _positive_integer(end_text, total_size)
        start = max(0, total_size - suffix)
        return ResolvedByteRange(start, total_size - 1, total_size, 206)
    start = _non_negative_integer(start_text, total_size)
    if start >= total_size:
        raise RangeNotSatisfiable(total_size)
    if not end_text:
        return ResolvedByteRange(start, total_size - 1, total_size, 206)
    end = _non_negative_integer(end_text, total_size)
    if end < start:
        raise RangeNotSatisfiable(total_size)
    return ResolvedByteRange(start, min(end, total_size - 1), total_size, 206)


def _positive_integer(value: str, total_size: int) -> int:
    parsed = _non_negative_integer(value, total_size)
    if parsed == 0:
        raise RangeNotSatisfiable(total_size)
    return parsed


def _non_negative_integer(value: str, total_size: int) -> int:
    if not value or not value.isascii() or not value.isdigit():
        raise RangeNotSatisfiable(total_size)
    return int(value)


def _handle_not_found() -> AppOperationError:
    return AppOperationError(
        "RKBAPP-PDF-HANDLE-NOT-FOUND",
        "The PDF handle is unavailable",
        status_code=404,
    )


__all__ = [
    "MAX_PDF_HANDLES_PER_SESSION",
    "PDF_HANDLE_TTL_SECONDS",
    "PDF_STREAM_CHUNK_BYTES",
    "PdfHandleEntry",
    "PdfHandleRegistry",
    "RangeNotSatisfiable",
    "ResolvedByteRange",
    "resolve_byte_range",
]
