from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.node_context import NodeContext, measure_as_parent
from promptpotter.application.intelligence.rasch import candidate_abilities
from promptpotter.application.optimizers.nodes import MeasuredRound, ReadArm
from promptpotter.application.scoring.arm_states import (
    conclude,
    measuring,
    read_breakage,
    refuse,
)
from promptpotter.application.scoring.metrics import arm_folds
from promptpotter.application.scoring.paired import (
    MemberRows,
    fresh_cells,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.query_loop import Walk, run_walks
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    close_walk,
    open_walk,
    reread_cells,
)
from promptpotter.application.scoring.selection import cells_read
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmKey,
    ArmName,
    CellSetName,
    MemberAddress,
)
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.results import (
    ArmFit,
    Concluded,
    Cut,
    arm_name,
    is_electable,
    is_leader_eligible,
    walked_short,
)
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.validators import BrokenSignal
from promptpotter.shared.measurement_context import (
    MeasuredPass,
    MeasurementRole,
    RoleScope,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.nodes import (
        CatchUp,
        Eliminator,
        Panel,
        Proposals,
        Race,
        RoundContext,
        Selector,
    )
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.paired_reading import PairedReading
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results import ArmRole, Measuring, Proposed
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule

logger = logging.getLogger(__name__)


async def rescore_parent(
    round_: RoundContext,
    scoring_set: list[Sample],
    *,
    role: ArmRole,
    force_fresh: bool = False,
) -> ReadArm:
    cycle = round_.cycle
    return await measure_as_parent(
        round_,
        cycle.parent,
        cycle.parent_point,
        scoring_set,
        role=role,
        force_fresh=force_fresh,
    )


async def measure_population(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    eliminator: Eliminator | None,
    selector: Selector,
) -> MeasuredRound:
    cycle = ctx.cycle
    arms, rows, cut = await _walk_population(
        ctx, population, panel, eliminator, keep_cut=selector.elects_partial
    )
    # Clamped so a tiny dataset stays electable.
    coverage_floor = min(cycle.config.optimization.elimination_n_min, len(panel.cells))
    seat: NodeContext[Any] = NodeContext(ctx, selector)
    # Cold too: a selector elects on that θ, and the round's `AbilityReading.caveat` names it.
    ruler = cycle.difficulty.ruler

    def readable(key: ArmKey, parent: CellSheet) -> bool:
        answered = {cell.key for cell in parent.scoreable}
        return cells_read(selector.elects_on, rows[key], answered, ruler) >= coverage_floor

    # The REPLICATION cohort, deliberately the looser predicate: the election admits on `is_electable`.
    scored = [key for key, arm in arms.items() if is_leader_eligible(arm)]
    cells = selector.parent_cells(seat, panel, rows)
    if cut is not None:
        # The spent budget buys the parent no cell: it is read where the archive already holds it.
        held = reread_cells(cycle.parent_point, cells, cycle.session, label=MeasurementRole.PARENT)
        on_disk = held.by_key()
        cells = [sample for sample in cells if sample.key in on_disk]
        # Nothing the cut left can be elected, so the round is unwound as an unkept cut is.
        if not cells or not any(readable(key, held) for key in rows):
            raise StopLoop(cut)
    parent = await rescore_parent(ctx, cells, role=MeasurementRole.PARENT)
    # Every cell the round bought joins the δ fit before a θ is read: a ruler carrying only cells
    # from earlier rounds reads each arm on the handful it shares with them.
    cycle.difficulty.link(
        [(arm.sp_hash, rows[key]) for key, arm in arms.items() if arm.sp_hash]
        + [(parent.state.sp_hash, parent.rows)]
    )
    ruler = cycle.difficulty.ruler
    # Never in a `finally`: an unwound round did not score. Round 0's is `round.py::emit_origin_round`.
    cycle.session.state.control.spend_sample_lookahead()
    # The shared anchor's single-draw noise is correlated across arms, so it favours none.
    pair = ReferencePairs.of_round(
        ctx,
        arms,
        declared=[cell.key for cell in parent.rows]
        if cycle.config.optimization.lift_reference == "best_so_far"
        else [sample.key for sample in panel.cells],
    )
    readings = await _reference_readings(ctx, panel, arms, rows, scored, parent, pair)
    rejects = eliminator is not None and eliminator.stop_disqualifies
    electable: list[ArmKey] = []
    for key in scored:
        arm = arms[key]
        # A collapsed arm is read above and still refused entry — it keeps its reading.
        if not is_electable(arm, rows[key].cells):
            continue
        if rejects and isinstance(arm, Cut) and arm.kind == "eliminated":
            continue
        # An arm the selector cannot read for a reason other than elimination: an operator skip.
        if not readable(key, parent.rows):
            continue
        electable.append(key)

    # Fixed δ decouples the arms, so this is the fit `elect_round_winner` takes of the same rows.
    abilities = candidate_abilities({key: rows[key] for key in electable}, parent.rows, ruler)
    fit = {
        key: ArmFit(
            theta=abilities.theta.get(key),
            theta_se=abilities.theta_se.get(key),
            theta_caveat=cycle.difficulty.scale.caveat(rows[key].cells),
            vs_reference=readings.get(key),
        )
        for key in arms
    }
    filed = {key: ctx.walks[key].arm for key in arms} | {
        key: walk.arm for key, walk in ctx.walks.items() if key not in arms
    }
    return MeasuredRound(
        arms=arms,
        rows=rows,
        parent=parent,
        filed=filed,
        folds=arm_folds(
            {key: read.state for key, read in filed.items()},
            {key: read.rows for key, read in filed.items()},
            cycle.session.pipeline_schema,
        ),
        fit=fit,
        electable=electable,
        coverage_floor=coverage_floor,
        cut=cut,
    )


async def _reference_readings(
    ctx: RoundContext,
    panel: Panel,
    arms: Mapping[ArmKey, Concluded],
    rows: Mapping[ArmKey, CellSheet],
    scored: Sequence[ArmKey],
    parent: ReadArm,
    pair: ReferencePairs,
) -> dict[ArmKey, PairedReading]:
    """Only a SCORED arm buys its parents a cell; any other is read on what those already bought."""
    cycle = ctx.cycle
    read = {key: ReadArm(key, arm, rows[key]) for key, arm in arms.items() if key in rows}
    if cycle.config.optimization.lift_reference == "best_so_far":
        return {key: pair.read(arm, parent.key, parent) for key, arm in read.items()}

    cells_of = {key: {cell.key for cell in sheet} for key, sheet in rows.items()}
    needed: dict[str, set[str]] = {}
    for key in scored:
        for pid in arms[key].individual.lineage.parent_ids:
            needed.setdefault(pid, set()).update(cells_of[key])
    # This round's individuals are not banked yet; every earlier one is, in a closed round.
    populated = {arm.individual.id: arm.individual for arm in arms.values()}
    for pid, keys in needed.items():
        if ArmKey(pid) == parent.key:
            continue
        individual = populated.get(pid) or cycle.measured(pid).individual
        await measure_as_parent(
            ctx,
            individual,
            cycle.points.job(individual),
            [s for s in panel.cells if s.key in keys],
            role=MeasurementRole.PARENT,
        )
    walks = ctx.walks

    def reference(pid: ArmKey, key: ArmKey) -> ReadArm | None:
        if (walk := walks.get(pid)) is None:
            return None
        rows = walk.arm.rows.where(lambda cell: cell.key in cells_of[key])
        return ReadArm(pid, walk.arm.state, rows)

    def against_better(key: ArmKey) -> tuple[ArmKey, PairedReading] | None:
        by_parent = {
            ArmKey(pid): pair.read(read[key], ArmKey(pid), reference(ArmKey(pid), key))
            for pid in read[key].individual.lineage.parent_ids
        }
        if not by_parent:
            return None

        def level(pid: ArmKey) -> float:
            lift = by_parent[pid].lift("fitness")
            return -math.inf if lift is None else lift.rate_a

        # `max` keeps the first of a tie, so a tie falls to `parent_ids` order.
        better = max(by_parent, key=level)
        return better, by_parent[better]

    read_against = {key: against for key in read if (against := against_better(key)) is not None}
    return {key: reading for key, (_, reading) in read_against.items()}


@dataclass(frozen=True, kw_only=True)
class ReferencePairs:
    path: tuple[CycleHop, ...]
    instrument_id: str
    scorer_id: str
    dataset_hash: str
    declared: Sequence[str]
    names: Mapping[ArmKey, ArmName]

    @classmethod
    def of_round(
        cls,
        ctx: RoundContext,
        arms: Mapping[ArmKey, Concluded],
        *,
        declared: Sequence[str],
    ) -> ReferencePairs:
        session = ctx.cycle.session
        names = {
            key: arm_name(key, arm, rr.round)
            for rr in ctx.cycle.rounds
            for key, arm in rr.arms.items()
        }
        for key, arm in arms.items():
            names.setdefault(key, arm_name(key, arm, ctx.round_num))
        return cls(
            path=(session.hop,),
            instrument_id=session.instrument_id,
            scorer_id=session.state.scoring.require_scorer().id,
            dataset_hash=session.bank.dataset_hash,
            declared=declared,
            names=names,
        )

    def read(self, arm: ReadArm, against: ArmKey, reference: ReadArm | None) -> PairedReading:
        """*reference* is ``None`` where the round never read the individual *against* names."""
        return read_pair(
            a=self._member(
                against,
                MeasurementRole.PARENT if reference is None else reference.state.role,
                NO_CELLS if reference is None else reference.rows,
                cut=reference is not None and walked_short(reference.state),
            ),
            b=self._member(arm.key, arm.state.role, arm.rows, cut=walked_short(arm.state)),
            cell_set=CellSetName.REFERENCE_CELLS,
            cells=self.declared,
            masked=False,
            dataset_hash=self.dataset_hash,
            measurands=grade_measurands(self.scorer_id),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.DECISION,
            instrument_id=self.instrument_id,
        )

    def _member(
        self,
        key: ArmKey,
        role: MeasurementRole,
        rows: CellSheet,
        *,
        cut: bool,
    ) -> MemberRows:
        return MemberRows(
            address=MemberAddress(
                path=self.path,
                individual_id=key,
                arm=self.names.get(key),
                pass_role=role,
            ),
            sheet=rows,
            bought=fresh_cells(rows),
            cut=cut,
            scope=RoleScope.DECISION,
            instrument_id=self.instrument_id,
            dataset_hash=self.dataset_hash,
            cell_set_id=None,
        )


async def _walk_population(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    eliminator: Eliminator | None,
    *,
    keep_cut: bool,
) -> tuple[dict[ArmKey, Concluded], dict[ArmKey, CellSheet], StopReason | None]:
    cycle = ctx.cycle
    callbacks = ctx.callbacks
    round_num = ctx.round_num
    schema = cycle.session.pipeline_schema
    proposed = population.arms(round_num)
    rows: dict[ArmKey, CellSheet] = {}
    concluded: dict[ArmKey, Concluded] = {}

    race: Race | None = (
        eliminator.race(NodeContext(ctx, eliminator), panel, population, partial(_catch_up, cycle))
        if eliminator is not None
        else None
    )
    labels = {key: arm.label for key, arm in proposed.items()}
    order = [int(s.id) for s in panel.order]
    sps = {key: cycle.points.job(arm.individual) for key, arm in proposed.items()}
    asked = {key: ctx.asked(key, panel.order) for key in proposed}
    states = {
        key: measuring(arm, sps[key], schema, expected=len(asked[key]), sample_order=order)
        for key, arm in proposed.items()
    }
    session = cycle.session
    skipped = session.store.campaigns.standing_rounds(session.hop, moment=None).skipped
    walks: dict[ArmKey, Walk | None] = {}
    for key, arm in proposed.items():
        checks: list[StopRule] = list(cycle.session.state.scoring.degradation_checks)
        # The arms before this one still being walked may yet become priors.
        rule = race.rule(list(walks.items())) if race is not None else None
        if rule is not None:
            checks.append(rule)
        walks[key] = _open_arm(
            ctx, arm, sps[key], panel.order, checks, skipped.get((round_num, key))
        )

    blocks = race.blocks if race is not None else None

    def on_turn(key: ArmKey, block: int | None) -> None:
        if race is not None:
            race.open_turn(key)
        states[key] = states[key].model_copy(
            update={
                "n_priors": race.n_priors if race is not None else 0,
                "block": None
                if block is None or blocks is None
                else {
                    "n": block + 1,
                    "of": -(-len(order) // blocks.block_size),
                    "size": blocks.block_size,
                    "racing": sum(1 for w in walks.values() if w is not None and w.outcome is None),
                },
            }
        )
        callbacks.on_arm(round_num, key, states[key])

    def on_decided(key: ArmKey) -> None:
        arm, results = _conclude_arm(ctx, states[key], sps[key], walks[key], key, race, labels)
        rows[key] = results
        concluded[key] = arm
        ctx.walked(ReadArm(key, arm, results), asked[key])
        callbacks.on_arm(round_num, key, arm)

    cut = await run_walks(
        walks,
        cycle.session,
        keep_cut=keep_cut,
        backfills=race,
        blocks=blocks,
        on_turn=on_turn,
        on_decided=on_decided,
    )
    ranked = [key for key in proposed if key in concluded]
    return {key: concluded[key] for key in ranked}, {key: rows[key] for key in ranked}, cut


def _catch_up(cycle: Cycle, sp: JobSearchPoint, sample: Sample, prior: ArmKey) -> CatchUp:
    # No stop rule: it recurses into the eliminator.
    walk = open_walk(
        sp,
        [sample],
        cycle.session,
        label=MeasurementRole.BACKFILL,
        sample_index=cycle.sample_index,
        # The PRIOR being caught up, never the arm whose cell triggered it.
        measured=MeasuredPass(prior, MeasurementRole.BACKFILL),
    )
    walk.release()
    _, cell = walk.launch(1)

    def commit() -> Sequence[GradedCell]:
        walk.collect()
        walk.end(walk.take(cell) or walk.judge(), cancel=True)
        return close_walk(walk).sheet.cells

    def discard() -> None:
        # Collected first, so a cell already back releases its claim now rather than on a callback.
        walk.collect()
        walk.end(None, cancel=True)

    return cell, commit, discard


def _open_arm(
    ctx: RoundContext,
    arm: Proposed,
    sp: JobSearchPoint,
    dataset: list[Sample],
    checks: list[StopRule],
    skip_at: int | None,
) -> Walk | None:
    if arm.refused:
        return None
    return open_walk(
        sp,
        dataset,
        ctx.cycle.session,
        label=MeasurementRole.PANEL,
        checks=checks,
        sample_index=ctx.cycle.sample_index,
        # Handed to the gateway rather than bound here, so no re-entrant asker inherits it.
        measured=MeasuredPass(arm.individual.id, MeasurementRole.PANEL),
        skip_at=skip_at,
        held=ctx.held(ArmKey(arm.individual.id)),
    )


def _conclude_arm(
    ctx: RoundContext,
    arm: Measuring,
    sp: JobSearchPoint,
    walk: Walk | None,
    key: ArmKey,
    race: Race | None,
    labels: Mapping[ArmKey, str],
) -> tuple[Concluded, CellSheet]:
    if walk is None:
        return refuse(arm), NO_CELLS

    scored = close_walk(walk)
    results, signal = scored.sheet, scored.signal
    elimination = (
        race.judge(signal, key=key, results=results.cells, labels=labels)
        if race is not None
        else None
    )
    breakage = (
        read_breakage(
            signal,
            effective_pipeline_params=arm.individual.pipeline_params,
            round_num=ctx.round_num,
            changes_description=arm.changes_description,
        )
        if isinstance(signal, BrokenSignal)
        else None
    )
    # Every cell taken, and not a walk the gateway gave up on, whose rows are synthetic.
    gave_up = signal is not None and signal.check_name == SCORING_ERROR_ABORT
    if race is not None and scored.stopped is None and not gave_up:
        race.admit(key, results.cells, sp)
    return conclude(arm, scored, elimination=elimination, breakage=breakage), results
