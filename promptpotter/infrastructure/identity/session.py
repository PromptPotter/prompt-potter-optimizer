"""The cookie carries the session id only — no JWT, no signed envelope (decisions.md § 0002)."""

from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import read_bytes_optional, write_json

logger = logging.getLogger(__name__)

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
        write_json(self._path(session_id), data.model_dump(mode="json"))
        return session_id, data

    def read(self, session_id: str) -> SessionData | None:
        if not session_id or "/" in session_id or "\\" in session_id:
            return None
        path = self._path(session_id)
        raw = read_bytes_optional(path)
        if raw is None:
            return None
        try:
            data = SessionData.model_validate_json(raw)
        except ValidationError:
            # Don't log the session id — it's the sole bearer credential.
            logger.warning("unreadable session file, deleting")
            path.unlink(missing_ok=True)
            return None
        if data.is_expired():
            path.unlink(missing_ok=True)
            return None
        return data

    def delete(self, session_id: str) -> None:
        if not session_id or "/" in session_id or "\\" in session_id:
            return
        self._path(session_id).unlink(missing_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"
