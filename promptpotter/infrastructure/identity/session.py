"""The cookie carries the session id only — no JWT, no signed envelope (decisions.md § 0002)."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import read_bytes_optional, unlink_robust, write_json

DEFAULT_SESSION_TTL_S = 60 * 60 * 24 * 7


class SessionData(FrozenModel):
    user_id: str
    tenant_id: str
    issuer: str
    subject: str
    email: str | None
    provider: str
    created_at: int
    expires_at: int

    def is_expired(self, now: int | None = None) -> bool:
        return (now if now is not None else int(time.time())) >= self.expires_at


class OIDCSessionStore:
    def __init__(self, sessions_dir: Path, ttl_s: int = DEFAULT_SESSION_TTL_S) -> None:
        self._dir = sessions_dir
        self._ttl_s = ttl_s

    def create(
        self,
        *,
        user_id: str,
        tenant_id: str,
        issuer: str,
        subject: str,
        email: str | None,
        provider: str,
    ) -> tuple[str, SessionData]:
        # Swept at write: a person who never returns leaves no email or user id on disk past the TTL.
        for held in self._dir.glob("*.json") if self._dir.is_dir() else ():
            _live(held)
        session_id = secrets.token_urlsafe(32)
        now = int(time.time())
        data = SessionData(
            user_id=user_id,
            tenant_id=tenant_id,
            issuer=issuer,
            subject=subject,
            email=email,
            provider=provider,
            created_at=now,
            expires_at=now + self._ttl_s,
        )
        write_json(self._dir / f"{session_id}.json", data.model_dump(mode="json"))
        return session_id, data

    def read(self, session_id: str) -> SessionData | None:
        path = self._path(session_id)
        return None if path is None else _live(path)

    def delete(self, session_id: str) -> None:
        if (path := self._path(session_id)) is not None:
            unlink_robust(path)

    def _path(self, session_id: str) -> Path | None:
        """``None`` for an id that names no file: the cookie is the caller's input."""
        if not session_id or "/" in session_id or "\\" in session_id:
            return None
        return self._dir / f"{session_id}.json"


def _live(path: Path) -> SessionData | None:
    """A cache read: a session file is a login, so one this build cannot read or one past its TTL
    is deleted and reads as no session; the person signs in again."""
    raw = read_bytes_optional(path)
    if raw is None:
        return None
    try:
        data: SessionData | None = SessionData.model_validate_json(raw)
    except ValidationError:
        data = None
    if data is None or data.is_expired():
        unlink_robust(path)
        return None
    return data
