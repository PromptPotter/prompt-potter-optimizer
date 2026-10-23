from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.config import paths
from promptpotter.config.settings import settings
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, WorkspaceDir
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.draft_store import DraftStore
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.layout import (
    ArchiveHome,
    archive_home,
    in_inner_sandbox,
    inner_sandbox_dir,
    tenant_workspace,
)
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.ruler_store import RulerStore
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
    install_datasets_root: Path
    identity: IdentityContext
    archive_home: ArchiveHome
    tenant_datasets: TenantDatasetStore
    ruler: RulerStore
    campaigns: CampaignStore
    drafts: DraftStore
    archive: MeasurementArchive
    optimizer_reuse: LLMReuseCache
    judge_reuse: LLMReuseCache
    diagnostic_runs: DiagnosticRunStore
    users: UserStore

    @property
    def tenant_id(self) -> TenantId:
        return self.identity.tenant_id

    @property
    def inner(self) -> bool:
        """An L4 inner run's sandbox: every cycle under it answers every run verb for itself."""
        return in_inner_sandbox(self.projects_root)


def owned_campaign(stores: Stores, campaign_id: str) -> Campaign:
    """Another user's campaign is NOT FOUND, never forbidden: existence leakage is a violation."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return campaign


def owned_draft_data(stores: Stores, draft_id: str) -> dict[str, Any]:
    """Asked before a draft's claim is taken: a claim another user holds answers as their draft does."""
    data = stores.drafts.read_draft(draft_id)
    if data is None or data["owner_user_id"] != str(stores.identity.user_id):
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return data


def build_stores(
    identity: IdentityContext,
    *,
    projects_root: Path,
    install_datasets_root: Path | None = None,
    shared_root: Path | None = None,
) -> Stores:
    """``projects_root`` takes no default: one would address the real workspace from a sandbox."""
    root = projects_root
    tenant_dir = tenant_workspace(root, identity.tenant_id)
    shared = shared_root if shared_root is not None else root
    home = archive_home(identity, settings.SHARED_ARCHIVE, shared_root=shared)
    account = tenant_workspace(shared, identity.tenant_id)
    return Stores(
        base_dir=tenant_dir,
        projects_root=root,
        shared_root=shared,
        install_datasets_root=install_datasets_root or paths.install_datasets_root(),
        identity=identity,
        archive_home=home,
        # Rooted at the account beside the caches: a sandbox isolates campaign STATE, and a
        # dataset or the ruler an L4 cell reads is not state.
        tenant_datasets=TenantDatasetStore(account),
        ruler=RulerStore(account),
        campaigns=CampaignStore(tenant_dir),
        drafts=DraftStore(tenant_dir),
        archive=MeasurementArchive.at(home.root),
        optimizer_reuse=LLMReuseCache(home.root, paths.OPTIMIZER_REUSE_DIR),
        judge_reuse=LLMReuseCache(home.root, paths.JUDGE_REUSE_DIR),
        diagnostic_runs=DiagnosticRunStore(tenant_dir),
        users=UserStore(tenant_dir),
    )


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
