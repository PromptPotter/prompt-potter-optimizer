from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from promptpotter.config import paths
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.config.settings import settings
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, WorkspaceDir
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.command_log import CommandLog
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    FRAMING_FILE,
    ORIGIN_FILE,
    claim_own_definition,
    render_framing_file,
)
from promptpotter.infrastructure.store.diagnostic_run_store import DiagnosticRunStore
from promptpotter.infrastructure.store.draft_store import DraftStore
from promptpotter.infrastructure.store.io import validate_path_component, write_text, write_yaml
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
from promptpotter.infrastructure.store.wallet_store import (
    ChargeBook,
    GrantStore,
    TenantApiKeyStore,
)
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.identity import IdentityContext, TenantId


@dataclass(frozen=True)
class Stores:
    base_dir: WorkspaceDir
    projects_root: Path
    shared_root: Path
    install_datasets_root: Path
    # Resolved after the install, never listed; ``None`` where this deployment holds none.
    research_datasets_root: Path | None
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
    grant: GrantStore
    api_keys: TenantApiKeyStore
    charges: ChargeBook
    commands: CommandLog

    @property
    def tenant_id(self) -> TenantId:
        return self.identity.tenant_id

    @property
    def block_packs(self) -> PackShelf:
        """A shelf per read, parsed once: a write landing after it never meets a stale parse.
        Rooted at the account beside the caches: a sandbox isolates campaign STATE, and a block
        pack an L4 cell reads is not state."""
        return PackShelf(
            account=tenant_workspace(self.shared_root, self.tenant_id) / paths.BLOCK_PACKS_DIR
        )

    @property
    def jobs_dir(self) -> Path:
        return paths.jobs_dir(self.shared_root)

    @property
    def inner(self) -> bool:
        """An L4 inner run's sandbox: every cycle under it answers every run verb for itself."""
        return in_inner_sandbox(self.projects_root)

    def write_own_definition(
        self,
        slug: str,
        *,
        source: dict[str, Any],
        pipeline: dict[str, Any],
        campaign: dict[str, Any],
        description: str,
        framing: TaskDecomposition,
        origin: dict[str, dict[str, Any]],
    ) -> Path:
        """THE writer of an account's definition, here since only the stores see every tier its
        name is claimed over. WHOLE files: the tenant tier lays no defaults or family under it."""
        claim_own_definition(self, slug, new=False)
        dst = self.tenant_datasets.dataset_dir(slug)
        write_text(dst / FRAMING_FILE, render_framing_file(description, framing))
        if origin:
            write_yaml(dst / ORIGIN_FILE, origin)
        # Last: the definition file is what makes the directory a dataset.
        definition = {"source": source, "pipeline": pipeline, "campaign": campaign}
        write_yaml(dst / DEFINITION_FILE, definition)
        return dst


def owned_campaign(stores: Stores, campaign_id: str) -> Campaign:
    """Another user's campaign is NOT FOUND, never forbidden: existence leakage is a violation."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None or not caller_owns(stores, campaign):
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    return campaign


def caller_owns(stores: Stores, campaign: Campaign) -> bool:
    return campaign.owner_user_id == str(stores.identity.user_id)


def build_stores(
    identity: IdentityContext,
    *,
    projects_root: Path,
    install_datasets_root: Path | None = None,
    research_datasets_root: Path | None | Literal["checkout"] = "checkout",
    shared_root: Path | None = None,
) -> Stores:
    """``projects_root`` takes no default: one would address the real workspace from a sandbox.
    The research root is the checkout's where a fresh build asks for it (``checkout``); a
    carried one, ``None`` included, is kept as carried."""
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
        research_datasets_root=(
            paths.research_datasets_root()
            if research_datasets_root == "checkout"
            else research_datasets_root
        ),
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
        # The account's, never a sandbox's: an L4 inner run spends from the wallet of its owner.
        grant=GrantStore(account),
        api_keys=TenantApiKeyStore(account),
        charges=ChargeBook(account),
        # The account's too: a sandbox's commands are filed under their full subject.
        commands=CommandLog(account),
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
    return build_stores(
        stores.identity,
        projects_root=sandbox_root,
        install_datasets_root=stores.install_datasets_root,
        research_datasets_root=stores.research_datasets_root,
        shared_root=stores.shared_root,
    )


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
