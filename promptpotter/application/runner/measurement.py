"""The round's measurement — the manifest's one ``measurement`` node, and the bench's: every arm
walked through the scoring gateway on the sampler's panel under the eliminator's race, the parent
re-scored on the same panel, each arm read against it, and the ruler extended over every cell."""

from __future__ import annotations

import math
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.exploration import PARENT_ABILITY_ID
from promptpotter.application.optimizers.nodes import Measured
from promptpotter.application.origin import rescore_parent
from promptpotter.application.scoring.candidate_report import (
    INVALID_SCORES,
    build_score_report,
    fatal_validation_failures,
    read_breakage,
    walk_outcome,
)
from promptpotter.application.scoring.classification import scoreable_rows
from promptpotter.application.scoring.metrics import matched_parent_stats
from promptpotter.application.scoring.query_loop import Walk, run_walks
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    close_walk,
    open_walk,
    score_search_point,
)
from promptpotter.application.scoring.selection import (
    distinct_valid_cells,
    matched_parent_lift,
    paired_fitness,
)
from promptpotter.domain.results import (
    ArmOutcome,
    CandidateProposal,
    ScoredCandidate,
    candidate_label,
    is_electable,
    is_leader_eligible,
)
from promptpotter.domain.run_records import SnapshotRecord
from promptpotter.shared.instrument import NO_ROUND_SLOT, MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.nodes import (
        CatchUp,
        Eliminator,
        Panel,
        Population,
        Race,
        RoundContext,
    )
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import ReferenceReading
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = ["measure_as_parent", "measure_population"]


async def measure_population(
    ctx: RoundContext,
    population: Population,
    panel: Panel,
    eliminator: Eliminator | None,
    *,
    reads_parent: bool,
) -> Measured:
    cycle = ctx.cycle
    schema = cycle.session.pipeline_schema
    assert schema is not None, "the measurement requires pipeline_schema"
    rows, scores = await _walk_population(ctx, population, panel, eliminator)

    # The REPLICATION cohort, deliberately the looser predicate; admission to the election is
    # `is_electable` below, since collapse is a verdict on rows we would be about to add to.
    aborted_ids = {cs.candidate_id for cs in scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in population.individuals
        if ind.lineage.id in rows and ind.lineage.id not in aborted_ids
    ]
    # On the round's WHOLE panel for a selector that reads it: a held round's headline IS this
    # re-score, and a narrower set hands it the denominator of whatever the eliminator cut.
    reached = {int(r["sample_id"]) for arm in rows.values() for r in arm}
    cells = panel.cells if reads_parent else [s for s in panel.cells if int(s.id) in reached]
    parent = await rescore_parent(cycle, cells, callbacks=ctx.callbacks)
    # Spent where the round's scoring ends, never in a `finally` (an unwound round did not score);
    # round 0 spends it in `round.py::emit_origin_round`, and the two cannot fire for one round.
    if cycle.session.sample_lookahead_consume is not None:
        cycle.session.sample_lookahead_consume()
    # The shared comparison anchor. Its single-draw noise is correlated across arms, so it floods
    # every comparison equally rather than favouring one.
    parent_rows = list(cast("list[QueryMeasurement]", parent.results))
    read_against, references = await _lift_references(ctx, population, panel, rows, scored, parent)
    # Clamped so a tiny dataset stays electable.
    coverage_floor = min(cycle.config.optimization.elimination_n_min, len(panel.cells))
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(scores)}
    electable: list[OptSearchPoint] = []
    for ind in scored:
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is None:
            continue
        cand_rows = rows[ind.lineage.id]
        if ind.lineage.id in read_against:
            reference_id, reference_rows = read_against[ind.lineage.id]
            matched = matched_parent_stats(reference_rows, cand_rows, schema)
            # Unconditional on ``matched``: the lift is defined on the cells both reached, so a
            # truncated arm gets an honest (wider) interval instead of nothing.
            lift = matched_parent_lift(cand_rows, reference_rows, grade="fitness")
            scores[cs_idx] = scores[cs_idx].model_copy(
                update={
                    "reference_id": reference_id,
                    "reference_accuracy": matched["accuracy"] if matched else None,
                    "reference_composite": matched["composite_fitness"] if matched else None,
                    "reference_lift": lift[0] if lift else None,
                    "reference_lift_ci_lo": lift[1] if lift else None,
                    "reference_lift_ci_hi": lift[2] if lift else None,
                }
            )
        # A collapsed arm is read here and still refused entry — it keeps its matched stamp.
        if not is_electable(scores[cs_idx], cand_rows):
            continue
        # The COVERAGE half: an arm under the floor cannot be selected. It catches an arm thin for
        # a reason other than elimination, an operator skip.
        if distinct_valid_cells(cand_rows) < coverage_floor:
            continue
        electable.append(ind)

    # Before the selector, which reads it: the ≥2-arm floor is satisfiable now, and on return the
    # ruler covers every cell above, so a θ fit raises on a hole instead of grading it δ=0.
    cycle.calibrate_ruler({**rows, PARENT_ABILITY_ID: parent_rows})
    return Measured(
        rows=rows,
        scores=scores,
        scored=scored,
        parent=parent,
        parent_rows=parent_rows,
        references=references,
        electable=electable,
        coverage_floor=coverage_floor,
    )


async def _lift_references(
    ctx: RoundContext,
    population: Population,
    panel: Panel,
    rows: dict[str, list[QueryMeasurement]],
    scored: list[OptSearchPoint],
    parent: ReferenceReading,
) -> tuple[dict[str, tuple[str, list[QueryMeasurement]]], dict[str, list[QueryMeasurement]]]:
    """Each scored arm's reference under ``lift_reference`` and the rows it is read on, then every
    reference's rows as the round banks them."""
    cycle = ctx.cycle
    parent_rows = list(cast("list[QueryMeasurement]", parent.results))
    bar_id = parent.opt_sp.lineage.id
    if cycle.config.optimization.lift_reference == "best_so_far":
        return {ind.lineage.id: (bar_id, parent_rows) for ind in scored}, {bar_id: parent_rows}

    cells_of = {
        ind.lineage.id: {int(r["sample_id"]) for r in rows[ind.lineage.id]} for ind in scored
    }
    needed: dict[str, set[int]] = {}
    for ind in scored:
        for pid in ind.lineage.parent_ids:
            needed.setdefault(pid, set()).update(cells_of[ind.lineage.id])
    # This round's individuals are not banked yet; every earlier one is, in a closed round.
    populated = {
        ind.lineage.id: (ind, params)
        for ind, params in zip(population.individuals, population.pipeline_params, strict=True)
    }
    demo = cycle.session.scoring.require_partition().demo
    references: dict[str, list[QueryMeasurement]] = {bar_id: parent_rows}
    for pid, sids in needed.items():
        if pid == bar_id:
            continue
        if pid in populated:
            individual, params = populated[pid]
            sp = individual.to_job_search_point(
                base_pipeline_params=params,
                schema=cycle.session.pipeline_schema,
                framing=cycle.framing,
                demo=demo,
            )
        else:
            sp = cycle.searchpoint(pid)
        references[pid] = await measure_as_parent(
            ctx, sp, pid, [s for s in panel.cells if int(s.id) in sids]
        )

    read_against: dict[str, tuple[str, list[QueryMeasurement]]] = {}
    for ind in scored:
        cells = cells_of[ind.lineage.id]
        on_arm = {
            pid: [r for r in references[pid] if int(r["sample_id"]) in cells]
            for pid in ind.lineage.parent_ids
        }
        if on_arm:
            # `max` keeps the first of a tie, so a tie falls to `parent_ids` order.
            better = max(on_arm, key=lambda pid: _mean_on(rows[ind.lineage.id], on_arm[pid]))
            read_against[ind.lineage.id] = (better, on_arm[better])
    # The parent's rows are banked though no arm read against them: a selector may keep the parent
    # itself, and a later round or a resume reads its rows back off this round.
    named = {bar_id} | {pid for pid, _ in read_against.values()}
    return read_against, {pid: rs for pid, rs in references.items() if pid in named}


async def measure_as_parent(
    ctx: RoundContext, sp: JobSearchPoint, individual_id: str, cells: list[Sample]
) -> list[QueryMeasurement]:
    walked = await score_search_point(
        sp,
        cells,
        ctx.cycle.session,
        label=MeasurementRole.PARENT,
        sample_index=ctx.cycle.sample_index,
        on_sample_scored=partial(ctx.callbacks.on_sample_scored, NO_ROUND_SLOT, 0),
        on_sample_starting=partial(ctx.callbacks.on_sample_started, NO_ROUND_SLOT, 0),
        measured=MeasuredCandidate(
            idx=NO_ROUND_SLOT,
            candidate_id=individual_id,
            label=f"parent:{individual_id[:8]}",
            role=MeasurementRole.PARENT,
        ),
    )
    return walked.results


def _mean_on(arm_rows: list[QueryMeasurement], parent_rows: list[QueryMeasurement]) -> float:
    _, parent_fit = paired_fitness(
        scoreable_rows(arm_rows), scoreable_rows(parent_rows), grade="fitness"
    )
    return sum(parent_fit) / len(parent_fit) if parent_fit else -math.inf


async def _walk_population(
    ctx: RoundContext,
    population: Population,
    panel: Panel,
    eliminator: Eliminator | None,
) -> tuple[dict[str, list[QueryMeasurement]], list[ScoredCandidate]]:
    cycle = ctx.cycle
    callbacks = ctx.callbacks
    round_num = ctx.round_num
    proposals = population.proposals
    n = len(population.individuals)
    rows: dict[str, list[QueryMeasurement]] = {}
    reports: dict[int, ScoredCandidate] = {}

    race: Race | None = (
        eliminator.race(ctx, panel, partial(_catch_up, cycle)) if eliminator is not None else None
    )
    ids = [ind.lineage.id for ind in population.individuals]
    labels = {cid: candidate_label(round_num, idx) for idx, cid in enumerate(ids)}
    order = [int(s.id) for s in panel.order]
    # Single merge site: each candidate's frozen searchpoint, shared by the in-flight dashboard
    # seed (resolved config-only) and the candidate's walk and report.
    demo = cycle.session.scoring.require_partition().demo
    sps = [
        ind.to_job_search_point(
            base_pipeline_params=population.pipeline_params[idx],
            schema=cycle.session.pipeline_schema,
            framing=cycle.framing,
            demo=demo,
        )
        for idx, ind in enumerate(population.individuals)
    ]
    skips = _skips_on_record(cycle.session.state.ledger, round_num)
    walks: list[Walk | None] = []
    for idx in range(n):
        checks: list[StopRule] = list(cycle.session.scoring.degradation_checks)
        # The candidates before this one still being walked may yet become priors.
        rule = race.rule(list(zip(ids, walks, strict=False))) if race is not None else None
        if rule is not None:
            checks.append(rule)
        walk = _open_candidate(ctx, idx, n, proposals[idx], sps[idx], panel.order, checks)
        if walk is not None:
            walk.skip_at = skips.get(ids[idx])
        walks.append(walk)

    blocks = race.blocks if race is not None else None

    def on_turn(idx: int, block: int | None) -> None:
        if race is not None:
            race.open_turn(ids[idx], idx, n)
        # One call shared with the origin pass; the dashboard keeps the order as
        # `declared_sample_order`, so a reader that missed the event still sees forward.
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
            population.pipeline_params[idx],
            race,
            labels,
        )
        rows[ids[idx]] = results
        if report.runtime_failures:
            proposal.runtime_failures = [*proposal.runtime_failures, *report.runtime_failures]
        reports[idx] = report
        callbacks.on_candidate_scored(idx, n, report.model_dump())

    await run_walks(
        walks,
        cycle.session,
        backfills=race,
        blocks=blocks,
        on_turn=on_turn,
        on_decided=on_decided,
    )
    ranked = sorted(reports)
    return {ids[i]: rows[ids[i]] for i in ranked}, [reports[i] for i in ranked]


def _catch_up(cycle: Cycle, sp: JobSearchPoint, sample: Sample, prior_id: str) -> CatchUp:
    # No display callbacks, which would mint a bogus `C{round}.0` row, and no stop rule, which
    # would recurse into the eliminator; the row is written when the commit takes it.
    walk = open_walk(
        sp,
        [sample],
        cycle.session,
        label=MeasurementRole.BACKFILL,
        sample_index=cycle.sample_index,
        on_sample_scored=None,
        on_sample_starting=None,
        # The PRIOR being caught up, never the arm whose cell triggered it; ``role`` marks
        # the row as measured for a paired comparison, outside the round's shared order.
        measured=MeasuredCandidate(
            idx=NO_ROUND_SLOT,
            candidate_id=prior_id,
            label=f"prior:{prior_id[:8]}",
            role=MeasurementRole.BACKFILL,
        ),
    )
    walk.release()
    _, cell = walk.launch(1, None)

    def commit() -> list[QueryMeasurement]:
        walk.collect()
        walk.end(walk.take(cell) or walk.judge(), cancel=True)
        return close_walk(walk).results

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
) -> Walk | None:
    if fatal_validation_failures(proposal.validation_failures):
        return None
    callbacks = ctx.callbacks
    return open_walk(
        candidate_sp,
        dataset,
        ctx.cycle.session,
        label=MeasurementRole.PANEL,
        on_sample_scored=partial(callbacks.on_sample_scored, idx, n),
        on_sample_starting=partial(callbacks.on_sample_started, idx, n),
        checks=checks,
        sample_index=ctx.cycle.sample_index,
        # Handed to the gateway rather than bound here, so no re-entrant asker inherits it; the
        # L4 recursion stamps an inner campaign's provenance from it.
        measured=MeasuredCandidate(
            idx=idx,
            candidate_id=proposal.opt_sp.lineage.id,
            label=candidate_label(ctx.round_num, idx),
            role=MeasurementRole.PANEL,
        ),
    )


def _conclude_candidate(
    ctx: RoundContext,
    idx: int,
    proposal: CandidateProposal,
    candidate_sp: JobSearchPoint,
    walk: Walk | None,
    dataset: list[Sample],
    effective_pipeline_params: dict[str, Any] | None,
    race: Race | None,
    labels: dict[str, str],
) -> tuple[ScoredCandidate, list[QueryMeasurement]]:
    opt_sp_c = proposal.opt_sp
    label = candidate_label(ctx.round_num, idx)
    pipeline_overlay = proposal.pipeline_overlay or None
    # Off `candidate_sp` — the SAME object the gateway hands `build_dataset_run_data`, so the id the
    # report carries and the `prompt_fields_id` the rows are keyed on are one computation.
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
            run_id=None,
            outcome=ArmOutcome.INVALID,
            resolved_pipeline_params=candidate_sp.config_params,
        )
        return report, []

    scored = close_walk(walk)
    results, signal = scored.results, scored.signal
    elimination = (
        race.judge(signal, candidate_id=opt_sp_c.lineage.id, results=results, labels=labels)
        if race is not None
        else None
    )
    breakage = (
        read_breakage(
            signal,
            results=results,
            effective_pipeline_params=effective_pipeline_params,
            round_num=ctx.round_num,
            candidate_label=opt_sp_c.lineage.changes_description or "",
        )
        if signal is not None and signal.outcome is ArmOutcome.BROKEN
        else None
    )
    # Every cell taken, and not a walk the gateway gave up on, whose rows are synthetic.
    gave_up = signal is not None and signal.check_name == SCORING_ERROR_ABORT
    if race is not None and scored.stopped is None and not gave_up:
        race.admit(opt_sp_c.lineage.id, results, candidate_sp)
    report = build_score_report(
        opt_sp_c,
        proposal.validation_failures,
        pipeline_overlay,
        scored.scores,
        results,
        dataset,
        label=label,
        sp_hash=sp_hash,
        run_id=scored.run_id,
        outcome=walk_outcome(scored),
        resolved_pipeline_params=candidate_sp.config_params,
        elimination_context=None if elimination is None else dict(elimination.context),
        elimination_reason=None if elimination is None else elimination.reason,
        breakage=breakage,
    )
    return report, results


def _skips_on_record(ledger: CycleEventLog | None, round_num: int) -> dict[str, int]:
    """The candidates of this round an operator skipped, and after how many rows. A skip is an
    input, not a measurement, so a round resumed after a stop cannot re-derive it — it reads the
    decision the ledger already carries, the last one per candidate. Keyed by candidate id, which
    only a resumed round restores: a rewound or regenerated round mints new ones, and drops the
    skips with the candidates they named."""
    skips: dict[str, int] = {}
    if ledger is None:
        return skips
    for _offset, rec in ledger.iter():
        if not isinstance(rec, SnapshotRecord) or rec.event != "candidate_scored":
            continue
        payload_scores = rec.payload.get("scores") or {}
        if rec.round != round_num or not (cid := payload_scores.get("candidate_id")):
            continue
        if payload_scores.get("outcome") == ArmOutcome.SKIPPED:
            skips[cid] = int(payload_scores.get("scored_samples") or 0)
        else:
            skips.pop(cid, None)
    return skips
