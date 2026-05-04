"""Composite ``Stores`` bundle — frozen dataclass + builder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.base import validate_path_component
from promptpotter.infrastructure.store.campaign_store import CampaignStore
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.optimizer_call_cache import OptimizerCallCache
from promptpotter.infrastructure.store.paths import (
    DEFAULT_DATASETS_ROOT,
    DEFAULT_PROJECTS_ROOT,
    DEFAULT_TENANT_ID,
)
from promptpotter.infrastructure.store.session_store import SessionStore


@dataclass(frozen=True)
class Stores:
    """Composite bundle of focused stores rooted at a per-tenant ``base_dir``.

    Construct via :func:`build_stores`. ``base_dir`` is the tenant root
    (``{projects_root}/{tenant_id}/``). Sessions and campaigns are peer
    trees under the tenant; a campaign records its parent session via
    ``index.json::parent_session_id``.
    """

    base_dir: Path
    tenant_id: str
    backends: BackendStore
    sessions: SessionStore
    campaigns: CampaignStore
    archive: MeasurementArchive
    optimizer_calls: OptimizerCallCache


def build_stores(
    projects_root: Path | str | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    datasets_root: Path | str | None = None,
) -> Stores:
    """Assemble a :class:`Stores` bundle rooted under a tenant.

    ``projects_root`` defaults to ``<repo_root>/.promptpotter/projects``.
    ``tenant_id`` defaults to ``"default"`` — the single-user CLI tenant.
    ``datasets_root`` defaults to ``<repo_root>/datasets`` — named dataset
    caches live there to survive ``.promptpotter/`` resets. Tests pass
    ``tmp_path`` to isolate from the real repo tree.
    """
    validate_path_component(tenant_id)
    root = Path(projects_root) if projects_root else DEFAULT_PROJECTS_ROOT
    tenant_dir = root / tenant_id
    ds_root = Path(datasets_root) if datasets_root else DEFAULT_DATASETS_ROOT
    return Stores(
        base_dir=tenant_dir,
        tenant_id=tenant_id,
        backends=BackendStore(tenant_dir, ds_root),
        sessions=SessionStore(tenant_dir),
        campaigns=CampaignStore(tenant_dir),
        archive=MeasurementArchive(tenant_dir),
        optimizer_calls=OptimizerCallCache(tenant_dir),
    )
