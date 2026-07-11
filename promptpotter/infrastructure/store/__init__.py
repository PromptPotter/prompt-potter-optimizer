"""Store — composite over focused leaf stores for file-based persistence.

CONCEPT MAP (re-exported surface; import from each leaf for internals):
* **stores** — :class:`Stores` frozen bundle + :func:`build_stores(identity)`
  builder (composite over the leaves); ``OptimizerCallCache`` (SHA-256-keyed,
  cross-fork optimizer-call cache, mirror of the archive).
* **leaf stores** — :class:`BackendStore`, :class:`CampaignStore`
  (``campaign_store/`` package), :class:`SessionStore`, :class:`SweepStore`,
  :class:`DiagnosticRunStore`, :class:`TenantDatasetStore`, :class:`UserStore`
  (+ ``User``).
* **base** (:mod:`.base`) — shared I/O (``write_json`` / ``read_json``,
  ``validate_path_component``).
* **paths** (:mod:`.paths`) — pure campaign/cycle dir builders + cycle-id
  parsing (``cycle_dir_for``, ``root_cycle_id``, ``sibling_kind``, …).
* **dataset_access** — identity-aware dataset read gateway
  (``list_readable_datasets`` / ``readable_dataset_dir`` / ``DatasetRef``).
* **measurement_archive** — :class:`MeasurementArchive`, the append-only
  content-addressed DB core (sole source for the derived intelligence views).

Also owns the per-tenant active-session pointer
(``mint_session_id`` / ``save_active_pointer`` / ``read_active_pointer`` /
``clear_active_pointer`` / ``active_pointer_exists``). The file lives at
``projects/{tenant}/.workspace/active_session.json`` (alongside the
workspace ledger); the tenant slug is the path key, not a JSON field, so
the on-disk shape is ``{session_id, campaign_id, cycle_id}``. The session
is the operator's lens into the workspace: which campaign + cycle are live.
``read_active_pointer_under`` is the tenant-root-keyed core the id-keyed
readers wrap — ``CampaignStore`` holds a resolved root, so it reads through
that seam rather than re-encoding the path.

No global pointer — by construction, no tenant can read or clobber another
tenant's active session.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from promptpotter.domain.identity import TenantId
from promptpotter.infrastructure.store import archive_views
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.checkin_draft_store import CheckinDraftStore
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    DatasetRef,
    list_readable_datasets,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.io import (
    read_json_tolerant,
    validate_path_component,
    write_json,
)
from promptpotter.infrastructure.store.layout import (
    DEFAULT_PROJECTS_ROOT,
    campaign_root_dir_for,
    cycle_dir_for,
    root_cycle_id,
    session_dir_for,
    sibling_kind,
)
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.session_store import SessionStore
from promptpotter.infrastructure.store.stores import OptimizerCallCache, Stores, build_stores
from promptpotter.infrastructure.store.sweep_store import SweepStore
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore
from promptpotter.infrastructure.store.user_store import User, UserStore


def _tenant_root(tenant_id: TenantId, projects_root: Path | None = None) -> Path:
    validate_path_component(tenant_id)
    return (projects_root if projects_root is not None else DEFAULT_PROJECTS_ROOT) / tenant_id


def active_pointer_path_under(tenant_root: Path) -> Path:
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
        active_pointer_path_under(_tenant_root(tenant_id, projects_root)),
        {
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": cycle_id,
        },
    )


def clear_active_pointer(tenant_id: TenantId, *, projects_root: Path | None = None) -> None:
    """Delete the tenant's active-session pointer file, if present. Idempotent."""
    active_pointer_path_under(_tenant_root(tenant_id, projects_root)).unlink(missing_ok=True)


def read_active_pointer_under(tenant_root: Path) -> tuple[str, str, str]:
    """``(session_id, campaign_id, cycle_id)`` under an already-resolved tenant root.

    Returns ``("", "", "")`` when the pointer is missing or unreadable.
    """
    ptr = read_json_tolerant(active_pointer_path_under(tenant_root))
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
    """Public predicate — whether the tenant's active-session pointer is on disk."""
    return active_pointer_path_under(_tenant_root(tenant_id, projects_root)).exists()


__all__ = [
    "BackendStore",
    "CampaignStore",
    "CheckinDraftStore",
    "DatasetAccessError",
    "DatasetRef",
    "DiagnosticRunStore",
    "MeasurementArchive",
    "OptimizerCallCache",
    "SessionStore",
    "Stores",
    "SweepStore",
    "TenantDatasetStore",
    "User",
    "UserStore",
    "active_pointer_exists",
    "active_pointer_path_under",
    "archive_views",
    "build_stores",
    "campaign_root_dir_for",
    "clear_active_pointer",
    "cycle_dir_for",
    "list_readable_datasets",
    "mint_session_id",
    "read_active_pointer",
    "read_active_pointer_under",
    "readable_dataset_dir",
    "root_cycle_id",
    "save_active_pointer",
    "session_dir_for",
    "sibling_kind",
    "write_json",
]
