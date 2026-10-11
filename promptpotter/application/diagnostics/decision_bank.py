from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from statistics import fmean
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.campaign_config import DeterminismClamp
from promptpotter.application.initialization.loop_start import (
    arm_diagnostic_scoring,
    diagnostic_pass,
    diagnostic_trace,
    measured_cell_usd,
)
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.jobs.quota import paid_verb
from promptpotter.application.optimizer_manifest import (
    bind_optimizer,
    resolved_overrides,
    set_determinism_clamp,
    set_optimizer_prompt_overrides,
)
from promptpotter.application.optimizers.nodes import Panel, Proposals, RoundContext
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.runner.round import propose_population, round_plan
from promptpotter.application.scoring.candidate_report import fatal_validation_failures
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.scoring.paired import (
    MemberRows,
    fresh_cells,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.sample_measurement import cell_bound
from promptpotter.application.scoring.search_point_scorer import reread_cells, score_search_point
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import ROUND_LIFT_SPEC, CellSetName, MemberAddress
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.wounds import collapse_counts
from promptpotter.infrastructure.llm.spend_book import SendBound, SpendBook, spending_under
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger
from promptpotter.infrastructure.store.io import write_json
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError, ErrorCategory, SendRefusedError
from promptpotter.shared.hashing import dataset_hash
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope
from promptpotter.shared.statistics import exact_paired_reading, paired_mean_t

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.spend import TokenUsageKind
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = [
    "ArmLifts",
    "DecisionBankError",
    "plan_totals",
    "read_arm",
    "read_bank",
    "run_decision_bank",
]

Overrides = dict[str, dict[str, Any]]


class DecisionBankError(ConflictError):
    """A resolved-state failure; the CLI shell maps it to a clean ``SystemExit``."""


class _RefusingBook(SpendBook):
    def __init__(self) -> None:
        super().__init__(declared=SpendCeilings(0.0, None), reserved=SpendCeilings(), meters="bill")
        self.refused: list[SendBound] = []

    def hold(
        self,
        held: SendBound,
        bound: SendBound,
        kind: TokenUsageKind,
        *,
        what: str,
        drawn_from: object | None = None,
    ) -> None:
        self.refused.append(bound)
        raise SendRefusedError(
            f"{what}: a dry run sends nothing", category=ErrorCategory.SPEND_CEILING
        )


@dataclass(frozen=True)
class Arm:
    """One side of the comparison: the optimizer-prompt overrides it proposes under, bound as an
    L4 inner cell's are, and the seed its proposers draw with."""

    overrides: Overrides
    seed: int


@dataclass(frozen=True)
class ArmLifts:
    """A proposal sharing fewer than two graded cells with its parent has no entry in ``lifts``."""

    proposed: int
    rejected: int
    collapses: dict[str, int]
    lifts: tuple[float, ...]

    @property
    def best_lift(self) -> float:
        """The round's best proposal, or the parent it holds (0.0)."""
        return max((*self.lifts, 0.0))

    @property
    def mean_lift(self) -> float | None:
        return fmean(self.lifts) if self.lifts else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposed": self.proposed,
            "rejected": self.rejected,
            "collapses": self.collapses,
            "lifts": list(self.lifts),
            "best_lift": self.best_lift,
            "mean_lift": self.mean_lift,
        }


@dataclass(frozen=True)
class ArmPlan:
    calls: int
    call_usd: float | None
    cells: int
    replayed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "call_usd": self.call_usd,
            "cells": self.cells,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class Decision:
    hop: CycleHop
    round_num: int
    n_cells: int
    cell_usd: float | None
    cell_usd_measured: float | None
    plans: tuple[ArmPlan, ArmPlan] | None = None
    readings: tuple[ArmLifts, ArmLifts] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.hop.campaign_id,
            "cycle_id": self.hop.cycle_id,
            "round": self.round_num,
            "cells": self.n_cells,
            "cell_usd": self.cell_usd,
            "cell_usd_measured": self.cell_usd_measured,
            "plans": None if self.plans is None else [p.as_dict() for p in self.plans],
            "readings": None if self.readings is None else [r.as_dict() for r in self.readings],
        }


@dataclass(frozen=True)
class DecisionBankOutcome:
    dataset_name: str
    arms: tuple[Arm, Arm]
    decisions: list[Decision]
    skipped: dict[str, int]
    reading: dict[str, Any] | None
    stopped: str | None
    artifact_path: str | None


def read_arm(
    parent: MemberRows,
    proposals: Sequence[MemberRows],
    *,
    proposed: int,
    collapses: dict[str, int],
) -> ArmLifts:
    """The pair a round reads an arm on (``ROUND_LIFT_SPEC``), so both read one number."""
    cells = list(parent.by_key())
    readings = [
        read_pair(
            a=parent,
            b=member,
            cell_set=CellSetName.REFERENCE_CELLS,
            cells=cells,
            masked=False,
            dataset_hash=parent.dataset_hash,
            measurands=grade_measurands(parent.sheet.scorer_id),
            spec=ROUND_LIFT_SPEC,
            scope=parent.scope,
            instrument_id=parent.instrument_id,
        )
        for member in proposals
    ]
    return ArmLifts(
        proposed=proposed,
        rejected=proposed - len(proposals),
        collapses=collapses,
        lifts=tuple(r.headline.estimate.value for r in readings if r.headline is not None),
    )


def _difference(other: list[float], base: list[float]) -> dict[str, Any] | None:
    if not base:
        return None
    mean, lo, hi, p, n = paired_mean_t(other, base)
    shift, exact_lo, exact_hi, exact_p, _ = exact_paired_reading(other, base)
    return {
        "n": n,
        "mean": mean,
        "ci": [lo, hi],
        "p": p,
        "exact_median": shift,
        "exact_ci": [exact_lo, exact_hi],
        "exact_p": exact_p,
    }


def read_bank(pairs: Sequence[tuple[ArmLifts, ArmLifts]]) -> dict[str, Any]:
    both = [(a.mean_lift, b.mean_lift) for a, b in pairs]
    measured = [(a, b) for a, b in both if a is not None and b is not None]
    return {
        "decisions": len(pairs),
        "best_lift": _difference([b.best_lift for _, b in pairs], [a.best_lift for a, _ in pairs]),
        "mean_lift": _difference([b for _, b in measured], [a for a, _ in measured]),
        "proposed": [sum(r.proposed for r in side) for side in zip(*pairs, strict=True)],
        "rejected": [sum(r.rejected for r in side) for side in zip(*pairs, strict=True)],
        "empty_generations": [
            sum(1 for r in side if not r.proposed) for side in zip(*pairs, strict=True)
        ],
    }


def _total(parts: Sequence[float | None]) -> float | None:
    return None if any(p is None for p in parts) else sum(p for p in parts if p is not None)


def plan_totals(decisions: Sequence[Decision]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for side in (0, 1):
        planned = [(d, d.plans[side]) for d in decisions if d.plans is not None]
        fresh = [(d, plan.cells - plan.replayed) for d, plan in planned]
        calls_usd = _total([plan.call_usd for _, plan in planned])
        out.append(
            {
                "optimizer_calls": sum(plan.calls for _, plan in planned),
                "optimizer_calls_usd": calls_usd,
                "cells": sum(plan.cells for _, plan in planned),
                "cells_replayed": sum(plan.replayed for _, plan in planned),
                "bound_usd": _total(
                    [calls_usd, *(None if d.cell_usd is None else n * d.cell_usd for d, n in fresh)]
                ),
                "measured_usd": _total(
                    [
                        calls_usd,
                        *(
                            None if d.cell_usd_measured is None else n * d.cell_usd_measured
                            for d, n in fresh
                        ),
                    ]
                ),
            }
        )
    return out


@dataclass
class _Frozen:
    hop: CycleHop
    round_num: int
    session: Session
    cells: list[Sample]
    parent_id: str
    parent: CellSheet
    recorded: int
    cell_usd: float | None
    cell_usd_measured: float | None
    populations: list[Proposals | None]
    refused: list[list[SendBound]]
    walks: list[list[tuple[str, JobSearchPoint]]]

    def member(
        self, individual_id: str, role: MeasurementRole | None, sheet: CellSheet, *, bought: int
    ) -> MemberRows:
        """``role`` is ``None`` for a proposal: this verb's pass is no round's role."""
        return MemberRows(
            address=MemberAddress(
                path=(self.hop,), individual_id=individual_id, arm=None, pass_role=role
            ),
            sheet=sheet,
            bought=bought,
            cut=False,
            scope=RoleScope.REPORT,
            instrument_id=self.session.instrument_id,
            dataset_hash=dataset_hash(self.session.samples),
            cell_set_id=None,
        )


async def _propose(cycle: Cycle, round_num: int, panel: Panel, arm: Arm) -> Proposals:
    set_optimizer_prompt_overrides(arm.overrides)
    pinned = cycle.config.optimization.determinism or DeterminismClamp()
    set_determinism_clamp(pinned.model_copy(update={"seed": arm.seed}))
    bind_optimizer(cycle.optimizer)
    ledger = active_cycle_ledger()
    assert ledger is not None, "a decision is proposed inside the verb's diagnostic trace"
    ctx = RoundContext(
        cycle=cycle, round_num=round_num, callbacks=RunCallbacks(ledger=ledger), detached=True
    )
    return await propose_population(ctx, round_plan(cycle.optimizer), panel)


def _open_cycle(session: Session, config: CampaignConfig, origin: RoundResult) -> Cycle:
    if origin.opt_sp is None:
        raise DecisionBankError("round 0 names no origin individual.")
    cycle = Cycle.start(
        origin.opt_sp,
        origin.origin,
        schema=session.pipeline_schema,
        framing=campaign_framing(session.store, config, session.dataset_name),
        origin_results=origin.results,
        session=session,
        config=config,
    )
    cycle.sample_index = SampleIndex.ensure_for(
        session.store,
        scorer=session.scoring.require_scorer(),
        dataset_name=session.dataset_name,
        sample_ids=session.scoring.require_partition().admitted_ids,
    )
    return cycle


def _first_cells(sheet: CellSheet | None, n: int | None) -> CellSheet:
    if sheet is None:
        return NO_CELLS
    kept = frozenset(list(sheet.on_ruler)[:n])
    return sheet.where(lambda cell: cell.ruler_key in kept)


async def _freeze_cycle(
    stores: Stores,
    hop: CycleHop,
    arms: tuple[Arm, Arm],
    dry: _RefusingBook | None,
    skipped: dict[str, int],
    *,
    cells: int | None,
    parallel: int,
) -> list[_Frozen]:
    campaigns = stores.campaigns
    standing = campaigns.standing_rounds(hop)
    recorded = {
        n: len(proposed.proposals)
        for n, proposed in standing.proposals.items()
        if n >= 1 and n in standing.rounds
    }
    if not recorded:
        return []
    campaign = campaigns.load_campaign(hop.campaign_id)
    assert campaign is not None, "the hop was enumerated off this campaign's manifest"
    session, config = await bind_cycle_session(stores, campaign, hop)
    arm_diagnostic_scoring(session, config, source=RunSource.DECISION_BANK)
    params = session.pipeline_params
    session.control = replace(session.control, held_lookahead=parallel)
    rounds = {
        rr.round: rr
        for rr in closed_rounds(
            stores, hop, session.scoring.require_scorer(), before_round=max(recorded) + 1
        )
    }
    if 0 not in rounds:
        skipped["no_origin_round"] = skipped.get("no_origin_round", 0) + len(recorded)
        return []
    cycle = _open_cycle(session, config, rounds[0])
    pool = {int(s.id): s for s in session.scoring.require_partition().search}
    bound = await cell_bound(session, params)
    measured_usd = measured_cell_usd(session, list(node_config_items(params)))

    frozen: list[_Frozen] = []
    for round_num in sorted(recorded):
        prior = [rounds[k] for k in range(round_num) if k in rounds]
        decided = rounds.get(round_num)
        parent = prior[-1].opt_sp if len(prior) == round_num else None
        if decided is None or parent is None:
            skipped["rounds_missing"] = skipped.get("rounds_missing", 0) + 1
            continue
        reference = _first_cells(decided.reference_results.get(parent.id), cells)
        ids = list(reference.on_ruler)
        if len(ids) < 2 or any(sid not in pool for sid in ids):
            skipped["no_parent_panel"] = skipped.get("no_parent_panel", 0) + 1
            continue
        panel_cells = [pool[sid] for sid in ids]
        panel = Panel(cells=panel_cells, order=panel_cells, block_size=1)
        point = _Frozen(
            hop=hop,
            round_num=round_num,
            session=session,
            cells=panel_cells,
            parent_id=parent.id,
            parent=reference,
            recorded=recorded[round_num],
            cell_usd=None if bound is None else bound.usd,
            cell_usd_measured=measured_usd,
            populations=[],
            refused=[],
            walks=[],
        )
        demo = session.scoring.require_partition().demo
        for arm in arms:
            cycle.replay_priors(prior)
            sent = len(dry.refused) if dry is not None else 0
            population: Proposals | None = None
            try:
                # Its own task: the arm's overrides and seed are per-task bindings and end with it.
                population = await asyncio.create_task(_propose(cycle, round_num, panel, arm))
            except SendRefusedError as refusal:
                if dry is None:
                    raise DecisionBankError(
                        f"{hop.campaign_id} round {round_num}: {refusal} — no arm was measured."
                    ) from refusal
            point.populations.append(population)
            point.refused.append(dry.refused[sent:] if dry is not None else [])
            point.walks.append(
                []
                if population is None
                else [
                    (
                        proposal.opt_sp.id,
                        proposal.opt_sp.to_job_search_point(
                            schema=session.pipeline_schema,
                            framing=cycle.framing,
                            demo=demo,
                        ),
                    )
                    for proposal in population.proposals
                    if not fatal_validation_failures(proposal.validation_failures)
                ]
            )
        frozen.append(point)
        logger.info(
            "%s round %d: state rebuilt on %d cells", hop.campaign_id, round_num, len(panel_cells)
        )
    return frozen


def _plan(point: _Frozen, side: int) -> ArmPlan:
    population, refused = point.populations[side], point.refused[side]
    if population is None:
        priced = [b.usd for b in refused]
        return ArmPlan(
            calls=len(refused),
            call_usd=None if any(u is None for u in priced) else sum(u for u in priced if u),
            cells=point.recorded * len(point.cells),
            replayed=0,
        )
    walks = point.walks[side]
    return ArmPlan(
        calls=0,
        call_usd=0.0,
        cells=len(walks) * len(point.cells),
        replayed=sum(
            len(reread_cells(sp, point.cells, point.session, label="decision_bank"))
            for _, sp in walks
        ),
    )


async def _measure(point: _Frozen, side: int) -> ArmLifts:
    population = point.populations[side]
    assert population is not None, "a measured arm proposed"
    members: list[MemberRows] = []
    for individual_id, sp in point.walks[side]:
        scored = await diagnostic_pass(
            DecisionBankError,
            score_search_point(
                sp, point.cells, point.session, label="decision_bank", measured=None
            ),
        )
        members.append(
            point.member(individual_id, None, scored.sheet, bought=fresh_cells(scored.sheet))
        )
    return read_arm(
        # The round bought the parent's cells; this verb reads them.
        point.member(point.parent_id, MeasurementRole.PARENT, point.parent, bought=0),
        members,
        proposed=len(population.proposals),
        collapses=collapse_counts(cp.validation_failures for cp in population.proposals),
    )


async def run_decision_bank(
    *,
    stores: Stores,
    dataset_name: str,
    campaign_ids: Sequence[str],
    base: Overrides,
    variant: Overrides | None,
    seed: int,
    cells: int | None,
    max_usd: float | None,
    parallel: int,
) -> DecisionBankOutcome:
    """No ``variant`` grades ``base`` against itself under another seed: the noise floor."""
    if variant is not None and resolved_overrides(variant) == resolved_overrides(base):
        raise DecisionBankError(
            "the variant resolves to the optimizer the base runs — no node of it names a "
            "prompt field, a rename or a model the base does not already carry."
        )
    arms = (Arm(base, seed), Arm(base, seed + 1) if variant is None else Arm(variant, seed))
    hops = [
        CycleHop(campaign_id=campaign.campaign_id, cycle_id=cycle_dir.name)
        for campaign in stores.campaigns.list_campaigns(dataset_name, lifecycle="all")
        if not campaign_ids or campaign.campaign_id in campaign_ids
        for cycle_dir in stores.campaigns.campaign_cycle_dirs(campaign.campaign_id)
    ]
    if max_usd is None:
        with spending_under(_RefusingBook()) as dry:
            return await _grade(
                stores, dataset_name, hops, arms, dry, cells=cells, parallel=parallel
            )
    # A decision answers for no one campaign, so no producer can be live on what it bills.
    async with paid_verb(stores=stores, bucket="decision-bank", hop=None) as book:
        admitted = book.declared
        book.declared = SpendCeilings(
            max_usd if admitted.usd is None else min(max_usd, admitted.usd), admitted.tokens
        )
        return await _grade(stores, dataset_name, hops, arms, book, cells=cells, parallel=parallel)


async def _grade(
    stores: Stores,
    dataset_name: str,
    hops: Sequence[CycleHop],
    arms: tuple[Arm, Arm],
    book: SpendBook,
    *,
    cells: int | None,
    parallel: int,
) -> DecisionBankOutcome:
    dry = book if isinstance(book, _RefusingBook) else None
    skipped: dict[str, int] = {}
    decisions: list[Decision] = []
    pairs: list[tuple[ArmLifts, ArmLifts]] = []
    stopped: str | None = None
    # A decision answers for no one campaign, so its bills land on the workspace's ledger.
    with diagnostic_trace(stores, None):
        # Every arm proposes before any is measured, or a banked row reaches a later panel.
        frozen = [
            point
            for hop in hops
            for point in await _freeze_cycle(
                stores, hop, arms, dry, skipped, cells=cells, parallel=parallel
            )
        ]
        if not frozen:
            raise DecisionBankError(
                f"no decision of {dataset_name!r} re-derives off disk ({skipped or 'none recorded'})."
            )
        for point in frozen:
            decision = Decision(
                hop=point.hop,
                round_num=point.round_num,
                n_cells=len(point.cells),
                cell_usd=point.cell_usd,
                cell_usd_measured=point.cell_usd_measured,
            )
            if dry is not None:
                decisions.append(replace(decision, plans=(_plan(point, 0), _plan(point, 1))))
                continue
            try:
                readings = (await _measure(point, 0), await _measure(point, 1))
            except DecisionBankError as stop:
                # Whole decisions only: a pair one arm half-measured compares nothing.
                stopped = str(stop)
                break
            pairs.append(readings)
            decisions.append(replace(decision, readings=readings))
            logger.info(
                "%s round %d: best lift %+.3f / %+.3f",
                point.hop.campaign_id,
                point.round_num,
                readings[0].best_lift,
                readings[1].best_lift,
            )
    if dry is not None:
        return DecisionBankOutcome(dataset_name, arms, decisions, skipped, None, None, None)
    if not pairs:
        raise DecisionBankError(f"no decision was measured whole: {stopped}")
    reading = read_bank(pairs)
    path = stores.diagnostic_runs.sidecar_path(
        f"decision-bank-{dataset_name}-{utcnow_iso()[:19].replace(':', '')}.json"
    )
    write_json(
        path,
        {
            "ts": utcnow_iso(),
            "dataset": dataset_name,
            "arms": [{"overrides": arm.overrides, "seed": arm.seed} for arm in arms],
            "max_usd": book.ceiling.usd,
            "usd_metered": book.usd_metered,
            "stopped": stopped,
            "skipped": skipped,
            "reading": reading,
            "decisions": [d.as_dict() for d in decisions],
        },
    )
    logger.info("decision-bank: wrote %d decision(s) -> %s", len(decisions), path)
    return DecisionBankOutcome(dataset_name, arms, decisions, skipped, reading, stopped, str(path))
