"""
Session persistence — cross-phase campaign state.

Manages session lifecycle across all campaign phases (init, scan, optimize,
results). Both notebook and CLI use this store, so artifacts are shared.

Disk layout::

    {backend_id}/sessions/{session_id}/
        session.json           — config, phase, init params, cycle_id
        scan_results.json      — scan_df records + axis_profiles
        campaign_log.md        — append-only structured report
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    read_json_optional,
    validate_path_component,
    write_json,
)

logger = logging.getLogger(__name__)

# Thin pointer to the active session — survives across CLI invocations
_ACTIVE_SESSION_PATH = Path(".promptpotter") / "active_session.json"


def generate_session_id() -> str:
    """Generate a unique session identifier."""
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    return f"{ts}_{short}"


class SessionStore:
    """File I/O for campaign session state."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _session_dir(self, backend_id: str, session_id: str) -> Path:
        validate_path_component(backend_id)
        validate_path_component(session_id)
        return self._base_dir / backend_id / "sessions" / session_id

    # -- Session state ---------------------------------------------------------

    def create(self, backend_id: str, state: dict[str, Any]) -> str:
        """Create a new session. Returns session_id."""
        session_id = generate_session_id()
        state["session_id"] = session_id
        state["created_at"] = datetime.now(UTC).isoformat()
        self.save(backend_id, session_id, state)
        return session_id

    def save(self, backend_id: str, session_id: str, state: dict[str, Any]) -> None:
        """Persist session state."""
        path = self._session_dir(backend_id, session_id) / "session.json"
        write_json(path, state)

    def load(self, backend_id: str, session_id: str) -> dict[str, Any] | None:
        """Load session state. Returns None if not found."""
        path = self._session_dir(backend_id, session_id) / "session.json"
        return read_json_optional(path)

    def list_sessions(self, backend_id: str) -> list[str]:
        """List all session IDs for a backend, sorted by creation time."""
        validate_path_component(backend_id)
        sessions_dir = self._base_dir / backend_id / "sessions"
        if not sessions_dir.exists():
            return []
        return sorted(
            d.name for d in sessions_dir.iterdir()
            if d.is_dir() and (d / "session.json").exists()
        )

    def latest_session_id(self, backend_id: str) -> str | None:
        """Return the most recent session ID, or None."""
        ids = self.list_sessions(backend_id)
        return ids[-1] if ids else None

    # -- Scan results ----------------------------------------------------------

    def save_scan_results(
        self,
        backend_id: str,
        session_id: str,
        scan_df_records: list[dict],
        axis_profiles: list[dict],
    ) -> None:
        """Persist sensitivity scan results (fills the persistence gap)."""
        path = self._session_dir(backend_id, session_id) / "scan_results.json"
        write_json(path, {
            "scan_df": scan_df_records,
            "axis_profiles": axis_profiles,
            "saved_at": datetime.now(UTC).isoformat(),
        })

    def load_scan_results(
        self,
        backend_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """Load scan results. Returns None if not found."""
        path = self._session_dir(backend_id, session_id) / "scan_results.json"
        return read_json_optional(path)

    # -- Campaign log ----------------------------------------------------------

    def append_log(
        self,
        backend_id: str,
        session_id: str,
        section: str,
    ) -> None:
        """Append a markdown section to the campaign log."""
        path = self._session_dir(backend_id, session_id) / "campaign_log.md"
        with open(path, "a", encoding="utf-8") as f:
            f.write(section + "\n\n")

    def load_log(self, backend_id: str, session_id: str) -> str:
        """Load the full campaign log. Returns empty string if not found."""
        path = self._session_dir(backend_id, session_id) / "campaign_log.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    # -- Active session pointer ------------------------------------------------

    def save_active_pointer(self, backend_id: str, session_id: str) -> None:
        """Persist pointer to the active session across CLI invocations."""
        _ACTIVE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ACTIVE_SESSION_PATH.write_text(
            json.dumps({"backend_id": backend_id, "session_id": session_id}),
            encoding="utf-8",
        )

    def load_active(
        self, session_override: str | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        """Load active session state + backend_id + session_id.

        Reads ``.promptpotter/active_session.json`` to find the active
        session, then loads the full session state.

        Raises ``SystemExit`` if no active session or session not found.
        """
        if not _ACTIVE_SESSION_PATH.exists():
            raise SystemExit("ERROR: No active session. Run 'init' first.")
        ptr = json.loads(_ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
        bid = ptr["backend_id"]
        sid = session_override or ptr["session_id"]
        state = self.load(bid, sid)
        if not state:
            raise SystemExit(f"ERROR: Session '{sid}' not found for backend '{bid}'.")
        return state, bid, sid
