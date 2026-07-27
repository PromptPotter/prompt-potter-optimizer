"""Server-side opaque session store.

Each session is a JSON file at `.promptpotter/identity/sessions/{id}.json`.
The cookie carries the opaque session_id only — no JWT, no signed
envelope (per ADR-0002 no-drift gate #2: tokens never appear past the
middleware). 32 bytes of `secrets.token_urlsafe` entropy is sufficient
for unguessability.

TTL: 7 days from creation; expired sessions are deleted on read miss.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from promptpotter.infrastructure.store.io import read_json, write_json

logger = logging.getLogger(__name__)

DEFAULT_SESSION_TTL_S = 60 * 60 * 24 * 7  # 7 days


@dataclass(frozen=True)
class SessionData:
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
    """File-backed opaque store for BROWSER LOGIN sessions — the OIDC cookie a
    request authenticates with.

    NOT :class:`promptpotter.infrastructure.store.SessionStore`, which persists a
    campaign run's session artifacts. Same word, two referents; the ``OIDC`` prefix
    is what keeps a reader from grabbing the wrong one."""

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
        """Mint a new session — random id, persisted; returns (session_id, data)."""
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
        write_json(
            self._path(session_id),
            {
                "user_id": data.user_id,
                "tenant_id": data.tenant_id,
                "issuer": data.issuer,
                "subject": data.subject,
                "email": data.email,
                "provider": data.provider,
                "created_at": data.created_at,
                "expires_at": data.expires_at,
            },
        )
        return session_id, data

    def read(self, session_id: str) -> SessionData | None:
        """Return the session or `None`. Expired sessions are deleted on miss."""
        if not session_id or "/" in session_id or "\\" in session_id:
            return None
        path = self._path(session_id)
        if not path.is_file():
            return None
        try:
            raw = read_json(path)
        except (OSError, JSONDecodeError):
            # Don't log the session id — it's the sole bearer credential.
            logger.warning("unreadable session file, deleting")
            path.unlink(missing_ok=True)
            return None
        data = SessionData(
            user_id=str(raw.get("user_id", "")),
            tenant_id=str(raw.get("tenant_id", "")),
            issuer=str(raw.get("issuer", "")),
            subject=str(raw.get("subject", "")),
            email=raw.get("email"),
            provider=str(raw.get("provider", "")),
            created_at=int(raw.get("created_at", 0)),
            expires_at=int(raw.get("expires_at", 0)),
        )
        if data.is_expired():
            path.unlink(missing_ok=True)
            return None
        return data

    def delete(self, session_id: str) -> None:
        """Idempotent logout."""
        if not session_id or "/" in session_id or "\\" in session_id:
            return
        self._path(session_id).unlink(missing_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"


__all__ = ["DEFAULT_SESSION_TTL_S", "OIDCSessionStore", "SessionData"]
