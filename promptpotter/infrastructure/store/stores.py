from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from promptpotter.config.paths import benchmark_datasets_root
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, WorkspaceDir
from promptpotter.infrastructure.store.backend_store import BackendStore
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.checkin_draft_store import CheckinDraftStore
from promptpotter.infrastructure.store.dataset_replace import heal_pending_replacements
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.layout import (
    JUDGE_REUSE_DIR,
    OPTIMIZER_REUSE_DIR,
    inner_sandbox_dir,
    tenant_workspace,
)
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore
from promptpotter.infrastructure.store.user_store import UserStore
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.identity import IdentityContext, TenantId

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Stores:
    base_dir: WorkspaceDir
    projects_root: Path
    shared_root: Path
    benchmarks_root: Path
    identity: IdentityContext
    backends: BackendStore
    tenant_datasets: TenantDatasetStore
    campaigns: CampaignStore
    checkin: CheckinDraftStore
    archive: MeasurementArchive
    optimizer_reuse: LLMReuseCache
    judge_reuse: LLMReuseCache
    diagnostic_runs: DiagnosticRunStore
    users: UserStore

    @property
    def tenant_id(self) -> TenantId:
        return self.identity.tenant_id


def owned_campaign(stores: Stores, campaign_id: str) -> Campaign:
    """Another user's campaign is NOT FOUND, never forbidden: existence leakage is a violation."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return campaign


def build_stores(
    identity: IdentityContext,
    *,
    projects_root: Path,
    benchmarks_root: Path | None = None,
    shared_root: Path | None = None,
) -> Stores:
    """``projects_root`` takes no default: one would address the real workspace from a sandbox."""
    root = projects_root
    tenant_dir = tenant_workspace(root, identity.tenant_id)
    shared = shared_root if shared_root is not None else root
    shared_tenant = shared / identity.tenant_id
    bench_root = benchmarks_root if benchmarks_root is not None else benchmark_datasets_root()
    stores = Stores(
        base_dir=tenant_dir,
        projects_root=root,
        shared_root=shared,
        benchmarks_root=bench_root,
        identity=identity,
        backends=BackendStore(tenant_dir),
        # Rooted with the caches: a sandbox isolates campaign STATE, and a dataset is not state.
        tenant_datasets=TenantDatasetStore(tenant_workspace(shared, identity.tenant_id)),
        campaigns=CampaignStore(tenant_dir),
        checkin=CheckinDraftStore(tenant_dir),
        archive=MeasurementArchive.at(shared_tenant),
        optimizer_reuse=LLMReuseCache(shared_tenant, OPTIMIZER_REUSE_DIR),
        judge_reuse=LLMReuseCache(shared_tenant, JUDGE_REUSE_DIR),
        diagnostic_runs=DiagnosticRunStore(tenant_dir),
        users=UserStore(tenant_dir),
    )
    # A sandbox shares the dataset tier but holds no campaigns: healing there half-applies.
    if shared == root:
        heal_pending_replacements(stores)
    return stores


def inner_sandbox_store(
    stores: Stores, outer_campaign_id: str, outer_cycle_id: str
) -> Stores | None:
    """Anchored on ``shared_root``, invariant across depth, exactly as the sandbox's writer is."""
    sandbox_root = inner_sandbox_dir(
        stores.shared_root,
        str(stores.tenant_id),
        CycleHop(campaign_id=outer_campaign_id, cycle_id=outer_cycle_id),
    )
    if not (sandbox_root / stores.tenant_id).is_dir():
        return None
    return build_stores(stores.identity, projects_root=sandbox_root, shared_root=stores.shared_root)


def descend_store(stores: Stores, hops: CyclePath) -> Stores:
    cur = stores
    for hop in hops:
        try:
            validate_path_component(hop.campaign_id)
            validate_path_component(hop.cycle_id)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        nxt = inner_sandbox_store(cur, hop.campaign_id, hop.cycle_id)
        if nxt is None:
            raise NotFoundError(f"No inner sandbox for cycle '{hop.cycle_id}'")
        cur = nxt
    return cur


def resolve_cycle_path(stores: Stores, path: CyclePath) -> tuple[Stores, CycleHop]:
    if not path:
        raise BadRequestError("empty cycle path")
    leaf = path[-1]
    try:
        validate_path_component(leaf.campaign_id)
        validate_path_component(leaf.cycle_id)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return descend_store(stores, path[:-1]), leaf


__all__ = [
    "Stores",
    "build_stores",
    "descend_store",
    "inner_sandbox_store",
    "owned_campaign",
    "resolve_cycle_path",
]
