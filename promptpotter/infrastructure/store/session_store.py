"""Per-session metadata under ``sessions/``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptpotter.infrastructure.store.base import (
    read_json,
    read_json_optional,
    write_json,
)
from promptpotter.infrastructure.store.paths import session_dir_for


class SessionStore:
    """File I/O for per-session artifacts.

    Sessions are tenant-scoped (no ``backend_id`` axis). The store is
    rooted at the tenant directory; per-session content nests under
    ``sessions/{session_id}/``.
    """

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    # -- Path helpers ---------------------------------------------------------

    def session_dir(self, session_id: str) -> Path:
        """Public accessor for ``{tenant_root}/sessions/{session_id}``."""
        return session_dir_for(self._base_dir, session_id)

    def _state_path(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    # -- Session CRUD ---------------------------------------------------------

    def create(self, session_id: str, state: dict[str, Any]) -> Path:
        """Write ``session.json`` with timestamps.

        Idempotent: a re-create preserves the existing ``created_at`` and
        merges new keys over old. ``updated_at`` is always now.
        """
        path = self._state_path(session_id)
        existing = read_json_optional(path) or {}
        now = datetime.now(UTC).isoformat()
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
        """Merge *updates* into ``session.json``. Updates ``updated_at``."""
        path = self._state_path(session_id)
        data = read_json(path)
        data.update(updates)
        data["updated_at"] = datetime.now(UTC).isoformat()
        write_json(path, data)


__all__ = ["SessionStore"]
