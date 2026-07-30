"""The per-tenant active-session pointer — which campaign + cycle the operator is on.

The tenant slug is the path key, never a JSON field, so there is no global pointer and by
construction no tenant can read or clobber another's. Distinct from
:class:`store.session_store.SessionStore`: this answers *which* session is live, that
stores *what* a session holds. Depends only on the pure leaves ``store.io`` +
``store.layout``, so it stays importable from anywhere without dragging a store in.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.identity import TenantId
from promptpotter.infrastructure.store.io import (
    read_json_tolerant,
    validate_path_component,
    write_json,
)


def _tenant_root(tenant_id: TenantId, projects_root: Path | None = None) -> Path:
    validate_path_component(tenant_id)
    return (projects_root if projects_root is not None else DEFAULT_PROJECTS_ROOT) / tenant_id


def _active_pointer_path_under(tenant_root: Path) -> Path:
    """Pointer path under an ALREADY-RESOLVED tenant root — the one place the
    ``.workspace/active_session.json`` layout is written down."""
    return tenant_root / ".workspace" / "active_session.json"


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
        _active_pointer_path_under(_tenant_root(tenant_id, projects_root)),
        {
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
        },
    )


def clear_active_pointer_under(tenant_root: Path) -> None:
    """Drop the pointer under an already-resolved tenant root. Idempotent.

    The root-keyed core, same as :func:`read_active_pointer_under`: ``CampaignStore``
    holds a resolved root and releases the pointer when the campaign it names is
    archived or deleted, so it must not have to re-derive the tenant slug it already
    resolved past.
    """
    _active_pointer_path_under(tenant_root).unlink(missing_ok=True)


def clear_active_pointer(tenant_id: TenantId, *, projects_root: Path | None = None) -> None:
    """Delete the tenant's active-session pointer file, if present. Idempotent."""
    clear_active_pointer_under(_tenant_root(tenant_id, projects_root))


def read_active_pointer_under(tenant_root: Path) -> tuple[str, str, str]:
    """``(session_id, campaign_id, cycle_id)`` under an already-resolved tenant root.

    Returns ``("", "", "")`` when the pointer is missing or unreadable.
    """
    ptr = read_json_tolerant(_active_pointer_path_under(tenant_root))
    if not isinstance(ptr, dict):
        return "", "", ""
    return (
        ptr.get("session_id", ""),
        ptr.get("campaign_id", ""),
        ptr.get("cycle_id", ""),
    )


def read_active_pointer(
    tenant_id: TenantId, *, projects_root: Path | None = None
) -> tuple[str, str, str]:
    """Return ``(session_id, campaign_id, cycle_id)`` for *tenant_id*."""
    return read_active_pointer_under(_tenant_root(tenant_id, projects_root))


def active_pointer_exists(tenant_id: TenantId, *, projects_root: Path | None = None) -> bool:
    return _active_pointer_path_under(_tenant_root(tenant_id, projects_root)).exists()


__all__ = [
    "active_pointer_exists",
    "clear_active_pointer",
    "clear_active_pointer_under",
    "mint_session_id",
    "read_active_pointer",
    "read_active_pointer_under",
    "save_active_pointer",
]
