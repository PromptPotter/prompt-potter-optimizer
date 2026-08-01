"""Round-boundary helpers — persist, close, post-round, escalation hook.

The ledger is the sole persistence ingress; display projections subscribe to it
(bypassing this seam collapses display + audit for the round)."""

from __future__ import annotations

import logging
from typing import Any

from promptpotter.application.bootstrap.session import Session
from promptpotter.application.config import CampaignConfig
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.escalation.firing import escalate_l2
from promptpotter.application.optimization.escalation.state import NextAction
from promptpotter.application.optimization.l1.critique import run_l1_critique
from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
from promptpotter.application.optimization.validators.l1_strict import (
    DROPPED_MANDATORY_PLACEHOLDER,
)
from promptpotter.application.output import (
    write_hard_samples_artifacts,
    write_log_md,
    write_review_md,
)
from promptpotter.application.run_observers import RunCallbacks
from promptpotter.domain.phases import StopLoop
from promptpotter.domain.results import RoundResult, is_round_winner
from promptpotter.domain.results_health import (
    assemble_prior_healths,
    compute_node_failure_rates,
    compute_round_health,
    evidence_starved_node,
)
from promptpotter.domain.run_records import PhaseRecord, ResumeCheckpointRecord
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.shared.errors import graceful

logger = logging.getLogger(__name__)


async def emit_origin_round(
    cycle: Cycle,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """Close **round 0** — the origin's measurement — through the standard path.

    The round itself is ``cycle.rounds[0]``, built by ``Cycle.start`` like any other
    round is built by the L1 path. This runs the two things a round does at its close
    and nothing else: seed the next round's critique, then ``close_round``, so the
    public round file (``rounds/round_0000.json``), the ``index.json::rounds[]`` entry,
    and the live ``dashboard.json::rounds[]`` summary all carry it in one shape.

    Like every ``close_round`` caller it does **not** feed escalation (only
    ``post_round`` observes). Idempotent at the call site: the loop guards on the
    round-0 file already existing.
    """
    round_result = cycle.origin_round
    # Seed round 1's L1 with a critique over the origin's misses. The loop runs
    # critique only at round end (``l1/execute.py``), so without this seed round 1
    # opens blind — it never sees the per-sample failure pattern (here: predicted
    # material vs ground-truth process) and falls back to surface-axis guesses.
    # Sweep/diag forks inherit round 0 and never call this path, so the seed is
    # automatically off there (round 1 stays bit-identical across cheap forks).
    if round_result.results:
        round_result.diagnostics = compute_round_diagnostics(
            round_result,
            [round_result],
            session.pipeline_schema,
        )
        with graceful("Origin critique failed; round 1 proceeds without seeded feedback"):
            async with observed_node(
                "l1_critique_r0",
                "llm/meta",
                obs=session.state.obs,
                campaign_id=session.state.tracing_campaign_id,
                round_num=0,
            ):
                round_result.critique = await run_l1_critique(
                    cycle, round_result, round_num=0, ledger=session.state.ledger
                )

    await close_round(cycle, round_result, 0, session, cb)


async def escalate_or_stop(
    cycle: Cycle,
    config: CampaignConfig,
    session: Session,
    round_num: int,
    cb: RunCallbacks,
) -> None:
    """Run L2 escalation; raise ``StopLoop`` if it returned a stop reason."""
    stop = await escalate_l2(
        cycle,
        config,
        session.pipeline_schema,
        round_num,
        cb.on_phase,
        obs=session.state.obs,
        tracing_campaign_id=session.state.tracing_campaign_id,
    )
    if stop:
        raise StopLoop(stop)


def _round_close_facts(round_result: RoundResult) -> dict[str, Any]:
    """What the CLOSE knows and no candidate could — the adopted individual, and each
    scored row's ``{theta, theta_se, composite_ci_lo, composite_ci_hi}`` (absent keys
    omitted, rows with nothing to say dropped).

    Those four are the only values a candidate's own ``candidate_scored`` snapshot cannot
    carry in final form: θ comes from the round's joint fit (or, at round 0, from the ruler
    calibration), and a warm ruler OVERRIDES the candidate's Wilson whisker with the tighter
    θ-implied band. Reading them off ``candidate_scores`` rather than off the election is
    what lets round 0 — which elects nothing — still report the origin's ability.

    **Keyed by LABEL, the positional identity (`C{round}.{idx}`), never `candidate_id`.**
    A lineage id is a fresh uuid per construction (`IndividualLineage.id`), so a resume
    re-mints round 0's C0 under a new id while this round's already-written close still
    names the old one — an id join then silently drops the crown and θ of every re-minted
    candidate, C0 first. Position is the canonical genealogy and both sides carry it.

    A held round's adopted individual is the retained incumbent, which is not among this
    round's rows, so ``winner_label`` comes back empty and nobody is crowned.
    """
    abilities: dict[str, dict[str, float]] = {}
    for cs in round_result.candidate_scores:
        vals = {
            key: value
            for key, value in (
                ("theta", cs.theta),
                ("theta_se", cs.theta_se),
                ("composite_ci_lo", cs.composite_ci_lo),
                ("composite_ci_hi", cs.composite_ci_hi),
            )
            if value is not None
        }
        if vals:
            abilities[cs.label] = vals
    winner_label = next(
        (
            cs.label
            for cs in round_result.candidate_scores
            if is_round_winner(cs.candidate_id, round_result.winner_id)
        ),
        "",
    )
    return {"winner_label": winner_label, "abilities": abilities}


def persist_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
) -> None:
    """The ledger emit is unconditional — every completed round lands on the ledger."""
    flushed: list[ResumeCheckpointRecord] = []
    if cycle.pending_decisions:
        flushed = list(cycle.pending_decisions)
        cycle.pending_decisions.clear()
        round_result.decisions.extend(d.to_dict() for d in flushed)

    if cycle.axes is not None:
        round_result.axis_memory_peaked = sorted(cycle.axes.peaked_axes())

    if (ledger := session.state.ledger) is not None:
        for d in flushed:
            ledger.append(d)
        ledger.append(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_num,
                payload={
                    "accuracy": round_result.accuracy,
                    "composite_fitness": round_result.composite_fitness,
                    "improved": round_result.improved,
                    # Banked beside `improved` because the life bank needs both to replay a
                    # round identically on resume (`EscalationFSM.fold`): `improved` says which
                    # way to move the bank, this says whether to move it at all.
                    "electable_count": round_result.electable_count,
                    "label": round_result.label,
                    # Everything the CLOSE knows and no candidate could: the frontier it
                    # advanced, the individual it adopted, and the ability fit. All read off
                    # the `RoundResult` being persisted, so one writer serves every round —
                    # round 0 included, which holds no election but does have a C0 and a θ
                    # from the ruler fit. Without this the only on-disk home of θ and the
                    # crown was `dashboard.json`, and a served view had to open another
                    # projection's output to find them.
                    "cumulative_theta": round_result.cumulative_theta,
                    **_round_close_facts(round_result),
                },
            )
        )

    if session.state.cycle_id:
        with graceful("Round checkpoint failed"):
            session.store.campaigns.save_round_file(
                session.campaign_id,
                session.state.cycle_id,
                round_result,
            )
        hard_samples_artifact = write_hard_samples_artifacts(session, cycle)
        write_log_md(session, hard_samples_artifact=hard_samples_artifact)
        write_review_md(session, cycle)

    if _rr := session.state.audit_projection:
        _rr.flush()


def count_positive_yield_axes(cycle: Cycle) -> int | None:
    """Axes with effect_size > AxisIndex noise floor (= 0.02); ``None`` pre-first-round (no AxisIndex yet)."""
    if cycle.axes is None:
        return None
    from promptpotter.application.intelligence.indexes.axis import NOISE_THRESHOLD

    return sum(1 for r in cycle.axes.axis_rankings() if r.effect_size > NOISE_THRESHOLD)


async def close_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    session: Session,
    cb: RunCallbacks,
) -> None:
    """Round-completion bookkeeping every completed round runs.
    Emits ``round:display`` (via ``cb.on_round_complete``) + ``round:complete`` (via ``persist_round``),
    writes ``round_NNNN.json``, refreshes axis memory. Independent of whether the round feeds escalation.

    SINGLE degradation-verdict compute site (origin + L1 both funnel here): stamp
    ``round_result.health`` BEFORE the dashboard emit (``cb.on_round_complete`` reads
    ``rr.health``) and the round-file write (``persist_round``), so the verdict reaches
    every surface in one shape.

    Track record = the prior rounds' verdicts, oldest first, starting at the origin's.
    The round being closed is excluded (it is already in ``cycle.rounds``)."""
    round_result.health = compute_round_health(
        results=round_result.results,
        prior_healths=assemble_prior_healths(cycle.rounds, round_num),
        is_origin=round_num == 0,
    )
    cb.on_round_complete(round_result, cycle.escalation.l1_stall_count, cycle.escalation.lives)
    persist_round(cycle, round_result, round_num, session)
    if cycle.axes and session.store:
        cycle.axes.refresh(
            session.store,
            scorer=session.scoring.scorer,
            scorer_id=session.scoring.scorer_id,
            scorer_formula=session.scoring.scorer_formula,
            dataset_name=session.dataset_name,
        )


async def post_round(
    cycle: Cycle,
    round_result: RoundResult,
    round_num: int,
    config: CampaignConfig,
    session: Session,
    cb: RunCallbacks,
    *,
    is_final_round: bool = False,
) -> None:
    """Clean-round escalation observation; raises ``StopLoop`` on stop condition.
    State machine observes outcome (CONTINUE / FIRE_L2 / STOP_*), then closes the round and dispatches.
    Probe rounds bypass observation and call ``close_round`` directly.

    ``is_final_round`` suppresses the L2 fire: L2's refined ``task_context`` is read by the
    NEXT round's L1, so on the last round it is a ~35s LLM call whose output dies with the
    cycle. The lives-exhausted boundary needs no flag — ``observe_round`` sets
    ``stop_reason`` and we raise before reaching the fire."""
    axes_with_positive_yield = count_positive_yield_axes(cycle)
    # A dropped mandatory backend placeholder is structural, not a stall — heal L2 now
    # (patience 0) instead of burning l1_patience rounds while L1 re-drops it.
    l1_mandatory_breach = any(
        vf.reason == DROPPED_MANDATORY_PLACEHOLDER
        for sc in round_result.candidate_scores
        for vf in sc.validation_failures
    )
    # A zero-candidate round (l1_generate returned nothing parseable) is the same class of
    # structural l1_generate fault as a mandatory-placeholder breach: re-running the identical
    # meta-prompt reproduces it, so route L2 to heal now instead of burning l1_patience dead
    # rounds. `l1_mandatory_breach` reads candidate_scores, which is empty in exactly this round,
    # so it can't catch this — the round owns the signal on `l1_parse_failure`.
    l1_zero_candidates = round_result.l1_parse_failure is not None
    # Evidence-starvation router input, derived from the SAME helper the degradation grade
    # reads (``evidence_starved_node``) so routing and verdict can't diverge. Health itself
    # isn't stamped until ``close_round`` (below), so we read the rates directly here.
    evidence_starved = (
        evidence_starved_node(compute_node_failure_rates(round_result.results)) is not None
    )
    event = cycle.escalation.observe_round(
        improved=round_result.improved,
        compared=round_result.electable_count > 0,
        current_accuracy=cycle.tracking.current_accuracy,
        l1_patience=config.optimization.l1_patience,
        lives=config.optimization.lives,
        axes_with_positive_yield=axes_with_positive_yield,
        l1_mandatory_breach=l1_mandatory_breach,
        l1_zero_candidates=l1_zero_candidates,
        evidence_starved=evidence_starved,
    )

    await close_round(cycle, round_result, round_num, session, cb)

    if event.stop_reason is not None:
        raise StopLoop(event.stop_reason)
    if is_final_round:
        return
    if event.next_action == NextAction.FIRE_L2:
        await escalate_or_stop(cycle, config, session, round_num, cb)


__all__ = [
    "close_round",
    "count_positive_yield_axes",
    "emit_origin_round",
    "escalate_or_stop",
    "persist_round",
    "post_round",
]
