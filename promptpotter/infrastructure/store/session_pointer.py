"""The per-tenant active-session pointer — which campaign + cycle the operator is on.

Every verb here is keyed on a :data:`~promptpotter.domain.cycle_paths.WorkspaceDir` —
the tenant's own root, ``Stores.base_dir`` — so there is no global pointer and by
construction no tenant can read or clobber another's. **The key is the whole contract —
never give it a default.** ``(tenant_id, projects_root=None)`` falling back to the
process-global ``DEFAULT_PROJECTS_ROOT`` makes omitting the root compile and be silently
wrong, and nearly every caller omits it. One is the L4 auto-rebase: an inner cycle
retargets the OPERATOR's pointer at a campaign under ``.inner/``, every per-cycle route
404s, and the dashboard goes blank mid-run. A resolved root is the only way to ask, and
the newtype stops the ``projects_root`` one level up from being passed by mistake.

Distinct from :class:`store.session_store.SessionStore`: this answers *which* session
is live, that stores *what* a session holds. Depends only on the pure leaves
``store.io`` + ``store.layout``, so it stays importable from anywhere without dragging
a store in.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from promptpotter.domain.cycle_paths import WorkspaceDir
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


def save_active_pointer(
    workspace: WorkspaceDir, session_id: str, campaign_id: str, cycle_id: str
) -> None:
    """Persist *workspace*'s active pointer — session + campaign + cycle.

    The workspace root selects the file; the JSON payload carries only the
    session / campaign / cycle ids.
    """
    validate_path_component(session_id)
    validate_path_component(campaign_id)
    validate_path_component(cycle_id)
    write_json(
        _active_pointer_path(workspace),
        {
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
        },
    )


def clear_active_pointer(workspace: WorkspaceDir) -> None:
    """Delete *workspace*'s active-session pointer file, if present. Idempotent."""
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
