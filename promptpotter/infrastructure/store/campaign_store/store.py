from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Unpack

from promptpotter.domain.bench import BenchScore, PartitionRecord
from promptpotter.domain.campaign import (
    Campaign,
    CampaignEdit,
    CampaignResult,
    HeadToHeadRecord,
    LifecycleFilter,
    LifecycleStatus,
)
from promptpotter.domain.cycle_listing import CycleIndex, CycleListEntry, RunStatus
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.export import PromptExport, parse_prompt_export
from promptpotter.domain.launch_limits import RoundsCap
from promptpotter.domain.phases import ErrorRecord, LaunchStage, ProducerState, StopReason
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import DeltaRuler
from promptpotter.domain.run_records import (
    CheckinClosedRecord,
    CycleFinal,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleRecord,
    CycleSeed,
    CycleSeedRecord,
    CycleSupersededRecord,
    ForkDirection,
    ForkGradedRecord,
    ForkSpec,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchReleasedRecord,
    OptimizerStateRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RulerRecord,
    RunLimitsRecord,
    RunPhaseRecord,
    SpawnedBy,
    SpawnedRecord,
)
from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.value_tree import ValueLeaf
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.ledger import CycleEventLog, continued_chain, ledger_chain
from promptpotter.infrastructure.projections.cycle_index import (
    read_cycle_index,
    write_cycle_index,
)
from promptpotter.infrastructure.projections.live_dashboard.projection import materializing_over
from promptpotter.infrastructure.runtime_flags import derive_run_state, is_checkin
from promptpotter.infrastructure.store.account_spend import bank_spend, sandbox_cycle_dirs
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    StandingRounds,
    scan_cycle_facts,
    scan_launch_claim,
    scan_ledger_cycle_seed,
    scan_ledger_ruler,
    scan_ledger_run_limits,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.io import (
    iter_files,
    read_json,
    read_json_optional,
    read_text_optional,
    read_yaml_optional,
    rmtree_robust,
    unlink_robust,
    write_json,
    write_text,
    write_yaml,
)
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    campaign_cycles_dir,
    campaign_root_dir_for,
    campaigns_root_dir_for,
    classify,
    cycle_dir_for,
    head_to_head_path,
    inner_sandbox_key,
    root_cycle_id,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment
from promptpotter.infrastructure.store.session_pointer import (
    clear_active_pointer,
    read_active_pointer,
)
from promptpotter.shared.errors import BadRequestError, ConflictError, NotFoundError, graceful

logger = logging.getLogger(__name__)


def _prune_empty_dirs(root: Path) -> None:
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        with contextlib.suppress(OSError):
            d.rmdir()


def _strip_to_keepsake(campaign_dir: Path) -> None:
    cycles_dir = campaign_cycles_dir(campaign_dir)
    if cycles_dir.is_dir():
        for cdir in cycles_dir.iterdir():
            if not cdir.is_dir():
                continue
            for p in [
                f
                for f, _st in iter_files(cdir)
                if not classify(f.relative_to(campaign_dir).parts).keepsake
            ]:
                unlink_robust(p)
            _prune_empty_dirs(cdir)


class CampaignStore:
    def __init__(self, base_dir: WorkspaceDir):
        self._base_dir = base_dir

    @property
    def workspace(self) -> WorkspaceDir:
        return self._base_dir

    def campaign_root_dir(self, campaign_id: str) -> Path:
        return campaign_root_dir_for(self._base_dir, campaign_id)

    def cycle_dir(self, hop: CycleHop) -> Path:
        return cycle_dir_for(self._base_dir, hop)

    def _campaign_layout(self, campaign_id: str) -> CampaignLayout:
        return CampaignLayout(self.campaign_root_dir(campaign_id))

    def _manifest_path(self, campaign_id: str) -> Path:
        return self._campaign_layout(campaign_id).manifest

    def _layout(self, hop: CycleHop) -> CycleLayout:
        return CycleLayout(self.cycle_dir(hop))

    def _append(self, hop: CycleHop, record: CycleRecord) -> None:
        cycle_dir = self.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(record)
        write_cycle_index(cycle_dir)

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        data = read_json_optional(self._manifest_path(campaign_id))
        if data is None:
            return None
        return Campaign.model_validate(data)

    def _campaigns_root(self) -> Path:
        return campaigns_root_dir_for(self._base_dir)

    def _cycle_dirs(self) -> list[Path]:
        root = self._campaigns_root()
        return sorted(p for p in root.glob("*/cycles/*") if p.is_dir()) if root.exists() else []

    def iter_campaign_dirs(self) -> list[Path]:
        """Archived included: archiving must not free daily spend-cap budget."""
        root = self._campaigns_root()
        if not root.exists():
            return []
        return sorted(p for p in root.iterdir() if CampaignLayout(p).manifest.is_file())

    def campaign_cycle_dirs(self, campaign_id: str) -> list[Path]:
        cycles = campaign_cycles_dir(self.campaign_root_dir(campaign_id))
        return sorted(p for p in cycles.glob("*") if p.is_dir())

    def campaign_cycle_ledgers(self, campaign_id: str) -> list[Path]:
        return [
            ledger
            for cycle_dir in self.campaign_cycle_dirs(campaign_id)
            if (ledger := CycleLayout(cycle_dir).ledger).is_file()
        ]

    def iter_cycle_ledgers(self) -> list[Path]:
        return [
            ledger
            for campaign_dir in self.iter_campaign_dirs()
            for ledger in self.campaign_cycle_ledgers(campaign_dir.name)
        ]

    def create_campaign(self, campaign: Campaign) -> Path:
        path = self._manifest_path(campaign.campaign_id)
        write_json(path, campaign.model_dump(mode="json"))
        return path

    def load_result(self, campaign_id: str) -> CampaignResult | None:
        data = read_json_optional(self._campaign_layout(campaign_id).result)
        return None if data is None else CampaignResult.model_validate(data)

    def write_result(self, campaign_id: str, result: CampaignResult) -> None:
        write_json(self._campaign_layout(campaign_id).result, result.model_dump(mode="json"))

    def load_head_to_head(self, head_to_head_id: str) -> HeadToHeadRecord | None:
        data = read_json_optional(head_to_head_path(self._base_dir, head_to_head_id))
        return None if data is None else HeadToHeadRecord.model_validate(data)

    def declare_head_to_head(self, record: HeadToHeadRecord) -> None:
        write_json(
            head_to_head_path(self._base_dir, record.head_to_head_id),
            record.model_dump(mode="json"),
        )

    def update_campaign(self, campaign_id: str, **changes: Unpack[CampaignEdit]) -> None:
        manifest = Campaign.model_validate(read_json(self._manifest_path(campaign_id)))
        self.create_campaign(manifest.edited(**changes))

    def repoint_dataset(self, old_name: str, new_name: str) -> int:
        count = 0
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None or campaign.dataset_name != old_name:
                continue
            self.update_campaign(cid, dataset_name=new_name)
            count += 1
        return count

    def list_campaign_ids(self) -> list[str]:
        return sorted(p.name for p in self.iter_campaign_dirs())

    @staticmethod
    def _ids_matching(ids: list[str], needle: str) -> list[str]:
        # Exact first: a root cycle's id is a prefix of every fork and diag of it.
        if needle in ids:
            return [needle]
        matches = [i for i in ids if i.endswith(f"__{needle}") or i.startswith(needle)]
        if needle and not matches:
            matches = [i for i in ids if needle in i]
        return matches

    def match_campaign_ids(self, needle: str) -> list[str]:
        return self._ids_matching(self.list_campaign_ids(), needle)

    def match_cycle_ids(self, campaign_id: str, needle: str) -> list[str]:
        ids = [p.name for p in self.campaign_cycle_dirs(campaign_id)]
        return self._ids_matching(ids, needle)

    def list_campaigns(
        self,
        dataset_name: str | None = None,
        *,
        lifecycle: LifecycleFilter = "active",
        owner_user_id: str | None = None,
    ) -> list[Campaign]:
        out: list[Campaign] = []
        for cid in self.list_campaign_ids():
            campaign = self.load_campaign(cid)
            if campaign is None:
                continue
            if dataset_name and campaign.dataset_name != dataset_name:
                continue
            if lifecycle == "checkin":
                if not is_checkin(self.cycle_dir(campaign.root_hop)):
                    continue
            elif lifecycle != "all" and campaign.lifecycle_status != lifecycle:
                continue
            if owner_user_id is not None and campaign.owner_user_id != owner_user_id:
                continue
            out.append(campaign)
        return out

    def attached_cycle_ids(self, campaign_id: str) -> list[str]:
        cycles_dir = campaign_cycles_dir(self.campaign_root_dir(campaign_id))
        if not cycles_dir.is_dir():
            return []
        attached: list[str] = []
        for cdir in sorted(p for p in cycles_dir.iterdir() if p.is_dir()):
            if derive_run_state(cdir).producer.attached:
                attached.append(cdir.name)
        return attached

    def _guard_and_release(self, campaign_id: str, verb: str) -> None:
        if live := self.attached_cycle_ids(campaign_id):
            raise ConflictError(
                f"refusing to {verb} {campaign_id}: cycle {live[0]} has a live producer "
                "— pause or stop it first"
            )
        active_campaign, _ = read_active_pointer(self._base_dir)
        if active_campaign == campaign_id:
            clear_active_pointer(self._base_dir)

    def _set_lifecycle(
        self, campaign_id: str, status: LifecycleStatus, changed_at: str, reason: str
    ) -> None:
        self.update_campaign(
            campaign_id,
            lifecycle_status=status,
            lifecycle_changed_at=changed_at,
            lifecycle_reason=reason,
        )

    def archive_campaign(self, campaign_id: str, *, changed_at: str, reason: str = "") -> bool:
        if self.load_campaign(campaign_id) is None:
            return False
        self._guard_and_release(campaign_id, "archive")
        self._set_lifecycle(campaign_id, "archived", changed_at, reason)
        return True

    def unarchive_campaign(self, campaign_id: str, *, changed_at: str, reason: str = "") -> bool:
        campaign = self.load_campaign(campaign_id)
        # Never a `deleted` one: its spend is already banked, so what it spent next reaches no ledger.
        if campaign is None or campaign.lifecycle_status != "archived":
            return False
        self._set_lifecycle(campaign_id, "active", changed_at, reason)
        return True

    def bank_all_before_removal(self) -> None:
        """Removal only: banking a subject that keeps its rows counts the money twice."""
        for campaign_dir in self.iter_campaign_dirs():
            bank_spend(
                workspace=self._base_dir,
                cycle_dirs=self.campaign_cycle_dirs(campaign_dir.name),
                campaign_id=campaign_dir.name,
            )

    def delete_campaign(
        self,
        campaign_id: str,
        *,
        keep_results: bool,
        changed_at: str,
        reason: str = "",
        inner_sandbox_root: Path | None = None,
    ) -> bool:
        campaign_dir = self.campaign_root_dir(campaign_id)
        if not CampaignLayout(campaign_dir).manifest.is_file():
            return False
        self._guard_and_release(campaign_id, "delete")
        # After the guard: a tombstone beside a refused delete's rows counts the money twice.
        bank_spend(
            workspace=self._base_dir,
            cycle_dirs=self.campaign_cycle_dirs(campaign_id),
            campaign_id=campaign_id,
        )
        # Before the tree goes: the off-tree inner sandboxes are keyed by these ids.
        inner_cycle_ids: list[str] = []
        if inner_sandbox_root is not None:
            cycles_dir = campaign_cycles_dir(campaign_dir)
            if cycles_dir.is_dir():
                inner_cycle_ids = [p.name for p in cycles_dir.iterdir() if p.is_dir()]
        if keep_results:
            self._set_lifecycle(campaign_id, "deleted", changed_at, reason)
            _strip_to_keepsake(campaign_dir)
        else:
            rmtree_robust(campaign_dir)
        if inner_sandbox_root is not None:
            tenant_id = self._base_dir.name
            for cycle_id in inner_cycle_ids:
                inner_dir = inner_sandbox_root / inner_sandbox_key(
                    tenant_id, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
                )
                self.delete_inner_sandbox(inner_dir, campaign_id=campaign_id)
        return True

    def delete_inner_sandbox(self, sandbox: Path, *, campaign_id: str) -> None:
        if not sandbox.exists():
            return
        # Keyed on the sandbox directory: inner cycle ids are content-addressed and repeat across them.
        bank_spend(
            workspace=self._base_dir,
            cycle_dirs=sandbox_cycle_dirs(sandbox),
            campaign_id=campaign_id,
            cycle_id=sandbox.name,
        )
        rmtree_robust(sandbox)

    def load(self, hop: CycleHop) -> CycleIndex | None:
        return read_cycle_index(self.cycle_dir(hop))

    def mint_cycle(self, hop: CycleHop, *, checkin: bool = False) -> None:
        if scan_cycle_facts(self._layout(hop).ledger).minted is None:
            self._append(hop, CycleMintedRecord(checkin=checkin))

    def close_checkin(self, hop: CycleHop) -> None:
        if scan_cycle_facts(self._layout(hop).ledger).checkin:
            self._append(hop, CheckinClosedRecord())

    def mint_fork_cycle(
        self, parent: CycleHop, new_cycle_id: str, spec: ForkSpec, *, from_round: int
    ) -> CycleHop:
        child = CycleHop(campaign_id=parent.campaign_id, cycle_id=new_cycle_id)
        # Read at the mint: the only moment the offset is true of a parent still running.
        cut = CycleEventLog.open(CycleDir(self.cycle_dir(parent))).next_offset
        self._append(
            child,
            CycleMintedRecord(
                parent_cycle_id=parent.cycle_id,
                forked_at_offset=cut,
                fork=spec.model_copy(update={"seed": None}),
            ),
        )
        self._append(child, RoundEnteredRecord(round=from_round, rewound=True))
        return child

    def grade_fork(self, hop: CycleHop, direction: ForkDirection) -> None:
        self._append(hop, ForkGradedRecord(direction=direction))

    def record_intervention(self, hop: CycleHop, *, kind: str) -> None:
        self._append(hop, InterventionRecord(kind=kind))

    def record_spawned(self, hop: CycleHop, spawned_by: SpawnedBy) -> None:
        self._append(hop, SpawnedRecord(spawned_by=spawned_by))

    def standing_rounds(self, hop: CycleHop, moment: Moment | None = None) -> StandingRounds:
        return scan_standing_rounds(ledger_chain(CycleDir(self.cycle_dir(hop)), moment))

    def round_proposals(self, hop: CycleHop, round_num: int) -> RoundProposedRecord | None:
        return self.standing_rounds(hop).proposals.get(round_num)

    def restate_optimizer_state(self, hop: CycleHop, rr: RoundResult) -> None:
        CycleEventLog.open(CycleDir(self.cycle_dir(hop))).append(
            OptimizerStateRecord(round=rr.round, optimizer_state=rr.optimizer_state)
        )

    def rewind_to_round(
        self,
        hop: CycleHop,
        after_round: int,
    ) -> None:
        layout = self._layout(hop)
        if not layout.ledger.exists():
            raise NotFoundError(f"cycle {hop.cycle_id!r} has no ledger on disk")
        max_complete = max(self.standing_rounds(hop).rounds, default=-1)
        if after_round > max_complete:
            raise BadRequestError(
                f"--from {after_round}: ledger only has completed rounds 0..{max_complete}"
            )
        self._append(hop, RoundEnteredRecord(round=after_round + 1, rewound=True))

    def bank_final(
        self,
        hop: CycleHop,
        final: CycleFinal,
        *,
        interrupted_round: int | None = None,
        export: PromptExport | None = None,
    ) -> None:
        with graceful("Cycle final append failed"):
            self._append(hop, CycleFinalRecord(final=final, interrupted_round=interrupted_round))
        if export is not None:
            with graceful("Export artifact write failed"):
                self._write_export(hop, export)

    def _write_export(self, hop: CycleHop, export: PromptExport) -> None:
        write_text(self._layout(hop).export, export.model_dump_json(indent=2) + "\n")

    def restate_export_bench(self, hop: CycleHop, bench: BenchScore) -> None:
        export = self.read_export(hop)
        if export is not None:
            self._write_export(hop, export.model_copy(update={"bench": bench}))

    def read_export(self, hop: CycleHop) -> PromptExport | None:
        text = read_text_optional(self._layout(hop).export)
        return parse_prompt_export(text) if text else None

    def mark_superseded(self, hop: CycleHop, successor_cycle_id: str) -> None:
        # The relation always lands; the ending only where none stands, or it replaces why it ended.
        with graceful("Supersede relation append failed"):
            self._append(hop, CycleSupersededRecord(successor_cycle_id=successor_cycle_id))
        self._declare_ended(hop, StopReason.REBASED)

    def line(self, hop: CycleHop) -> list[CycleHop]:
        out = [hop]
        while successor := scan_cycle_facts(self._layout(hop).ledger).superseded_by:
            hop = CycleHop(campaign_id=hop.campaign_id, cycle_id=successor)
            out.append(hop)
        return out

    def line_holder(self, hop: CycleHop) -> CycleHop:
        return self.line(hop)[-1]

    def declare_stop(
        self, hop: CycleHop, stop: RunPhaseRecord, *, error: ErrorRecord | None = None
    ) -> None:
        cycle_dir = CycleDir(self.cycle_dir(hop))
        dashboard = materializing_over(cycle_dir, hop)
        ledger = CycleEventLog.open(cycle_dir)
        if dashboard is not None:
            ledger.bind(dashboard)
        # Error first, so no reader finds the ending without it.
        if error is not None:
            ledger.append(error)
        ledger.append(stop)
        if dashboard is not None:
            dashboard.drain()
        write_cycle_index(cycle_dir)

    def claim_launch(
        self,
        hop: CycleHop,
        *,
        stage: LaunchStage,
        job_id: str,
        claimant_lock: Path,
    ) -> None:
        self._append(
            hop,
            LaunchClaimRecord(stage=stage, job_id=job_id, claimant_lock=str(claimant_lock)),
        )

    def launch_claim(self, hop: CycleHop) -> LaunchClaimRecord | None:
        return scan_launch_claim(self._layout(hop).ledger)

    def release_claim(self, hop: CycleHop, *, job_id: str, detail: str) -> None:
        claim = self.launch_claim(hop)
        if claim is not None and claim.job_id == job_id:
            self._append(hop, LaunchReleasedRecord(job_id=job_id, detail=detail))

    def _declare_ended(self, hop: CycleHop, reason: StopReason) -> bool:
        facts = scan_cycle_facts(self._layout(hop).ledger)
        if facts.minted is None or facts.ended is not None:
            return False
        self.declare_stop(hop, RunPhaseRecord.stop(reason))
        return True

    def mark_producer_vanished(self, hop: CycleHop) -> bool:
        cycle_dir = self.cycle_dir(hop)
        if derive_run_state(cycle_dir).producer.state is not ProducerState.SILENT:
            return False
        lock = CycleLayout(cycle_dir).producer_lock
        # Declared holding the cycle, so a launch taking it up is never ended under itself.
        if not producer_lock.take(lock):
            return False
        try:
            return self._declare_ended(hop, StopReason.PRODUCER_VANISHED)
        finally:
            producer_lock.release(lock)

    def _list_entry(self, cycle_dir: Path, index: CycleIndex) -> CycleListEntry:
        campaign_id, cycle_id = cycle_dir.parent.parent.name, cycle_dir.name
        kind = sibling_kind(cycle_id)
        run = derive_run_state(cycle_dir)
        campaign = self.load_campaign(campaign_id)
        return CycleListEntry(
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            parent_cycle_id=index.parent_cycle_id,
            dataset_name="" if campaign is None else campaign.dataset_name,
            backend_id="" if campaign is None else campaign.backend_id,
            mint_kind=index.mint_kind,
            is_root=kind == "root",
            stop_reason=index.stop_reason,
            superseded_by=index.superseded_by,
            run_phase=run.run_phase,
            status=RunStatus.of(run.run_phase, index.stop_reason),
            producer_attached=run.producer.attached,
            run_admission=run.admission,
            pause=run.pause,
            standing=index.standing,
            rounds_closed=index.rounds_closed,
            created_at=index.created_at,
            updated_at=index.updated_at,
            human_intervened=index.human_intervened,
            spawned_by=index.spawned_by,
        )

    def enumerate_cycles(self) -> list[CycleListEntry]:
        return [
            self._list_entry(cycle_dir, index)
            for cycle_dir in self._cycle_dirs()
            if (index := read_cycle_index(cycle_dir)) is not None
        ]

    def _stub_deletion_blocked(self, hop: CycleHop) -> str | None:
        cycle_dir = self.cycle_dir(hop)
        if self.load(hop) is None:
            return "not on disk"
        if derive_run_state(cycle_dir).producer.attached:
            return "a producer holds it — pause it or let it end first"
        if root_cycle_id(hop.cycle_id) == hop.cycle_id:
            return "family root — deletion is for sibling stubs only"
        # Its OWN ledger, not the chain: a rebase fork stands on parent rounds it never closed.
        own = scan_standing_rounds([LedgerSpan(CycleLayout(cycle_dir).ledger)]).rounds
        if own:
            return f"closed {len(own)} round(s) of its own — cycle ran real work"
        for other in self.campaign_cycle_dirs(hop.campaign_id):
            minted = scan_cycle_facts(CycleLayout(other).ledger).minted
            if minted is not None and minted.parent_cycle_id == hop.cycle_id:
                return f"has descendant {other.name}"
        return None

    def try_delete_stub_cycle(self, hop: CycleHop) -> tuple[bool, str]:
        blocked = self._stub_deletion_blocked(hop)
        if blocked is not None:
            return False, blocked
        cycle_dir = self.cycle_dir(hop)
        # After the refusal, before the rmtree: an origin-scored stub already paid for round 0.
        bank_spend(
            workspace=self._base_dir,
            cycle_dirs=[cycle_dir],
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
        )
        rmtree_robust(cycle_dir)
        return True, ""

    def write_cycle_seed(self, hop: CycleHop, seed: CycleSeed) -> None:
        cycle_dir = self.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(CycleSeedRecord(seed=seed))

    def read_cycle_seed(self, hop: CycleHop) -> CycleSeed | None:
        return scan_ledger_cycle_seed(self._layout(hop).ledger)

    def write_run_limits(
        self,
        hop: CycleHop,
        ceiling: SpendCeilings,
        *,
        rounds: RoundsCap | None,
        pause_at_round: int | None,
        reserve: SpendCeilings,
    ) -> None:
        """Whole, last wins: a ``None`` rounds cap or ``pause_at_round`` DROPS the standing one."""
        CycleEventLog.open(CycleDir(self.cycle_dir(hop))).append(
            RunLimitsRecord(
                ceiling=ceiling, rounds=rounds, pause_at_round=pause_at_round, reserve=reserve
            )
        )

    def read_run_limits(self, hop: CycleHop) -> RunLimitsRecord:
        return scan_ledger_run_limits(self._layout(hop).ledger)

    def write_resolved_pipeline(self, hop: CycleHop, declaration: dict[str, Any]) -> None:
        write_yaml(self._layout(hop).resolved_pipeline, declaration)

    def write_resolved_experiment(
        self, hop: CycleHop, experiment: Mapping[str, Any] | None
    ) -> None:
        """First write wins: the roster names what every round ALREADY measured."""
        if experiment is None:
            return
        doc = dict(experiment)
        path = self._layout(hop).resolved_experiment
        held = read_yaml_optional(path)
        if held is None:
            write_yaml(path, doc)
        elif held != doc:
            logger.warning(
                "%s resolves a DIFFERENT panel than the one %s measured — the landed roster "
                "stands and this run's rows are not comparable to the earlier ones. Pin the "
                "backend's roster, or run this as a new campaign.",
                hop.campaign_id,
                path.name,
            )

    def read_resolved_experiment(self, hop: CycleHop) -> dict[str, Any] | None:
        raw = read_yaml_optional(self._layout(hop).resolved_experiment)
        return raw if isinstance(raw, dict) else None

    def read_bank_partition(self, hop: CycleHop) -> PartitionRecord | None:
        raw = read_json_optional(self._layout(hop).bank_partition)
        return None if raw is None else PartitionRecord.model_validate(raw)

    def write_bank_partition(self, hop: CycleHop, record: PartitionRecord) -> None:
        write_json(self._layout(hop).bank_partition, record.model_dump(mode="json"))

    def write_optimized_surface(self, hop: CycleHop, leaves: Sequence[ValueLeaf]) -> None:
        by_delivery: dict[str, list[ValueLeaf]] = {}
        for leaf in leaves:
            by_delivery.setdefault(leaf.delivery, []).append(leaf)
        lines = [
            "# What this cycle optimizes",
            "",
            "Derived at run init from `PipelineSchema.value_tree`, never hand-maintained.",
            "**may-not-arrive** marks a channel the model reads only if it OPENS the artifact",
            "carrying the value — there, a value can be mutated every round and reach nothing.",
            "",
            "These are the AXES. The VALUES they currently hold are in `pipeline.resolved.yaml`",
            "beside this file, written on the same cadence — not copied here, because a second",
            "copy of a value is one that can disagree with the declaration it came from.",
            "",
        ]
        for delivery in sorted(by_delivery):
            group = by_delivery[delivery]
            caveat = " · **may-not-arrive**" if group[0].may_not_arrive else ""
            lines.append(f"## {delivery} — {group[0].visibility}{caveat}")
            lines.append("")
            lines += [
                f"- `{leaf.path}` ({leaf.kind}){'' if leaf.mutable else ' — PINNED'}"
                for leaf in group
            ]
            lines.append("")
        write_text(self._layout(hop).optimized_surface, "\n".join(lines))

    def read_resolved_pipeline(self, hop: CycleHop) -> dict[str, Any] | None:
        raw = read_yaml_optional(self._layout(hop).resolved_pipeline)
        return raw if isinstance(raw, dict) else None

    def write_ruler(
        self, hop: CycleHop, ruler: DeltaRuler, *, dataset_name: str, round_num: int
    ) -> None:
        cycle_dir = self.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(
            RulerRecord(ruler=ruler, dataset_name=dataset_name, round=round_num)
        )

    def read_ruler(self, hop: CycleHop, *, dataset_name: str) -> DeltaRuler | None:
        return scan_ledger_ruler(continued_chain(CycleDir(self.cycle_dir(hop))), dataset_name)


__all__ = ["CampaignStore"]
