from __future__ import annotations

from research_kb.errors import ResearchKBError


class AppOperationError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


_PUBLIC_CORE_MESSAGES = {
    "RKBC-002": "The request does not match the Core contract",
    "RKBC-005": "The requested record does not exist",
    "RKBC-014": "The source changed and must be revalidated before PDF access",
    "RKBC-017": "The record changed before this operation completed",
    "RKBC-018": "The operation receipt is incomplete or inconsistent",
    "RKBC-029": "The Evidence source is not a supported PDF",
    "RKBC-030": "The Evidence PDF exceeds the supported size budget",
    "RKBC-036": "The requested operation conflicts with current state",
}


def public_core_error(error: ResearchKBError) -> tuple[int, str, str]:
    code = error.diagnostic.code
    status = {
        "RKBC-005": 404,
        "RKBC-014": 409,
        "RKBC-017": 409,
        "RKBC-029": 415,
        "RKBC-030": 413,
        "RKBC-036": 409,
    }.get(code, 400)
    return status, code, _PUBLIC_CORE_MESSAGES.get(code, "Core rejected the operation")


__all__ = ["AppOperationError", "public_core_error"]
