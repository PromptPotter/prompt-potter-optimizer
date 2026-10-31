from __future__ import annotations

import contextlib
import logging
import os
import shutil
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Unpack, get_args

from pydantic import ValidationError

from promptpotter.domain.bench import BenchScore, PartitionRecord
from promptpotter.domain.campaign import (
    Campaign,
    CampaignEdit,
    CampaignOwner,
    Compare,
    HeadToHeadRecord,
    LifecycleFilter,
    LifecycleStatus,
    ceiling_meter,
)
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, WorkspaceDir
from promptpotter.domain.export import PromptExport, parse_prompt_export
from promptpotter.domain.phases import ErrorRecord, LaunchStage, ProducerState, StopReason
from promptpotter.domain.pipeline_schema import CapabilityMenu
from promptpotter.domain.run_records import (
    BankedSubject,
    CampaignCeilingRecord,
    ComparisonExit,
    CycleFinal,
    CycleFinalRecord,
    CycleMintedRecord,
    CycleRecord,
    CycleSeed,
    CycleSeedRecord,
    CycleShareRecord,
    CycleSupersededRecord,
    ForkDirection,
    ForkGradedRecord,
    ForkSpec,
    InterventionRecord,
    LaunchClaimRecord,
    LaunchReleasedRecord,
    LeftComparisonRecord,
    ResolvedRunRecord,
    RoundEnteredRecord,
    RoundProposedRecord,
    RoundRestatedRecord,
    RunPhaseRecord,
    RunWiringRecord,
    SpawnedBy,
    SpawnedRecord,
    StandingBudgetRecord,
    parent_address,
)
from promptpotter.domain.spend import NOTHING_METERED, Budget, CeilingMeter
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.value_tree import ValueLeaf
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.ledger import CycleEventLog, ledger_chain
from promptpotter.infrastructure.projections.cycle_index import (
    read_cycle_index,
    write_cycle_index,
)
from promptpotter.infrastructure.projections.live_dashboard.projection import DashboardCheckout
from promptpotter.infrastructure.runtime_flags import (
    derive_run_state,
    run_state_stamps,
    standing_budget,
)
from promptpotter.infrastructure.store.account_spend import banked_metered, sandbox_cycle_dirs
from promptpotter.infrastructure.store.account_trail import bank_before_removal
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    ResolvedRun,
    StandingRounds,
    scan_campaign_ceiling,
    scan_cycle_facts,
    scan_cycle_share,
    scan_last,
    scan_launch_claim,
    scan_ledger_metered,
    scan_left_comparison,
    scan_resolved_run,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.data_root import campaign_row
from promptpotter.infrastructure.store.io import (
    iter_files,
    read_json_optional,
    read_text_optional,
    rmtree_robust,
    unlink_robust,
    write_json,
    write_text,
)
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    campaign_cycles_dir,
    campaign_root_dir_for,
    campaigns_root_dir_for,
    cycle_dir_for,
    head_to_head_path,
    inner_sandbox_key,
    root_cycle_id,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment, file_sig
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_path,
    clear_active_pointer,
    read_active_pointer,
)
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    ManifestUnreadableError,
    NotFoundError,
    graceful,
    validation_reason,
)

logger = logging.getLogger(__name__)


def _prune_empty_dirs(root: Path) -> None:
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        with contextlib.suppress(OSError):
            d.rmdir()


def _keepsake(path: Path, campaign_dir: Path) -> bool:
    row = campaign_row(PurePosixPath(path.relative_to(campaign_dir).as_posix()))
    return row is not None and row.keepsake


def _strip_to_keepsake(campaign_dir: Path) -> None:
    cycles_dir = campaign_cycles_dir(campaign_dir)
    if cycles_dir.is_dir():
        for cdir in cycles_dir.iterdir():
            if not cdir.is_dir():
                continue
            for p in [f for f, _st in iter_files(cdir) if not _keepsake(f, campaign_dir)]:
                unlink_robust(p)
            _prune_empty_dirs(cdir)


def _ledger_meters(ledgers: Iterable[Path]) -> dict[CeilingMeter, Budget]:
    total = dict.fromkeys(get_args(CeilingMeter), NOTHING_METERED)
    for ledger in ledgers:
        if ledger.is_file():
            for meter, counted in scan_ledger_metered(ledger).items():
                total[meter] = total[meter].plus(counted)
    return total


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

    def layout(self, hop: CycleHop) -> CycleLayout:
        return CycleLayout(self.cycle_dir(hop))

    def ledger(self, hop: CycleHop) -> Path:
        return self.layout(hop).ledger

    def _append(self, hop: CycleHop, record: CycleRecord) -> int:
        cycle_dir = self.cycle_dir(hop)
        offset = CycleEventLog.open(CycleDir(cycle_dir)).append(record)
        write_cycle_index(cycle_dir)
        return offset

    def _read_declared[M: (Campaign, CampaignOwner, HeadToHeadRecord)](
        self, path: Path, model: type[M]
    ) -> M | None:
        data = read_json_optional(path)
        if data is None:
            return None
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise ManifestUnreadableError(
                path=str(path.relative_to(self._base_dir)),
                reason=validation_reason(exc, shown=1),
            ) from exc

    def load_campaign(self, campaign_id: str) -> Campaign | None:
        return self._read_declared(self._manifest_path(campaign_id), Campaign)

    def campaign_owner(self, campaign_id: str) -> CampaignOwner | None:
        return self._read_declared(self._manifest_path(campaign_id), CampaignOwner)

    def _campaigns_root(self) -> Path:
        return campaigns_root_dir_for(self._base_dir)

    def _cycle_dirs(self) -> list[Path]:
        root = self._campaigns_root()
        return sorted(p for p in root.glob("*/cycles/*") if p.is_dir()) if root.exists() else []

    def version(self) -> tuple[object, ...]:
        """Stats only: what a read over this store's campaigns, cycles and run states stands on."""
        root = self._campaigns_root()
        return (
            str(self._base_dir),
            file_sig(root),
            file_sig(active_pointer_path(self._base_dir)),
            file_sig(CycleEventLog.workspace_path(self._base_dir)),
            *(
                (
                    campaign.name,
                    file_sig(CampaignLayout(campaign).manifest),
                    *(
                        (cycle.name, *run_state_stamps(cycle))
                        for cycle in sorted(campaign_cycles_dir(campaign).glob("*"))
                    ),
                )
                for campaign in (sorted(root.iterdir()) if root.is_dir() else ())
            ),
        )

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

    def metered_spend(self, campaign_id: str, meter: CeilingMeter) -> Budget:
        """Each cycle's OWN appends once (a fork's prefix is its parent's), and what a deleted cycle metered."""
        own = _ledger_meters(self.campaign_cycle_ledgers(campaign_id))[meter]
        return own.plus(banked_metered(self._base_dir, campaign_id, meter))

    def cycle_metered(self, hop: CycleHop, meter: CeilingMeter) -> Budget:
        return _ledger_meters([self.ledger(hop)])[meter]

    def ceiling_meter_of(self, campaign_id: str) -> CeilingMeter:
        campaign = self.load_campaign(campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {campaign_id}")
        return ceiling_meter(campaign.declaration.purpose)

    def campaign_walk_spans(self, campaign_id: str) -> list[LedgerSpan]:
        """Each cycle's whole chain, so a cycle lifted from another tree remembers its prefix."""
        widest: dict[Path, int | None] = {}
        for cycle_dir in self.campaign_cycle_dirs(campaign_id):
            for path, until in ledger_chain(CycleDir(cycle_dir), moment=None):
                if not path.is_file():
                    continue
                held = widest.get(path, 0)
                widest[path] = None if until is None or held is None else max(held, until)
        return [LedgerSpan(path, until) for path, until in widest.items()]

    def iter_cycle_ledgers(self) -> list[Path]:
        return [
            ledger
            for campaign_dir in self.iter_campaign_dirs()
            for ledger in self.campaign_cycle_ledgers(campaign_dir.name)
        ]

    def create_campaign(self, campaign: Campaign) -> Path:
        """Never over one it holds: a second root would orphan every line banked under the first."""
        path = self._manifest_path(campaign.campaign_id)
        if path.exists():
            raise ConflictError(
                f"campaign {campaign.campaign_id} exists; a mint never re-roots it",
                code="campaign_exists",
            )
        write_json(path, campaign.model_dump(mode="json"))
        return path

    def load_head_to_head(self, head_to_head_id: str) -> HeadToHeadRecord | None:
        return self._read_declared(
            head_to_head_path(self._base_dir, head_to_head_id), HeadToHeadRecord
        )

    def declare_head_to_head(self, record: HeadToHeadRecord) -> None:
        write_json(
            head_to_head_path(self._base_dir, record.head_to_head_id),
            record.model_dump(mode="json"),
        )

    def update_campaign(self, campaign_id: str, **changes: Unpack[CampaignEdit]) -> None:
        manifest = self.load_campaign(campaign_id)
        if manifest is None:
            raise NotFoundError(f"campaign not found: {campaign_id}")
        write_json(
            self._manifest_path(campaign_id), manifest.edited(**changes).model_dump(mode="json")
        )

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
            # A survey: one manifest this build cannot read must not hide the account's others.
            try:
                campaign = self.load_campaign(cid)
            except ManifestUnreadableError as exc:
                logger.warning("%s", exc)
                continue
            if campaign is None:
                continue
            if dataset_name and campaign.dataset_name != dataset_name:
                continue
            if lifecycle != "all" and campaign.lifecycle_status != lifecycle:
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

    def _bank(
        self,
        cycle_dirs: list[Path],
        *,
        campaign_id: str,
        cycle_id: str = "",
        metered: dict[CeilingMeter, Budget],
    ) -> None:
        bank_before_removal(
            workspace=self._base_dir,
            cycle_dirs=cycle_dirs,
            subject=BankedSubject(campaign_id=campaign_id, cycle_id=cycle_id),
            metered=metered,
        )

    def _campaign_metered_bank(self, campaign_id: str) -> None:
        # What its rows metered, never the banked part: that is a tombstone already.
        self._bank(
            self.campaign_cycle_dirs(campaign_id),
            campaign_id=campaign_id,
            metered=_ledger_meters(self.campaign_cycle_ledgers(campaign_id)),
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
        if not campaign_dir.is_dir():
            return False
        # A keepsake is its manifest marked deleted, so one this build cannot read refuses before the bank.
        # With no manifest nothing marks it deleted, and its rows would count beside the bank.
        keepsake = keep_results and self.load_campaign(campaign_id) is not None
        self._guard_and_release(campaign_id, "delete")
        # After the guard: a tombstone beside a refused delete's rows counts the money twice.
        self._campaign_metered_bank(campaign_id)
        # Before the tree goes: the off-tree inner sandboxes are keyed by these ids.
        inner_cycle_ids: list[str] = []
        if inner_sandbox_root is not None:
            cycles_dir = campaign_cycles_dir(campaign_dir)
            if cycles_dir.is_dir():
                inner_cycle_ids = [p.name for p in cycles_dir.iterdir() if p.is_dir()]
        if keepsake:
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
        # Metered nothing of the outer ceiling: the outer ledger carries every inner call.
        self._bank(
            sandbox_cycle_dirs(sandbox),
            campaign_id=campaign_id,
            cycle_id=sandbox.name,
            metered=_ledger_meters([]),
        )
        rmtree_robust(sandbox)

    def load(self, hop: CycleHop, *, moment: Moment | None) -> CycleIndex | None:
        return read_cycle_index(self.cycle_dir(hop), moment=moment)

    def adopt_ledger(self, hop: CycleHop, ledger: Path) -> None:
        """A MOVE, never a copy: what a draft billed is the cycle's first lines."""
        target = self.ledger(hop)
        if target.exists():
            raise ConflictError(f"cycle {hop.cycle_id} already holds a ledger")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(ledger, target)

    def mint_cycle(self, hop: CycleHop) -> None:
        if scan_cycle_facts(self.ledger(hop)).minted is None:
            self._append(hop, CycleMintedRecord())

    def mint_fork_cycle(
        self, child: CycleHop, parent_dir: Path, spec: ForkSpec, *, cut: int, cut_round: int
    ) -> None:
        """*parent_dir* may lie in another tree: the record names it by path, never by id."""
        self._append(
            child,
            CycleMintedRecord(
                parent=parent_address(self.cycle_dir(child), parent_dir),
                forked_at_offset=cut,
                cut_round=cut_round,
                fork=spec.model_copy(update={"seed": None}),
            ),
        )
        self._append(child, RoundEnteredRecord(round=cut_round, rewound=True))

    def grade_fork(self, hop: CycleHop, direction: ForkDirection) -> None:
        self._append(hop, ForkGradedRecord(direction=direction))

    def record_intervention(self, hop: CycleHop, *, kind: str) -> None:
        self._append(hop, InterventionRecord(kind=kind))

    def leave_comparison(self, hop: CycleHop, act: ComparisonExit) -> None:
        """decisions.md § 0009: every act on an arm is admitted, and this is the event it leaves by."""
        campaign = self.load_campaign(hop.campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {hop.campaign_id}")
        if isinstance(campaign.declaration.purpose, Compare):
            self._append(hop, LeftComparisonRecord(act=act))

    def left_comparison(self, campaign_id: str) -> list[ComparisonExit]:
        """Off every cycle of the campaign: the arm is the campaign, whichever line an act ran on."""
        return scan_left_comparison(
            LedgerSpan(ledger) for ledger in self.campaign_cycle_ledgers(campaign_id)
        )

    def record_spawned(self, hop: CycleHop, spawned_by: SpawnedBy) -> None:
        self._append(hop, SpawnedRecord(spawned_by=spawned_by))

    def standing_rounds(self, hop: CycleHop, *, moment: Moment | None) -> StandingRounds:
        return scan_standing_rounds(ledger_chain(CycleDir(self.cycle_dir(hop)), moment=moment))

    def round_proposals(self, hop: CycleHop, round_num: int) -> RoundProposedRecord | None:
        held = self.standing_rounds(hop, moment=None).proposals.get(round_num)
        return None if held is None else held.record

    def round_submission(self, hop: CycleHop, round_num: int) -> RoundProposedRecord | None:
        return self.standing_rounds(hop, moment=None).submitted.get(round_num)

    def propose_round(self, hop: CycleHop, proposed: RoundProposedRecord) -> int:
        return self._append(hop, proposed)

    def rebank_round(self, hop: CycleHop, statement: RoundRestatedRecord) -> int:
        """A closed round stated again on a cycle no run holds; a running cycle restates on its live ledger."""
        return self._append(hop, statement)

    def rewind_enters(self, hop: CycleHop, after_round: int) -> int:
        if not self.ledger(hop).exists():
            raise NotFoundError(f"cycle {hop.cycle_id!r} has no ledger on disk")
        last = self.standing_rounds(hop, moment=None).last_closed
        if last is None or after_round > last:
            held = "none" if last is None else f"0..{last}"
            raise BadRequestError(f"--from {after_round}: ledger's completed rounds are {held}")
        return after_round + 1

    def rewind_to_round(self, hop: CycleHop, after_round: int) -> None:
        entered = self.rewind_enters(hop, after_round)
        self._append(hop, RoundEnteredRecord(round=entered, rewound=True))

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
        write_text(self.layout(hop).export, export.to_json())

    def restate_export_bench(self, hop: CycleHop, bench: BenchScore) -> None:
        export = self.read_export(hop)
        if export is not None:
            self._write_export(hop, export.model_copy(update={"bench": bench}))

    def read_export(self, hop: CycleHop) -> PromptExport | None:
        text = read_text_optional(self.layout(hop).export)
        return parse_prompt_export(text) if text else None

    def mark_superseded(self, hop: CycleHop, successor_cycle_id: str) -> None:
        # The relation always lands; the ending only where none stands, or it replaces why it ended.
        with graceful("Supersede relation append failed"):
            self._append(hop, CycleSupersededRecord(successor_cycle_id=successor_cycle_id))
        self._declare_ended(hop, StopReason.REBASED)

    def line(self, hop: CycleHop, *, moment: Moment | None = None) -> list[CycleHop]:
        """The supersede chain from *hop*; *moment* walks it as it stood then."""
        out = [hop]
        while successor := self._successor(hop, moment):
            hop = CycleHop(campaign_id=hop.campaign_id, cycle_id=successor)
            out.append(hop)
        return out

    def line_to(self, hop: CycleHop) -> list[CycleHop]:
        """The supersede chain ending at *hop*, oldest first: `line` walked back."""
        out = [hop]
        while (index := self.load(out[0], moment=None)) is not None and (
            parent := index.parent_cycle_id
        ) is not None:
            up = CycleHop(campaign_id=hop.campaign_id, cycle_id=parent)
            if self._successor(up, None) != out[0].cycle_id:
                break
            out.insert(0, up)
        return out

    def line_holder(self, hop: CycleHop, *, moment: Moment | None = None) -> CycleHop:
        return self.line(hop, moment=moment)[-1]

    def _successor(self, hop: CycleHop, moment: Moment | None) -> str | None:
        ledger = self.ledger(hop)
        until = None if moment is None else moment.span(ledger).until
        return scan_cycle_facts(ledger, until).superseded_by

    def declare_stop(
        self, hop: CycleHop, stop: RunPhaseRecord, *, error: ErrorRecord | None = None
    ) -> None:
        cycle_dir = CycleDir(self.cycle_dir(hop))
        dashboard = DashboardCheckout.over(cycle_dir)
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
        return scan_launch_claim(self.ledger(hop))

    def release_claim(self, hop: CycleHop, *, job_id: str, detail: str) -> None:
        claim = self.launch_claim(hop)
        if claim is not None and claim.job_id == job_id:
            self._append(hop, LaunchReleasedRecord(job_id=job_id, detail=detail))

    def _declare_ended(self, hop: CycleHop, reason: StopReason) -> bool:
        lock = self.layout(hop).producer_lock
        # A held cycle's producer declares its own stop; asked first, so no caller waits out a live hold.
        if producer_lock.held(lock) or not producer_lock.take(lock):
            return False
        try:
            facts = scan_cycle_facts(self.ledger(hop))
            if facts.minted is None or facts.ended is not None:
                return False
            self.declare_stop(hop, RunPhaseRecord.stop(reason))
            return True
        finally:
            producer_lock.release(lock)

    def mark_producer_vanished(self, hop: CycleHop) -> bool:
        if derive_run_state(self.cycle_dir(hop)).producer.state is not ProducerState.SILENT:
            return False
        return self._declare_ended(hop, StopReason.PRODUCER_VANISHED)

    def enumerate_cycles(self) -> list[tuple[CycleHop, CycleIndex]]:
        """Every cycle of every campaign in this store whose ledger folds to an index."""
        return _indexed(self._cycle_dirs())

    def campaign_cycles(self, campaign_id: str) -> list[tuple[CycleHop, CycleIndex]]:
        """`enumerate_cycles`, narrowed to one campaign's directory."""
        return _indexed(self.campaign_cycle_dirs(campaign_id))

    def _stub_deletion_blocked(self, hop: CycleHop) -> str | None:
        cycle_dir = self.cycle_dir(hop)
        if self.load(hop, moment=None) is None:
            return "not on disk"
        if derive_run_state(cycle_dir).producer.attached:
            return "a producer holds it — pause it or let it end first"
        if root_cycle_id(hop.cycle_id) == hop.cycle_id:
            return "family root — deletion is for sibling stubs only"
        # Its OWN ledger, not the chain: a rebase fork stands on parent rounds it never closed.
        own = scan_standing_rounds([LedgerSpan(self.ledger(hop))]).rounds
        if own:
            return f"closed {len(own)} round(s) of its own — cycle ran real work"
        here = Path(os.path.normpath(cycle_dir))
        for other in self.campaign_cycle_dirs(hop.campaign_id):
            minted = scan_cycle_facts(CycleLayout(other).ledger).minted
            if minted is not None and minted.parent_cycle_dir(other) == here:
                return f"has descendant {other.name}"
        return None

    def deletable_stubs(self, hop: CycleHop) -> list[CycleHop]:
        """What ``cleanup-empty-cycles`` at *hop* deletes: its root's stub forks, never the active cycle."""
        root_id = root_cycle_id(hop.cycle_id)
        active = read_active_pointer(self._base_dir)
        stubs: list[CycleHop] = []
        for cycle_dir in self.campaign_cycle_dirs(hop.campaign_id):
            fork = CycleHop(campaign_id=hop.campaign_id, cycle_id=cycle_dir.name)
            index = read_cycle_index(cycle_dir, moment=None)
            if index is None or index.parent_cycle_id != root_id or fork.cycle_id == root_id:
                continue
            if (fork.campaign_id, fork.cycle_id) != active and (
                self._stub_deletion_blocked(fork) is None
            ):
                stubs.append(fork)
        return stubs

    def try_delete_stub_cycle(self, hop: CycleHop) -> tuple[bool, str]:
        blocked = self._stub_deletion_blocked(hop)
        if blocked is not None:
            return False, blocked
        cycle_dir = self.cycle_dir(hop)
        # After the refusal, before the rmtree: an origin-scored stub already paid for round 0.
        self._bank(
            [cycle_dir],
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            metered=_ledger_meters([self.ledger(hop)]),
        )
        rmtree_robust(cycle_dir)
        return True, ""

    def write_cycle_seed(self, hop: CycleHop, seed: CycleSeed) -> None:
        cycle_dir = self.cycle_dir(hop)
        CycleEventLog.open(CycleDir(cycle_dir)).append(CycleSeedRecord(seed=seed))

    def last[R: StrictModel](self, hop: CycleHop, record: type[R]) -> R | None:
        """The cycle's OWN last *record*: a fork inherits none from its parent's prefix."""
        return scan_last([LedgerSpan(self.ledger(hop))], record)

    def read_cycle_seed(self, hop: CycleHop) -> CycleSeed | None:
        record = self.last(hop, CycleSeedRecord)
        return None if record is None else record.seed

    def write_standing_budget(
        self, hop: CycleHop, budget: Budget, *, pause_at_round: int | None
    ) -> None:
        """Whole, last wins: an axis it leaves unnamed, or a ``None`` ``pause_at_round``, stands no more."""
        CycleEventLog.open(CycleDir(self.cycle_dir(hop))).append(
            StandingBudgetRecord(budget=budget, pause_at_round=pause_at_round)
        )

    def read_standing_budget(self, hop: CycleHop) -> StandingBudgetRecord:
        return standing_budget(self.cycle_dir(hop))

    def write_campaign_ceiling(self, campaign: Campaign, money: Budget) -> None:
        """Whole, last wins, on the campaign's root: an axis it leaves unnamed stands no more."""
        CycleEventLog.open(CycleDir(self.cycle_dir(campaign.root_hop))).append(
            CampaignCeilingRecord(budget=money)
        )

    def read_campaign_ceiling(self, hop: CycleHop) -> Budget:
        """Off any cycle of the campaign: each reads its root's."""
        return scan_campaign_ceiling(self.cycle_dir(hop))

    def write_cycle_share(self, hop: CycleHop, allotment: Budget) -> None:
        CycleEventLog.open(CycleDir(self.cycle_dir(hop))).append(
            CycleShareRecord(allotment=allotment)
        )

    def read_cycle_share(self, hop: CycleHop) -> CycleShareRecord | None:
        return scan_cycle_share(self.ledger(hop))

    def _chain(self, hop: CycleHop) -> list[LedgerSpan]:
        return ledger_chain(CycleDir(self.cycle_dir(hop)), moment=None)

    def write_resolved_run(
        self,
        hop: CycleHop,
        *,
        declaration: dict[str, Any],
        experiment: Mapping[str, Any] | None,
        model_capabilities: CapabilityMenu,
    ) -> None:
        """Appended whenever this launch resolves other than the chain holds, a moved panel included."""
        doc = None if experiment is None else dict(experiment)
        held = self.read_resolved_run(hop)
        panel_moved = doc is not None and held.experiment is not None and held.experiment != doc
        if panel_moved:
            logger.warning(
                "%s resolves a DIFFERENT panel than the one cycle %s measured — the landed roster "
                "stands and this run's rows are not comparable to the earlier ones. Pin the "
                "backend's roster, or run this as a new campaign.",
                hop.campaign_id,
                held.experiment_cycle_id,
            )
        resolved = ResolvedRun(declaration, doc, model_capabilities=model_capabilities)
        landed = held.then(resolved)
        if panel_moved or landed != held:
            self._append(
                hop,
                ResolvedRunRecord(
                    declaration=declaration,
                    experiment=doc,
                    model_capabilities=model_capabilities,
                ),
            )

    def read_resolved_run(self, hop: CycleHop) -> ResolvedRun:
        return scan_resolved_run(self._chain(hop))

    def read_run_wiring(self, hop: CycleHop) -> RunWiringRecord | None:
        """The chain's last launch's wiring; ``None`` before the first is declared."""
        return scan_last(self._chain(hop), RunWiringRecord)

    def read_bank_partition(self, hop: CycleHop) -> PartitionRecord | None:
        return scan_last(self._chain(hop), PartitionRecord)

    def write_bank_partition(self, hop: CycleHop, record: PartitionRecord) -> None:
        self._append(hop, record)

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
            "These are the AXES. The VALUES they currently hold are the `resolved_run` record on",
            "this cycle's ledger, appended on the same cadence — not copied here, because a second",
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
        write_text(self.layout(hop).optimized_surface, "\n".join(lines))


def _indexed(cycle_dirs: list[Path]) -> list[tuple[CycleHop, CycleIndex]]:
    return [
        (CycleHop(campaign_id=cycle_dir.parent.parent.name, cycle_id=cycle_dir.name), index)
        for cycle_dir in cycle_dirs
        if (index := read_cycle_index(cycle_dir, moment=None)) is not None
    ]
