from __future__ import annotations

import logging
import math
from functools import partial
from typing import TYPE_CHECKING

from promptpotter.application.bench.node_context import NodeContext, measure_as_parent
from promptpotter.application.intelligence.rasch import candidate_abilities
from promptpotter.application.optimizers.nodes import Measured
from promptpotter.application.scoring.candidate_report import (
    arm_id,
    arm_theta_caveat,
    build_score_report,
    fatal_validation_failures,
    read_breakage,
    walk_outcome,
)
from promptpotter.application.scoring.metrics import INVALID_SCORES
from promptpotter.application.scoring.paired import (
    MemberRows,
    fresh_cells,
    grade_measurands,
    read_pair,
)
from promptpotter.application.scoring.query_loop import ArmSlot, Walk, run_walks
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    close_walk,
    open_walk,
    reread_cells,
    score_search_point,
)
from promptpotter.application.scoring.selection import distinct_valid_cells
from promptpotter.domain.paired_reading import (
    ROUND_LIFT_SPEC,
    ArmPointer,
    CellSetName,
    MemberAddress,
)
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.results import (
    ArmOutcome,
    CandidateProposal,
    ReferenceReading,
    ScoredCandidate,
    candidate_label,
    is_electable,
    is_leader_eligible,
)
from promptpotter.domain.run_records import CandidateScoredRecord
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.validators import BrokenSignal
from promptpotter.shared.hashing import dataset_hash
from promptpotter.shared.measurement_context import (
    NO_ROUND_SLOT,
    MeasuredCandidate,
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
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.paired_reading import PairedReading
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = ["measure_population", "rescore_parent"]


async def rescore_parent(
    cycle: Cycle,
    scoring_set: list[Sample],
    *,
    force_fresh: bool = False,
) -> ReferenceReading:
    session = cycle.session
    tr = cycle.tracking
    assert tr.current_sp is not None
    scored = await score_search_point(
        tr.current_sp,
        scoring_set,
        session,
        label=MeasurementRole.PARENT,
        sample_index=cycle.sample_index,
        # Ticked, not silenced: a silent whole-panel walk serves `between_samples` and reads as hung.
        slot=ArmSlot(NO_ROUND_SLOT, 0, cycle.opt_sp.id),
        measured=MeasuredCandidate(
            idx=0,
            candidate_id=cycle.opt_sp.id,
            label=cycle.rounds[-1].label,
            role=MeasurementRole.PARENT,
        ),
        force_fresh=force_fresh,
    )
    # A partial pass is kept: `read_pair` pairs each candidate on the cells both scored.
    if scored.stopped is not None:
        logger.warning(
            "Round parent %s stopped after %d/%d cells (%s); its floor covers only those.",
            cycle.rounds[-1].label,
            len(scored.sheet),
            len(scoring_set),
            scored.stopped,
        )
    return ReferenceReading(
        opt_sp=cycle.opt_sp,
        results=scored.sheet,
        # The gateway's OWN scores: re-running `compute_composite_fitness` drops the evaluators.
        report=build_score_report(
            cycle.opt_sp,
            (),
            None,
            scored.scores,
            scored.sheet.cells,
            scoring_set,
            # The parent INDIVIDUAL's label, off the round it won — it reaches disk.
            label=cycle.rounds[-1].label,
            sp_hash=tr.current_sp.sp_hash(session.pipeline_schema),
            outcome=walk_outcome(scored),
            resolved_pipeline_params=tr.current_sp.config_params,
        ),
    )


async def measure_population(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    eliminator: Eliminator | None,
    selector: Selector,
) -> Measured:
    cycle = ctx.cycle
    schema = cycle.session.pipeline_schema
    assert schema is not None, "the measurement requires pipeline_schema"
    rows, scores, cut = await _walk_population(
        ctx, population, panel, eliminator, keep_cut=selector.elects_partial
    )
    # Clamped so a tiny dataset stays electable.
    coverage_floor = min(cycle.config.optimization.elimination_n_min, len(panel.cells))
    if cut is not None and not any(
        distinct_valid_cells(arm) >= coverage_floor for arm in rows.values()
    ):
        # Nothing the cut left can be elected, so the round is unwound as an unkept cut is.
        raise StopLoop(cut)

    # The REPLICATION cohort, deliberately the looser predicate: the election admits on `is_electable`.
    aborted_ids = {cs.candidate_id for cs in scores if not is_leader_eligible(cs)}
    # By the arm's own id: a rejected proposal's individual can be a measured sibling's.
    scored = [
        proposal.opt_sp
        for idx, proposal in enumerate(population.proposals)
        if (cid := arm_id(proposal, ctx.round_num, idx)) in rows and cid not in aborted_ids
    ]
    cells = selector.parent_cells(NodeContext(ctx, selector.name), panel, rows)
    if cut is not None:
        # The spent budget buys the parent no cell: it is read where the archive already holds it.
        assert cycle.tracking.current_sp is not None
        cells = reread_cells(
            cycle.tracking.current_sp, cells, cycle.session, label=MeasurementRole.PARENT
        )
        if not cells:
            raise StopLoop(cut)
    parent = await rescore_parent(cycle, cells)
    # Never in a `finally`: an unwound round did not score. Round 0's is `round.py::emit_origin_round`.
    cycle.session.control.spend_sample_lookahead()
    # The shared anchor's single-draw noise is correlated across arms, so it favours none.
    parent_rows = parent.results
    pair = _ReferencePairs(
        ctx,
        scores,
        bar_cut=parent.report.outcome.ended_early,
        declared=[cell.key for cell in parent_rows]
        if cycle.config.optimization.lift_reference == "best_so_far"
        else [sample.key for sample in panel.cells],
    )
    readings, references = await _reference_readings(
        ctx, population, panel, rows, scores, scored, parent, pair
    )
    scores = [
        cs.model_copy(update={"vs_reference": reading})
        if (reading := readings.get(cs.candidate_id)) is not None
        else cs
        for cs in scores
    ]
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(scores)}
    rejects = eliminator is not None and eliminator.stop_disqualifies
    electable: list[OptSearchPoint] = []
    for ind in scored:
        cs_idx = cs_by_id.get(ind.id)
        if cs_idx is None:
            continue
        cand_rows = rows[ind.id]
        # A collapsed arm is read above and still refused entry — it keeps its reading.
        if not is_electable(scores[cs_idx], cand_rows.cells):
            continue
        if rejects and scores[cs_idx].outcome is ArmOutcome.ELIMINATED:
            continue
        # Catches an arm thin for a reason other than elimination: an operator skip.
        if distinct_valid_cells(cand_rows) < coverage_floor:
            continue
        electable.append(ind)

    # Before the selector, which reads it; the parent rides under its own id so the ruler links it.
    bar_id = parent.opt_sp.id
    cycle.calibrate_ruler({**rows, bar_id: rows.get(bar_id, NO_CELLS).merged(parent_rows)})
    if (ruler := cycle.difficulty.ruler) is not None:
        # Fixed δ decouples the arms, so this is the fit `elect_round_winner` takes of the same rows.
        abilities = candidate_abilities(
            {ind.id: rows[ind.id] for ind in electable}, parent_rows, ruler
        )
        scores = [
            cs.model_copy(
                update={
                    "theta": abilities.theta[cs.candidate_id],
                    "theta_se": abilities.theta_se[cs.candidate_id],
                }
            )
            if cs.candidate_id in abilities.theta
            else cs
            for cs in scores
        ]
        scores = [
            cs.model_copy(
                update={"theta_caveat": arm_theta_caveat(rows[cs.candidate_id].cells, ruler)}
            )
            if cs.candidate_id in rows
            else cs
            for cs in scores
        ]
    return Measured(
        rows=rows,
        scores=scores,
        scored=scored,
        parent=parent,
        parent_rows=parent_rows,
        references=references,
        electable=electable,
        coverage_floor=coverage_floor,
        cut=cut,
    )


async def _reference_readings(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    rows: dict[str, CellSheet],
    scores: Sequence[ScoredCandidate],
    scored: list[OptSearchPoint],
    parent: ReferenceReading,
    pair: _ReferencePairs,
) -> tuple[dict[str, PairedReading], dict[str, CellSheet]]:
    """Only a SCORED arm buys its parents a cell; any other is read on what those already bought."""
    cycle = ctx.cycle
    parent_rows = parent.results
    bar_id = parent.opt_sp.id
    arms = {cs.candidate_id: cs for cs in scores if cs.candidate_id in rows}
    if cycle.config.optimization.lift_reference == "best_so_far":
        return (
            {cid: pair.read(arm, rows[cid], bar_id, parent_rows) for cid, arm in arms.items()},
            {bar_id: parent_rows},
        )

    cells_of = {cid: {cell.sample_id for cell in arm_rows} for cid, arm_rows in rows.items()}
    needed: dict[str, set[int]] = {}
    for ind in scored:
        for pid in ind.lineage.parent_ids:
            needed.setdefault(pid, set()).update(cells_of[ind.id])
    # This round's individuals are not banked yet; every earlier one is, in a closed round.
    populated = {ind.id: ind for ind in population.individuals}
    demo = cycle.session.scoring.require_partition().demo
    references: dict[str, CellSheet] = {bar_id: parent_rows}
    for pid, sids in needed.items():
        if pid == bar_id:
            continue
        if pid in populated:
            sp = populated[pid].to_job_search_point(
                schema=cycle.session.pipeline_schema, framing=cycle.framing, demo=demo
            )
        else:
            sp = cycle.searchpoint(pid)
        references[pid] = await measure_as_parent(
            cycle, sp, pid, [s for s in panel.cells if int(s.id) in sids]
        )

    parents_of = {
        arm_id(proposal, ctx.round_num, idx): proposal.opt_sp.lineage.parent_ids
        for idx, proposal in enumerate(population.proposals)
    }

    def against_better(cid: str, held: Mapping[str, CellSheet]) -> tuple[str, PairedReading] | None:
        by_parent = {
            pid: pair.read(
                arms[cid],
                rows[cid],
                pid,
                held.get(pid, NO_CELLS).where(lambda cell: cell.sample_id in cells_of[cid]),
            )
            for pid in parents_of[cid]
        }
        if not by_parent:
            return None

        def level(pid: str) -> float:
            lift = by_parent[pid].lift("fitness")
            return -math.inf if lift is None else lift.rate_a

        # `max` keeps the first of a tie, so a tie falls to `parent_ids` order.
        better = max(by_parent, key=level)
        return better, by_parent[better]

    read_against = {
        ind.id: against
        for ind in scored
        if ind.id in arms and (against := against_better(ind.id, references)) is not None
    }
    for cid in arms:
        if cid not in read_against and (against := against_better(cid, references)) is not None:
            read_against[cid] = against
    # EVERY parent's rows are banked, the one an arm was not read against included.
    return {cid: reading for cid, (_, reading) in read_against.items()}, references


class _ReferencePairs:
    def __init__(
        self,
        ctx: RoundContext,
        scores: Sequence[ScoredCandidate],
        *,
        bar_cut: bool,
        declared: Sequence[str],
    ) -> None:
        session = ctx.cycle.session
        self._declared = declared
        self._path = (session.hop,)
        self._instrument = session.instrument_id
        self._scorer = session.scoring.require_scorer().id
        self._dataset = dataset_hash(session.samples)
        self._bar_id = ctx.cycle.opt_sp.id
        self._bar_cut = bar_cut
        # The arm each individual was FIRST measured as: a later round may read it again.
        self._arms = {
            cs.candidate_id: ArmPointer(
                round=rr.round, label=cs.label, candidate_id=cs.candidate_id
            )
            for rr in reversed(ctx.cycle.rounds)
            for cs in rr.candidate_scores
        }
        for cs in scores:
            self._arms.setdefault(
                cs.candidate_id,
                ArmPointer(round=ctx.round_num, label=cs.label, candidate_id=cs.candidate_id),
            )

    def read(
        self,
        arm: ScoredCandidate,
        arm_rows: CellSheet,
        reference_id: str,
        reference_rows: CellSheet,
    ) -> PairedReading:
        return read_pair(
            a=self._member(
                reference_id,
                MeasurementRole.PARENT,
                reference_rows,
                cut=self._bar_cut and reference_id == self._bar_id,
            ),
            b=self._member(
                arm.candidate_id,
                MeasurementRole.PANEL,
                arm_rows,
                cut=arm.outcome.ended_early,
            ),
            cell_set=CellSetName.REFERENCE_CELLS,
            cells=self._declared,
            masked=False,
            dataset_hash=self._dataset,
            measurands=grade_measurands(self._scorer),
            spec=ROUND_LIFT_SPEC,
            scope=RoleScope.DECISION,
            instrument_id=self._instrument,
        )

    def _member(
        self,
        individual_id: str,
        role: MeasurementRole,
        rows: CellSheet,
        *,
        cut: bool,
    ) -> MemberRows:
        return MemberRows(
            address=MemberAddress(
                path=self._path,
                individual_id=individual_id,
                arm=self._arms.get(individual_id),
                pass_role=role,
            ),
            sheet=rows,
            bought=fresh_cells(rows),
            cut=cut,
            scope=RoleScope.DECISION,
            instrument_id=self._instrument,
            dataset_hash=self._dataset,
            cell_set_id=None,
        )


async def _walk_population(
    ctx: RoundContext,
    population: Proposals,
    panel: Panel,
    eliminator: Eliminator | None,
    *,
    keep_cut: bool,
) -> tuple[dict[str, CellSheet], list[ScoredCandidate], StopReason | None]:
    cycle = ctx.cycle
    callbacks = ctx.callbacks
    round_num = ctx.round_num
    proposals = population.proposals
    n = len(population.individuals)
    rows: dict[str, CellSheet] = {}
    reports: dict[int, ScoredCandidate] = {}

    race: Race | None = (
        eliminator.race(
            NodeContext(ctx, eliminator.name), panel, population, partial(_catch_up, cycle)
        )
        if eliminator is not None
        else None
    )
    ids = [arm_id(proposal, round_num, idx) for idx, proposal in enumerate(proposals)]
    labels = {cid: candidate_label(round_num, idx) for idx, cid in enumerate(ids)}
    order = [int(s.id) for s in panel.order]
    demo = cycle.session.scoring.require_partition().demo
    sps = [
        ind.to_job_search_point(
            schema=cycle.session.pipeline_schema, framing=cycle.framing, demo=demo
        )
        for ind in population.individuals
    ]
    skips = _skips_on_record(cycle.session.state.ledger, round_num)
    walks: list[Walk | None] = []
    for idx in range(n):
        checks: list[StopRule] = list(cycle.session.scoring.degradation_checks)
        # The candidates before this one still being walked may yet become priors.
        rule = race.rule(list(zip(ids, walks, strict=False))) if race is not None else None
        if rule is not None:
            checks.append(rule)
        walks.append(
            _open_candidate(
                ctx, idx, n, proposals[idx], sps[idx], panel.order, checks, skips.get(ids[idx])
            )
        )

    blocks = race.blocks if race is not None else None

    def on_turn(idx: int, block: int | None) -> None:
        if race is not None:
            race.open_turn(ids[idx], idx, n)
        callbacks.announce_candidate(
            round_num,
            idx,
            n,
            opt_sp=population.individuals[idx],
            resolved_pipeline_params=sps[idx].config_params,
            sample_order=order,
            n_priors=race.n_priors if race is not None else 0,
            pipeline_overlay=proposals[idx].pipeline_overlay or None,
            block=None
            if block is None or blocks is None
            else {
                "n": block + 1,
                "of": -(-len(order) // blocks.block_size),
                "size": blocks.block_size,
                "racing": sum(1 for w in walks if w is not None and w.outcome is None),
            },
        )

    def on_decided(idx: int) -> None:
        proposal = proposals[idx]
        report, results = _conclude_candidate(
            ctx,
            idx,
            proposal,
            sps[idx],
            walks[idx],
            panel.order,
            ids[idx],
            race,
            labels,
        )
        rows[ids[idx]] = results
        if report.runtime_failures:
            proposal.runtime_failures = [*proposal.runtime_failures, *report.runtime_failures]
        reports[idx] = report
        callbacks.on_candidate_scored(idx, n, report)

    cut = await run_walks(
        walks,
        cycle.session,
        keep_cut=keep_cut,
        backfills=race,
        blocks=blocks,
        on_turn=on_turn,
        on_decided=on_decided,
    )
    ranked = sorted(reports)
    return {ids[i]: rows[ids[i]] for i in ranked}, [reports[i] for i in ranked], cut


def _catch_up(cycle: Cycle, sp: JobSearchPoint, sample: Sample, prior_id: str) -> CatchUp:
    # No slot (it mints a bogus `C{round}.0` row) and no stop rule (it recurses into the eliminator).
    walk = open_walk(
        sp,
        [sample],
        cycle.session,
        label=MeasurementRole.BACKFILL,
        sample_index=cycle.sample_index,
        # The PRIOR being caught up, never the arm whose cell triggered it.
        measured=MeasuredCandidate(
            idx=NO_ROUND_SLOT,
            candidate_id=prior_id,
            label=f"prior:{prior_id[:8]}",
            role=MeasurementRole.BACKFILL,
        ),
    )
    walk.release()
    _, cell = walk.launch(1, None)

    def commit() -> Sequence[GradedCell]:
        walk.collect()
        walk.end(walk.take(cell) or walk.judge(), cancel=True)
        return close_walk(walk).sheet.cells

    def discard() -> None:
        # Collected first, so a cell already back releases its claim now rather than on a callback.
        walk.collect()
        walk.end(None, cancel=True)

    return cell, commit, discard


def _open_candidate(
    ctx: RoundContext,
    idx: int,
    n: int,
    proposal: CandidateProposal,
    candidate_sp: JobSearchPoint,
    dataset: list[Sample],
    checks: list[StopRule],
    skip_at: int | None,
) -> Walk | None:
    if fatal_validation_failures(proposal.validation_failures):
        return None
    return open_walk(
        candidate_sp,
        dataset,
        ctx.cycle.session,
        label=MeasurementRole.PANEL,
        slot=ArmSlot(idx, n, proposal.opt_sp.id),
        checks=checks,
        sample_index=ctx.cycle.sample_index,
        # Handed to the gateway rather than bound here, so no re-entrant asker inherits it.
        measured=MeasuredCandidate(
            idx=idx,
            candidate_id=proposal.opt_sp.id,
            label=candidate_label(ctx.round_num, idx),
            role=MeasurementRole.PANEL,
        ),
        skip_at=skip_at,
    )


def _conclude_candidate(
    ctx: RoundContext,
    idx: int,
    proposal: CandidateProposal,
    candidate_sp: JobSearchPoint,
    walk: Walk | None,
    dataset: list[Sample],
    candidate_id: str,
    race: Race | None,
    labels: dict[str, str],
) -> tuple[ScoredCandidate, CellSheet]:
    opt_sp_c = proposal.opt_sp
    label = candidate_label(ctx.round_num, idx)
    pipeline_overlay = proposal.pipeline_overlay or None
    # Off the object the gateway hands `archive_entry`: one id for the report and its cells' filing.
    sp_hash = candidate_sp.sp_hash(ctx.cycle.session.pipeline_schema)

    if walk is None:
        report = build_score_report(
            opt_sp_c,
            proposal.validation_failures,
            pipeline_overlay,
            INVALID_SCORES,
            [],
            dataset,
            label=label,
            sp_hash=sp_hash,
            outcome=ArmOutcome.INVALID,
            resolved_pipeline_params=candidate_sp.config_params,
        )
        return report.model_copy(update={"candidate_id": candidate_id}), NO_CELLS

    scored = close_walk(walk)
    results, signal = scored.sheet, scored.signal
    elimination = (
        race.judge(signal, candidate_id=candidate_id, results=results.cells, labels=labels)
        if race is not None
        else None
    )
    breakage = (
        read_breakage(
            signal,
            effective_pipeline_params=opt_sp_c.pipeline_params,
            round_num=ctx.round_num,
            candidate_label=opt_sp_c.lineage.changes_description or "",
        )
        if isinstance(signal, BrokenSignal)
        else None
    )
    # Every cell taken, and not a walk the gateway gave up on, whose rows are synthetic.
    gave_up = signal is not None and signal.check_name == SCORING_ERROR_ABORT
    if race is not None and scored.stopped is None and not gave_up:
        race.admit(candidate_id, results.cells, candidate_sp)
    report = build_score_report(
        opt_sp_c,
        proposal.validation_failures,
        pipeline_overlay,
        scored.scores,
        results.cells,
        dataset,
        label=label,
        sp_hash=sp_hash,
        outcome=walk_outcome(scored),
        resolved_pipeline_params=candidate_sp.config_params,
        elimination_context=None if elimination is None else dict(elimination.context),
        elimination_reason=None if elimination is None else elimination.reason,
        breakage=breakage,
    )
    return report, results


def _skips_on_record(ledger: CycleEventLog | None, round_num: int) -> dict[str, int]:
    """Keyed by candidate id: a regenerated round proposes other individuals and drops their skips."""
    skips: dict[str, int] = {}
    if ledger is None:
        return skips
    for _offset, rec in ledger.iter():
        if not isinstance(rec, CandidateScoredRecord):
            continue
        if rec.round != round_num:
            continue
        if rec.scores.outcome == ArmOutcome.SKIPPED:
            skips[rec.scores.candidate_id] = rec.scores.scored_samples
        else:
            skips.pop(rec.scores.candidate_id, None)
    return skips
