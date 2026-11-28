from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from promptpotter.config import paths
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_paths import (
    CycleHop,
    CyclePath,
    Subject,
    WorkspaceDir,
    validate_path_component,
)
from promptpotter.domain.phases import ProducerState, RunState, StopReason
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.lineage import CycleSupersededRecord
from promptpotter.domain.records.run_phase import RunPhaseRecord
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.run_state import derive_run_state
from promptpotter.infrastructure.store.campaign_store.ledger_scan import CycleFactsFold
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.command_log import CommandLog, Controls
from promptpotter.infrastructure.store.draft_store import DraftStore
from promptpotter.infrastructure.store.io import WORKSPACE_STATE_DIR, refuse_off_workspace, stamp_of
from promptpotter.infrastructure.store.layout import (
    ArchiveHome,
    archive_home,
    campaign_root_dir_for,
    campaigns_root_dir_for,
    drafts_root_dir_for,
    inner_sandbox_dir,
    tenant_workspace,
)
from promptpotter.infrastructure.store.llm_reuse_cache import LLMReuseCache
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive
from promptpotter.infrastructure.store.read_model import LedgerIndex
from promptpotter.infrastructure.store.ruler_store import RulerStore
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore
from promptpotter.infrastructure.store.user_store import UserStore
from promptpotter.infrastructure.store.wallet_store import (
    ChargeBook,
    GrantStore,
    TenantApiKeyStore,
)
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.identity import IdentityContext, TenantId, default_identity


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
    users: UserStore
    grant: GrantStore
    api_keys: TenantApiKeyStore
    charges: ChargeBook
    # The account's, shared by every sandbox under it: what was asked, of every subject.
    commands: CommandLog
    # The L4 sandbox chain this store sits in, outermost first: ``()`` is the account's own.
    inside: CyclePath = ()

    @property
    def tenant_id(self) -> TenantId:
        return self.identity.tenant_id

    @property
    def account_dir(self) -> WorkspaceDir:
        """The account's own tree, a sandbox's owner's: its ruler, datasets, charges and filings."""
        return tenant_workspace(self.shared_root, self.tenant_id)

    @property
    def account_state_dir(self) -> Path:
        return self.account_dir / WORKSPACE_STATE_DIR

    @property
    def account_drafts_dir(self) -> Path:
        return drafts_root_dir_for(self.account_dir)

    def stamps(self, campaign_ids: Iterable[str] | None = None) -> tuple[object, ...]:
        """What this store's writers stamped: its campaigns and ledgers (*campaign_ids*' alone where
        named), and the account's ruler, datasets and charges and the archive home it reads
        (`io.py::stamp`); never a walk."""
        campaigns = (
            [campaigns_root_dir_for(self.base_dir)]
            if campaign_ids is None
            else [campaign_root_dir_for(self.base_dir, c) for c in sorted(set(campaign_ids))]
        )
        roots = dict.fromkeys((*campaigns, self.base_dir, self.account_dir, self.archive_home.root))
        return tuple(stamp_of(root) for root in roots)

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
        return bool(self.inside)

    def subject(self, hop: CycleHop) -> Subject:
        return Subject.of((*self.inside, hop))

    def inbox(self, hop: CycleHop) -> Controls:
        return self.commands.book().controls(self.subject(hop))

    def run_state(self, hop: CycleHop) -> RunState:
        state = derive_run_state(self.campaigns.cycle_dir(hop), self.inbox(hop), self.jobs_dir)
        return state._replace(inner=self.inner, runs_on=self.campaigns.successor(hop))

    def void_inbox(self, hop: CycleHop, *, sparing: str | None = None) -> None:
        """A run boundary: what the cycle's loop was asked and never took stands no more, less
        what stands for the hold *sparing* names (a launch's own)."""
        detail = "run boundary" if sparing is None else "the launch"
        self.commands.void(self.subject(hop), f"{detail} of {hop.cycle_id}", sparing=sparing)

    def declare_stop(
        self, hop: CycleHop, stop: RunPhaseRecord, *, error: ErrorRecord | None = None
    ) -> None:
        """THE stop of a cycle, its run's own included: its ending, then its run boundary."""
        # Error first, so no reader finds the ending without it.
        self.campaigns.append(hop, *([] if error is None else [error]), stop)
        self.void_inbox(hop)

    def declare_ended(self, hop: CycleHop, reason: StopReason) -> bool:
        """Ends a minted cycle no producer holds on *reason*, where it has not ended; a vanished
        producer's only where its run is still owed, read under the lock (a pause since stands)."""
        layout = self.campaigns.layout(hop)
        lock = layout.producer_lock

        def ends(*, own_hold: bool) -> bool:
            facts = LedgerIndex.of(layout.ledger).view(CycleFactsFold)
            if facts.minted is None or facts.ended is not None:
                return False
            if reason is not StopReason.PRODUCER_VANISHED:
                return True
            run = derive_run_state(layout.cycle_dir, Controls(), self.jobs_dir, own_hold=own_hold)
            return run.producer.state is ProducerState.SILENT

        # A held cycle's producer declares its own stop; asked first, so no caller waits out a live
        # hold, and the whole scan is read before the lock, so a resume never waits it out.
        if producer_lock.held(lock) or not ends(own_hold=False) or not producer_lock.take(lock):
            return False
        try:
            # Asked again off the index the first read built: only the lines since are parsed.
            if not ends(own_hold=True):
                return False
            self.declare_stop(hop, RunPhaseRecord.stop(reason))
            return True
        finally:
            producer_lock.release(lock)

    def mark_superseded(self, hop: CycleHop, successor_cycle_id: str) -> bool:
        # The relation always lands; the ending only where none stands, or it replaces why it ended.
        self.campaigns.append(hop, CycleSupersededRecord(successor_cycle_id=successor_cycle_id))
        return self.declare_ended(hop, StopReason.REBASED)

    def mark_producer_vanished(self, hop: CycleHop) -> bool:
        """A cycle whose producer went with its run owed (`ProducerState.SILENT`) ends
        PRODUCER_VANISHED, at its run boundary."""
        return self.run_state(hop).producer.state is ProducerState.SILENT and self.declare_ended(
            hop, StopReason.PRODUCER_VANISHED
        )


def build_stores(
    identity: IdentityContext,
    *,
    projects_root: Path,
    install_datasets_root: Path | None = None,
    research_datasets_root: Path | None | Literal["checkout"] = "checkout",
    shared_root: Path | None = None,
) -> Stores:
    """``projects_root`` takes no default (one would address the real workspace from a sandbox), and
    a workspace io places no write in is refused (`io.py::refuse_off_workspace`). A fresh build
    asks the checkout's research root (``checkout``); a carried one, ``None`` too, is kept."""
    refuse_off_workspace(tenant_dir := tenant_workspace(projects_root, identity.tenant_id))
    shared = shared_root if shared_root is not None else projects_root
    home = archive_home(identity, settings.SHARED_ARCHIVE, shared_root=shared)
    account = tenant_workspace(shared, identity.tenant_id)
    return Stores(
        base_dir=tenant_dir,
        projects_root=projects_root,
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
        campaigns=CampaignStore(tenant_dir, paths.jobs_dir(shared)),
        drafts=DraftStore(tenant_dir),
        archive=MeasurementArchive.at(home.root),
        optimizer_reuse=LLMReuseCache(home.root, paths.OPTIMIZER_REUSE_DIR),
        judge_reuse=LLMReuseCache(home.root, paths.JUDGE_REUSE_DIR),
        users=UserStore(tenant_dir),
        # The account's, never a sandbox's: an L4 inner run spends from the wallet of its owner.
        grant=GrantStore(account),
        api_keys=TenantApiKeyStore(account),
        charges=ChargeBook(account),
        commands=CommandLog(account, paths.jobs_dir(shared)),
    )


def tenant_stores(projects_root: Path, workspace: WorkspaceDir) -> Stores:
    """A tenant dir's Stores, as the CLI's `--tenant` builds them: no `user.json` asked."""
    return build_stores(default_identity(tenant_id=workspace.name), projects_root=projects_root)


def sandbox_store(stores: Stores, outer: CycleHop) -> Stores:
    """THE build of *outer*'s L4 sandbox under *stores*: anchored on ``shared_root``, invariant
    across depth. Only campaign STATE is sandboxed; the caches stay on the real tenant tree."""
    built = build_stores(
        stores.identity,
        projects_root=inner_sandbox_dir(stores.shared_root, str(stores.tenant_id), outer),
        install_datasets_root=stores.install_datasets_root,
        research_datasets_root=stores.research_datasets_root,
        shared_root=stores.shared_root,
    )
    return replace(built, inside=(*stores.inside, outer))


def inner_sandbox_store(stores: Stores, outer: CycleHop) -> Stores | None:
    """A reader's: the key is flat, so the outer hop must be a cycle of *stores*, else a chain
    splices in any sandbox; ``None`` where none was written."""
    if not stores.campaigns.cycle_dir(outer).is_dir():
        return None
    sandbox_root = inner_sandbox_dir(stores.shared_root, str(stores.tenant_id), outer)
    if not (sandbox_root / stores.tenant_id).is_dir():
        return None
    return sandbox_store(stores, outer)


def descend_store(stores: Stores, hops: CyclePath) -> Stores:
    cur = stores
    for hop in hops:
        try:
            validate_path_component(hop.campaign_id)
            validate_path_component(hop.cycle_id)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        nxt = inner_sandbox_store(cur, hop)
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
