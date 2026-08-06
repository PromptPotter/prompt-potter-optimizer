from __future__ import annotations

from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import WorkspaceDir
from promptpotter.infrastructure.store.io import (
    read_json,
    read_json_optional,
    write_json,
)
from promptpotter.infrastructure.store.layout import session_dir_for
from promptpotter.shared.clock import utcnow_iso


class SessionStore:
    """Tenant-scoped per-session artifacts. A CAMPAIGN RUN's session, not a browser login — that is ``OIDCSessionStore``."""

    def __init__(self, base_dir: WorkspaceDir):
        self._base_dir = base_dir

    # -- Path helpers ---------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        return session_dir_for(self._base_dir, session_id)

    def _state_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    # -- Session CRUD ---------------------------------------------------------

    def create(self, session_id: str, state: dict[str, Any]) -> Path:
        """Idempotent ``session.json`` write — preserves ``created_at``, merges over old, refreshes ``updated_at``."""
        path = self._state_path(session_id)
        existing = read_json_optional(path) or {}
        now = utcnow_iso()
        data = {
            **existing,
            **state,
            "session_id": session_id,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        write_json(path, data)
        return path

    def read(self, session_id: str) -> dict[str, Any] | None:
        return read_json_optional(self._state_path(session_id))

    def update(self, session_id: str, updates: dict[str, Any]) -> None:
        path = self._state_path(session_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = utcnow_iso()
        write_json(path, data)


__all__ = ["SessionStore"]
