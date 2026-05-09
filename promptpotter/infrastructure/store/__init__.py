"""Focused store modules for file-based persistence.

Also owns the tenant-global active-session pointer
(``mint_session_id`` / ``save_active_pointer`` / ``read_active_pointer`` /
``clear_active_pointer`` / ``active_pointer_exists``) — payload shape is
``{tenant_id, session_id, cycle_id}``. Callers that need ``backend_id``
read it from the session's state blob.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from promptpotter.infrastructure.store import archive_views
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.base import validate_path_component
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.paths import (
    campaign_dir_for,
    root_cycle_id,
    root_dir_for,
    session_dir_for,
    sibling_kind,
    sweep_batch_dir_for,
)
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.stores import OptimizerCallCache, Stores, build_stores
from promptpotter.infrastructure.store.sweep_store import SweepStore

_ACTIVE_SESSION_PATH = Path(__file__).resolve().parents[3] / ".promptpotter" / "active_session.json"


def mint_session_id() -> str:
    """Mint a fresh, opaque session id (``s_<8 hex>``).

    Random — no relation to problem identity. Stays stable across
    multiple campaigns under the same session in the future 1:N world.
    """
    return f"s_{uuid.uuid4().hex[:8]}"


def save_active_pointer(tenant_id: str, session_id: str, cycle_id: str) -> None:
    """Persist pointer to the active session+campaign across CLI invocations."""
    validate_path_component(tenant_id)
    validate_path_component(session_id)
    validate_path_component(cycle_id)
    _ACTIVE_SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_SESSION_PATH.write_text(
        json.dumps({"tenant_id": tenant_id, "session_id": session_id, "cycle_id": cycle_id}),
        encoding="utf-8",
    )


def clear_active_pointer() -> None:
    """Delete the active-session pointer file, if present. Idempotent."""
    _ACTIVE_SESSION_PATH.unlink(missing_ok=True)


def read_active_pointer() -> tuple[str, str, str]:
    """Return ``(tenant_id, session_id, cycle_id)`` from the pointer.

    Returns ``("", "", "")`` when the pointer is missing or unreadable.
    Does NOT raise if a referenced session/campaign has been deleted —
    only inspects the raw pointer file. Used by guardrails that need to
    compare the pointer to a requested tenant without coupling to session
    lifecycle.
    """
    if not _ACTIVE_SESSION_PATH.exists():
        return "", "", ""
    try:
        ptr = json.loads(_ACTIVE_SESSION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", ""
    return (
        ptr.get("tenant_id", ""),
        ptr.get("session_id", ""),
        ptr.get("cycle_id", ""),
    )


def active_pointer_exists() -> bool:
    """Public predicate — whether the active-session pointer file is on disk."""
    return _ACTIVE_SESSION_PATH.exists()


def walk_cycle_lineage(tenant_root: Path, cycle_id: str) -> list[str]:
    """Walk parent_cycle_id chain via index.json reads. Returns ``[root, …, cycle_id]``.

    Each fork's ``index.json`` carries ``parent_cycle_id``; this walker
    follows that chain until it hits a cycle with no parent (the family
    root). The dashboard surfaces the result as ``cycle_id_path`` so live
    readers + the webapp can render the branch tree without parsing the
    cycle-id string encoding (which is fragile to future trigger
    additions).

    O(depth) reads. Idempotent on re-call. Stops on the first missing
    ``index.json`` (treats it as the root) so an in-flight fork before
    its index is written still produces a partial-but-stable path.
    """
    chain = [cycle_id]
    current = cycle_id
    while True:
        idx_path = campaign_dir_for(tenant_root, current) / "index.json"
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
    "campaign_dir_for",
    "clear_active_pointer",
    "mint_session_id",
    "read_active_pointer",
    "root_cycle_id",
    "root_dir_for",
    "save_active_pointer",
    "session_dir_for",
    "sibling_kind",
    "sweep_batch_dir_for",
    "walk_cycle_lineage",
]
