from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.exploration import (
    build_observations,
    select_round_subset,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import optimizer_model
from promptpotter.application.optimization.l1.candidate_source import generate_or_load_candidates
from promptpotter.application.optimization.l1.critique import run_l1_critique
from promptpotter.application.optimization.l1.score.overlap import measure_overlap
from promptpotter.application.optimization.l1.score.winner import l1_score
from promptpotter.application.optimization.pobb.checks import PoBBConfig
from promptpotter.application.optimization.resume_and_fork.decisions import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
from promptpotter.application.run_phase_control import declare_run_phase
from promptpotter.application.runner.termination import panel_gate_tripped
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.phases import CampaignPhase, RunPhase, StopLoop, StopReason, emit_phase
from promptpotter.domain.results import RoundResult, is_leader_eligible, unscoreable_cells
from promptpotter.domain.validators import StopRule

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.infrastructure.tracing.events import (
    PromptVersion,
    RoundEnd,
    RoundStart,
)
from promptpotter.shared.errors import graceful, is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


async def execute_round(
    cycle: Cycle,
    round_num: int,
    scoring_pool: list[Sample],
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    *,
    skip_critique: bool = False,
    is_final_round: bool = False,
) -> RoundResult:
    """One L1 round: generate → score+select → critique. The runner folds the result in via ``absorb_round`` — this never mutates
    ``Cycle``. Sweep mode and the final round drop the critique; `ensure_prior_critique` re-sends it
    at the head of a round that turns out to follow one."""
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    obs = session.state.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.tracing_campaign_id, round_num=round_num))

    # Narrow the train-split bank to ``sp_budget_ttest`` contested samples via the
    # adaptive queue mechanism. Origin + every candidate share this subset so PoBB compares
    # like-for-like.
    if not opt.mechanisms.selection.per_round_resubset or cycle.ruler is None:
        # Frozen selection: ignore accumulating observations, so every round gets the
        # deterministic campaign-start subset (bank prefix) — one fixed sample basis.
        # Two triggers: resubset is OFF, OR the δ ruler is still COLD — a per-round subset
        # would then be difficulty-blind and cross-round-incomparable, and freezing
        # concentrates measurements so the ruler warms + locks fastest. Once warm, the
        # branch below thaws to adaptive.
        scoring_set = select_round_subset(scoring_pool, [], config.sp_budget_ttest)
    else:
        # Archive obs are dataset-scoped + abort-residue-free → cross-cycle evidence.
        own = build_observations(cycle.rounds)
        observations = [*cycle.archive_observations, *own]
        # Thawing to adaptive is what can walk the subset off the locked ruler: the acquisition
        # score prefers unmeasured cells, because `delta_learning_gain` rises with δ's SE.
        # `anchor_floor` reserves enough already-anchored cells for the next extension to equate
        # against, so growth stays possible.
        scoring_set = select_round_subset(
            scoring_pool,
            observations,
            config.sp_budget_ttest,
            ruler=cycle.ruler,
            anchor_floor=opt.elimination_n_min,
            # The archive fits the θ scale; only THIS cycle's arms are in the race the panel has
            # to separate. Targeting the archive's best calibrates the round to an arm nobody ran.
            leader_ids={o.candidate_id for o in own},
        )

    candidates, yield_stats = await generate_or_load_candidates(
        round_num, cycle, callbacks.on_phase, n_scoring_samples=len(scoring_set), obs=obs
    )

    assert cycle.tracking.current_sp is not None
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_samples=len(scoring_set),
        current_best_accuracy=cycle.tracking.current_accuracy,
        current_pipeline_params=cycle.tracking.current_sp.pipeline_params,
    )
    async with observed_node(
        f"l1_score_r{round_num}",
        "scoring",
        obs=obs,
        as_type="span",
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        round_result, winner_opt_sp = await l1_score(
            cycle,
            candidates,
            scoring_set,
            pipeline_params=cycle.tracking.current_sp.pipeline_params,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            pobb_config=PoBBConfig(
                n_min=opt.elimination_n_min,
                epsilon=opt.pobb_epsilon,
                epsilon_floor=opt.pobb_epsilon_floor,
                lock_in=opt.pobb_lock_in,
                lock_in_n_min=opt.pobb_lock_in_n_min,
                epsilon_elimination=opt.mechanisms.elimination.epsilon_elimination,
                leader_lock_in=opt.mechanisms.elimination.leader_lock_in,
            ),
            round_num=round_num,
            yield_stats=yield_stats,
        )
        # The elected winner IS the round's resulting parent — and on a HELD round
        # (no candidate cleared the floor) l1_score returns the retained parent itself
        # (origin.opt_sp), so the ids match and absorb_round adopts nothing. absorb reads
        # this to advance the cycle's identity to the winner on an advancing round.
        round_result.opt_sp = winner_opt_sp
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=round_result.label,
        # The elected id, beside the prose label. `winner_label` is `changes_description`, which
        # is optional and which two candidates can share, so it cannot identify a row — and the
        # dashboard needs to crown one HERE, at the election, rather than wait for the round to
        # close two optimizer calls later.
        winner_accuracy=round_result.accuracy,
        winner_composite_fitness=round_result.composite_fitness,
        winner_evaluators=dict(round_result.evaluators),
        winner_total=round_result.total,
        improved=round_result.improved,
        verdict_reason=round_result.verdict_reason,
        p_value=round_result.p_value,
        candidate_scores=[c.model_dump() for c in round_result.candidate_scores],
        winner_matched_parent_accuracy=round_result.matched_parent_accuracy,
        winner_matched_parent_composite=round_result.matched_parent_composite,
    )

    # PANEL COVERAGE — the round's own completeness, asked HERE because this is the last
    # point at which nothing has been decided on it yet: the winner is elected but round
    # diagnostics have not run and the critique has not fired, and a critique computed over
    # a holed round would itself be invalid evidence for the next one. Round granularity,
    # not per-candidate: a hole is a statement about the COMPARISON, and one candidate
    # cannot see it.
    #
    # The `is_leader_eligible` conjunct matters. A degradation / scoring-error abort also
    # produces error rows, and it already owns a channel (`backend_unreachable_tripped`);
    # halting on it here would be a second mechanism doing one job.
    holed = sorted(
        c.candidate_id
        for c in round_result.candidate_scores
        if is_leader_eligible(c)
        and unscoreable_cells(round_result.all_candidate_results.get(c.candidate_id) or [])
    )
    # Sink is the LEDGER, not the round's `decisions` list: the halt below unwinds before
    # `persist_round`, so a round-local record would never reach disk on exactly the round
    # it is evidence about. ARCHIVAL by gating — see `resume_and_fork/decisions.py` for why
    # replaying it could only ever confirm itself.
    ledger = session.state.ledger
    assert ledger is not None, "build_run_observers must bind state.ledger before a round runs"
    record_decision(
        ledger,
        ResumeCheckpointKind.PANEL_COVERAGE,
        {
            "candidate_ids": [c.candidate_id for c in round_result.candidate_scores],
            "round_num": round_num,
        },
        holed,
        data={
            "holes_by_candidate": {
                c.candidate_id: unscoreable_cells(
                    round_result.all_candidate_results.get(c.candidate_id) or []
                )
                for c in round_result.candidate_scores
            }
        },
        round=round_num,
    )
    if panel_gate_tripped(holed, opt.panel_gate) is not None:
        for cid in holed:
            rows = round_result.all_candidate_results.get(cid) or []
            for row in rows:
                if is_error_result(row):
                    logger.warning(
                        "round %d panel HOLE: candidate %s, sample %s — %s",
                        round_num,
                        cid,
                        row.get("sample_id"),
                        row.get("error") or row.get("error_category"),
                    )
        logger.warning(
            "Round %d halted BEFORE electing on an incomplete panel: %d of %d electable "
            "candidate(s) carry cells that returned no measurement. The round is not "
            "persisted — `resume` re-runs it, replays the cached candidates, re-measures "
            "the missing cells and decides on a complete panel. Set "
            "`optimization.panel_gate: off` to elect on holed panels instead.",
            round_num,
            len(holed),
            sum(1 for c in round_result.candidate_scores if is_leader_eligible(c)),
        )
        declare_run_phase(session, RunPhase.PAUSED)
        raise StopLoop(StopReason.PAUSED)

    # The 1-to-1 series. Here and nowhere earlier: the election, the ruler extension and the
    # panel gate are all behind us, so no cell this buys can reach a decision this round made —
    # and the fields it writes sit outside `results` / `all_candidate_results`, which is where
    # the NEXT round's acquisition and ruler read. Bounded by `sp_budget_ttest` on ONE
    # searchpoint, and zero on a held round.
    await measure_overlap(cycle, round_result, scoring_pool)

    # The election, banked. AFTER the panel gate on purpose: a round halted on a holed panel is
    # unwound and re-run, so crowning it would put a winner on the timeline for a round that
    # never stood. Still a whole `l1_critique` call before the close, which is the point — the
    # crown and the matched-parent floor both exist HERE, and rode the close only because that
    # was the record that happened to have a payload.
    callbacks.on_election(round_result)

    # Round diagnostics feed dispatch's ``diagnostics`` signal (L1_CRITIQUE/L2/L3).
    rounds_history = [*cycle.rounds, round_result]
    round_result.diagnostics = compute_round_diagnostics(
        round_result,
        rounds_history,
        session.pipeline_schema,
    )

    # A zero-candidate round (l1_generate returned [] — empty/parse-failed provider
    # response) leaves ``round_result.results`` holding the ORIGIN's rows, so without the
    # ``candidates`` guard critique would fire a meta LLM call to critique nothing. Skip it:
    # the empty generation is already recorded as a ``ValidationFailure`` wound that routes
    # L2 to heal l1_generate — that is the right next move, not another critique.
    # Critique is feedback FOR THE NEXT ROUND's l1_generate — nothing else reads it. When no
    # next round will run, the call is pure latency: on the L4 inner benchmark the terminal
    # critique + the terminal L2 fire together burned 15.8% of all inner wall time producing
    # output that died with the cycle. Both boundaries must be checked: the calendar cap
    # (`is_final_round`, known before the round) and the lives bank emptying (knowable only
    # now, from this round's `improved` verdict — asked through the FSM so the lives-bank lookahead can
    # never disagree with the banking `post_round` is about to do).
    will_stop = is_final_round or cycle.escalation.would_exhaust_lives(
        round_result.improved,
        config.optimization.lives,
        compared=round_result.electable_count > 0,
    )
    if candidates and round_result.results and not skip_critique and not will_stop:
        # Critique is round-over-round feedback — survive a malformed response.
        with graceful("L1 critique failed; the next round re-sends it before generating"):
            async with observed_node(
                f"l1_critique_r{round_num}",
                "llm/optimizer",
                obs=obs,
                campaign_id=session.state.tracing_campaign_id,
                round_num=round_num,
            ):
                critique_result = await run_l1_critique(
                    cycle,
                    round_result,
                    round_num=round_num,
                    ledger=session.state.ledger,
                )
            round_result.critique = critique_result
    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    accuracy=round_result.accuracy,
                    total=round_result.total,
                    improved=round_result.improved,
                    winner_lineage_id=winner_opt_sp.lineage.id,
                    candidate_scores=[c.model_dump() for c in round_result.candidate_scores],
                    model=optimizer_model(),
                    n_variants=config.optimization.n_variants,
                    optimizer_templates=["l1_generate", "l1_critique"],
                    evaluators=dict(round_result.evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    lineage_id=winner_opt_sp.lineage.id,
                    rendered_prompt=winner_opt_sp.render(),
                    layer1_fields={f: getattr(winner_opt_sp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=winner_opt_sp.lineage.parent_id,
                )
            )

    return round_result


__all__ = ["execute_round"]
