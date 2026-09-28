"""The round's measurement — the manifest's one ``measurement`` node, and the bench's: every arm
walked through the scoring gateway on the sampler's panel under the eliminator's race, the parent
re-scored on the same panel, each arm read against it, and the ruler extended over every cell."""

from __future__ import annotations

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
from promptpotter.application.scoring.metrics import matched_parent_stats
from promptpotter.application.scoring.query_loop import Walk, run_walks
from promptpotter.application.scoring.search_point_scorer import (
    SCORING_ERROR_ABORT,
    close_walk,
    open_walk,
)
from promptpotter.application.scoring.selection import distinct_valid_cells, matched_parent_lift
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
    from promptpotter.application.optimizers.nodes import (
        CatchUp,
        Eliminator,
        Panel,
        Population,
        Race,
        RoundContext,
    )
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog

__all__ = ["measure_population"]


async def measure_population(
    ctx: RoundContext,
    population: Population,
    panel: Panel,
    eliminator: Eliminator | None,
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
    # On the round's WHOLE panel, never the cells the arms reached: a held round's headline IS
    # this re-score, and a narrower set hands it the denominator of whatever the eliminator cut.
    parent = await rescore_parent(cycle, panel.cells, callbacks=ctx.callbacks)
    # Spent where the round's scoring ends, never in a `finally` (an unwound round did not score);
    # round 0 spends it in `round.py::emit_origin_round`, and the two cannot fire for one round.
    if cycle.session.sample_lookahead_consume is not None:
        cycle.session.sample_lookahead_consume()
    # The shared comparison anchor. Its single-draw noise is correlated across arms, so it floods
    # every comparison equally rather than favouring one.
    parent_rows = list(cast("list[QueryMeasurement]", parent.results))
    reference_id = parent.opt_sp.lineage.id
    # Clamped so a tiny dataset stays electable.
    coverage_floor = min(cycle.config.optimization.elimination_n_min, len(panel.cells))
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(scores)}
    electable: list[str] = []
    for ind in scored:
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is None:
            continue
        cand_rows = rows[ind.lineage.id]
        matched = matched_parent_stats(parent_rows, cand_rows, schema)
        # Unconditional on ``matched``: the lift is defined on the cells both reached, so a
        # truncated arm gets an honest (wider) interval instead of nothing.
        lift = matched_parent_lift(cand_rows, parent_rows)
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
        electable.append(ind.lineage.id)

    # Before the selector, which reads it: the ≥2-arm floor is satisfiable now, and on return the
    # ruler covers every cell above, so a θ fit raises on a hole instead of grading it δ=0.
    cycle.calibrate_ruler({**rows, PARENT_ABILITY_ID: parent_rows})
    return Measured(
        rows=rows,
        scores=scores,
        scored=scored,
        parent=parent,
        parent_rows=parent_rows,
        electable=electable,
        coverage_floor=coverage_floor,
    )


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
    scores: list[ScoredCandidate] = []

    def catch_up(sp: JobSearchPoint, sample: Sample, prior_id: str) -> CatchUp:
        # No display callbacks, which would mint a bogus `C{round}.0` row, and no stop rule, which
        # would recurse into the eliminator; the row is written when the commit takes it.
        walk = open_walk(
            sp,
            [sample],
            cycle.session,
            label=MeasurementRole.BACKFILL,
            axes=cycle.axes,
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

        return cell, commit

    race: Race | None = eliminator.race(ctx, panel, catch_up) if eliminator is not None else None
    ids = [ind.lineage.id for ind in population.individuals]
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
        if race is not None:
            # The candidates before this one still being walked may yet become priors.
            checks.append(race.rule(list(zip(ids, walks, strict=False))))
        walk = _open_candidate(ctx, idx, n, proposals[idx], sps[idx], panel.order, checks)
        if walk is not None:
            walk.skip_at = skips.get(ids[idx])
        walks.append(walk)

    def on_turn(idx: int) -> None:
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
            {cs.candidate_id: cs.label for cs in scores},
        )
        rows[ids[idx]] = results
        if report.runtime_failures:
            proposal.runtime_failures = [*proposal.runtime_failures, *report.runtime_failures]
        scores.append(report)
        callbacks.on_candidate_scored(idx, n, report.model_dump())

    await run_walks(walks, cycle.session, backfills=race, on_turn=on_turn, on_decided=on_decided)
    return rows, scores


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
        axes=ctx.cycle.axes,
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
    elimination_context = (
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
        elimination_context=dict(elimination_context) if elimination_context else None,
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
