from __future__ import annotations

import secrets
from dataclasses import dataclass


SESSION_COOKIE = "rkb_session"
CSRF_HEADER = "x-rkb-csrf"


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserSession:
    session_id: str
    csrf_token: str


class SessionManager:
    def __init__(self, startup_token: str):
        if len(startup_token) < 32:
            raise ValueError("startup token is too short")
        self._startup_token: str | None = startup_token
        self._sessions: dict[str, BrowserSession] = {}

    def bootstrap(self, candidate: str) -> BrowserSession:
        expected = self._startup_token
        if expected is None or not secrets.compare_digest(expected, candidate):
            raise AuthenticationError("startup token is invalid or already used")
        self._startup_token = None
        session = BrowserSession(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
        )
        self._sessions[session.session_id] = session
        return session

    def require(self, session_id: str | None) -> BrowserSession:
        if session_id is None or session_id not in self._sessions:
            raise AuthenticationError("authenticated browser session is required")
        return self._sessions[session_id]

    @staticmethod
    def require_csrf(session: BrowserSession, candidate: str | None) -> None:
        if candidate is None or not secrets.compare_digest(session.csrf_token, candidate):
            raise AuthenticationError("CSRF token is invalid")

    def clear(self) -> None:
        self._startup_token = None
        self._sessions.clear()


def new_startup_token() -> str:
    return secrets.token_urlsafe(32)


__all__ = [
    "AuthenticationError",
    "BrowserSession",
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "SessionManager",
    "new_startup_token",
]
