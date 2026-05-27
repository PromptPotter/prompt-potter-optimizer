"""Focused store modules for file-based persistence.

Also owns the per-tenant active-session pointer
(``mint_session_id`` / ``save_active_pointer`` / ``read_active_pointer`` /
``clear_active_pointer`` / ``active_pointer_exists``). The file lives at
``projects/{tenant}/.workspace/active_session.json`` (alongside the
workspace ledger); the tenant slug is the path key, not a JSON field, so
the on-disk shape is ``{session_id, campaign_id, cycle_id}``. The session
is the operator's lens into the workspace: which campaign + cycle are live.

No global pointer — by construction, no tenant can read or clobber another
tenant's active session.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from promptpotter.domain.identity import TenantId
from promptpotter.infrastructure.store import archive_views
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.base import validate_path_component, write_json
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.paths import (
    DEFAULT_PROJECTS_ROOT,
    campaign_root_dir_for,
    cycle_dir_for,
    root_cycle_id,
    session_dir_for,
    session_index,
    sibling_kind,
)
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.stores import OptimizerCallCache, Stores, build_stores
from promptpotter.infrastructure.store.sweep_store import SweepStore
from promptpotter.infrastructure.store.user_store import User, UserStore


def _active_pointer_path(tenant_id: TenantId, projects_root: Path | None = None) -> Path:
    """Resolve the per-tenant active-pointer file under the tenant workspace dir."""
    validate_path_component(tenant_id)
    root = projects_root if projects_root is not None else DEFAULT_PROJECTS_ROOT
    return root / tenant_id / ".workspace" / "active_session.json"


def mint_session_id() -> str:
    """Mint a fresh, opaque session id (``s_<8 hex>``)."""
    return f"s_{uuid.uuid4().hex[:8]}"


def save_active_pointer(
    tenant_id: TenantId,
    session_id: str,
    campaign_id: str,
    cycle_id: str,
    *,
    projects_root: Path | None = None,
) -> None:
    """Persist the tenant's active pointer — session + campaign + cycle.

    *tenant_id* selects the file path; the JSON payload itself carries only
    the session / campaign / cycle ids.
    """
    validate_path_component(session_id)
    validate_path_component(campaign_id)
    validate_path_component(cycle_id)
    write_json(
        _active_pointer_path(tenant_id, projects_root),
        {
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
        },
    )


def clear_active_pointer(tenant_id: TenantId, *, projects_root: Path | None = None) -> None:
    """Delete the tenant's active-session pointer file, if present. Idempotent."""
    _active_pointer_path(tenant_id, projects_root).unlink(missing_ok=True)


def read_active_pointer(
    tenant_id: TenantId, *, projects_root: Path | None = None
) -> tuple[str, str, str]:
    """Return ``(session_id, campaign_id, cycle_id)`` for *tenant_id*.

    Returns ``("", "", "")`` when the pointer is missing or unreadable.
    """
    path = _active_pointer_path(tenant_id, projects_root)
    if not path.exists():
        return "", "", ""
    try:
        ptr = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "", "", ""
    return (
        ptr.get("session_id", ""),
        ptr.get("campaign_id", ""),
        ptr.get("cycle_id", ""),
    )


def active_pointer_exists(tenant_id: TenantId, *, projects_root: Path | None = None) -> bool:
    """Public predicate — whether the tenant's active-session pointer is on disk."""
    return _active_pointer_path(tenant_id, projects_root).exists()


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
    "DiagnosticRunStore",
    "MeasurementArchive",
    "OptimizerCallCache",
    "SessionStore",
    "Stores",
    "SweepStore",
    "User",
    "UserStore",
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
    "session_dir_for",
    "session_index",
    "sibling_kind",
    "walk_cycle_lineage",
    "write_json",
]
