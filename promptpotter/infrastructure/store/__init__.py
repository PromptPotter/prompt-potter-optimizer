"""Focused store modules for file-based persistence.

Also owns the tenant-global active-session pointer
(``mint_session_id`` / ``save_active_pointer`` / ``read_active_pointer`` /
``clear_active_pointer`` / ``active_pointer_exists``) — payload shape is
``{tenant_id, session_id, campaign_id, cycle_id}``. The session is the
operator's active pointer/lens into the workspace: which campaign + cycle
are live.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from promptpotter.infrastructure.store import archive_views
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.base import validate_path_component, write_json
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.paths import (
    campaign_root_dir_for,
    cycle_dir_for,
    root_cycle_id,
    session_cycle_id,
    session_dir_for,
    session_index,
    sibling_kind,
)
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.stores import OptimizerCallCache, Stores, build_stores
from promptpotter.infrastructure.store.sweep_store import SweepStore

_ACTIVE_SESSION_PATH = Path(__file__).resolve().parents[3] / ".promptpotter" / "active_session.json"


def mint_session_id() -> str:
    """Mint a fresh, opaque session id (``s_<8 hex>``)."""
    return f"s_{uuid.uuid4().hex[:8]}"


def save_active_pointer(tenant_id: str, session_id: str, campaign_id: str, cycle_id: str) -> None:
    """Persist the active pointer — tenant, session, campaign, cycle."""
    validate_path_component(tenant_id)
    validate_path_component(session_id)
    validate_path_component(campaign_id)
    validate_path_component(cycle_id)
    write_json(
        _ACTIVE_SESSION_PATH,
        {
            "tenant_id": tenant_id,
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
        },
    )


def clear_active_pointer() -> None:
    """Delete the active-session pointer file, if present. Idempotent."""
    _ACTIVE_SESSION_PATH.unlink(missing_ok=True)


def read_active_pointer() -> tuple[str, str, str, str]:
    """Return ``(tenant_id, session_id, campaign_id, cycle_id)`` from the pointer.

    Returns ``("", "", "", "")`` when the pointer is missing or unreadable.
    """
    if not _ACTIVE_SESSION_PATH.exists():
        return "", "", "", ""
    try:
        ptr = json.loads(_ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", "", ""
    return (
        ptr.get("tenant_id", ""),
        ptr.get("session_id", ""),
        ptr.get("campaign_id", ""),
        ptr.get("cycle_id", ""),
    )


def active_pointer_exists() -> bool:
    """Public predicate — whether the active-session pointer file is on disk."""
    return _ACTIVE_SESSION_PATH.exists()


def walk_cycle_lineage(tenant_root: Path, campaign_id: str, cycle_id: str) -> list[str]:
    """Walk the ``parent_cycle_id`` chain via index.json reads. Returns ``[root, …, cycle_id]``.

    All cycles of a campaign's lineage live flat under the same
    ``campaigns/{campaign_id}/cycles/`` dir; this follows the
    ``parent_cycle_id`` field until it hits the root. O(depth) reads.
    """
    chain = [cycle_id]
    current = cycle_id
    while True:
        idx_path = cycle_dir_for(tenant_root, campaign_id, current) / "index.json"
        if not idx_path.exists():
            break
        try:
            data = json.loads(idx_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            break
        parent = data.get("parent_cycle_id")
        if not parent:
            break
        chain.insert(0, str(parent))
        current = str(parent)
    return chain


__all__ = [
    "BackendStore",
    "CampaignStore",
    "MeasurementArchive",
    "OptimizerCallCache",
    "SessionStore",
    "Stores",
    "SweepStore",
    "active_pointer_exists",
    "archive_views",
    "build_stores",
    "campaign_root_dir_for",
    "clear_active_pointer",
    "cycle_dir_for",
    "mint_session_id",
    "read_active_pointer",
    "root_cycle_id",
    "save_active_pointer",
    "session_cycle_id",
    "session_dir_for",
    "session_index",
    "sibling_kind",
    "walk_cycle_lineage",
]
