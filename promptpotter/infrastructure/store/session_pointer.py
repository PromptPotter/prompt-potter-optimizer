"""The per-tenant active-session pointer, keyed on a ``WorkspaceDir``. **Never give that key a default** — a
process-global fallback lets an inner cycle retarget the OPERATOR's pointer and blank the dashboard."""

from __future__ import annotations

import uuid
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.infrastructure.store.io import (
    read_json_tolerant,
    validate_path_component,
    write_json,
)


def _active_pointer_path(workspace: WorkspaceDir) -> Path:
    """The one place the ``.workspace/active_session.json`` layout is written down."""
    return workspace / ".workspace" / "active_session.json"


def mint_session_id() -> str:
    """Mint a fresh, opaque session id (``s_<8 hex>``)."""
    return f"s_{uuid.uuid4().hex[:8]}"


def save_active_pointer(workspace: WorkspaceDir, session_id: str, hop: CycleHop) -> None:
    """Persist the workspace's active pointer. The workspace ROOT selects the file; the payload carries only the three ids."""
    validate_path_component(session_id)
    validate_path_component(hop.campaign_id)
    validate_path_component(hop.cycle_id)
    write_json(
        _active_pointer_path(workspace),
        {
            "session_id": session_id,
            "campaign_id": hop.campaign_id,
            "cycle_id": hop.cycle_id,
        },
    )


def clear_active_pointer(workspace: WorkspaceDir) -> None:
    _active_pointer_path(workspace).unlink(missing_ok=True)


def read_active_pointer(workspace: WorkspaceDir) -> tuple[str, str, str]:
    """``(session_id, campaign_id, cycle_id)``; ``("", "", "")`` when missing or unreadable."""
    ptr = read_json_tolerant(_active_pointer_path(workspace))
    if not isinstance(ptr, dict):
        return "", "", ""
    return (
        ptr.get("session_id", ""),
        ptr.get("campaign_id", ""),
        ptr.get("cycle_id", ""),
    )


def active_pointer_exists(workspace: WorkspaceDir) -> bool:
    return _active_pointer_path(workspace).exists()


__all__ = [
    "active_pointer_exists",
    "clear_active_pointer",
    "mint_session_id",
    "read_active_pointer",
    "save_active_pointer",
]
