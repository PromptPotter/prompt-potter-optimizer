"""Resume entry point — repair holed rounds, rescore, replay decisions, halt-or-fork.

:func:`resume_with_divergence_check` is the single entry point the runner reaches for.
It rescores prior rounds under the active scorer, repairs any that recorded a cell
without a measurement (:func:`repair_incomplete_rounds`), then walks
``replay_decisions`` for the first divergence and either halts with
:class:`ResumeDivergenceError` or mints a sibling cycle via :func:`_mint_fork`
(``ForkTrigger.SCORING_DIVERGENCE``), retargeting the active pointer.

**Incompleteness and divergence are different questions.** Divergence asks whether the
recorded outcome still holds under the active scorer, and the config diff decides
whether it is worth asking. Incompleteness asks whether the round ever had the evidence
to decide at all — a question the config cannot answer — so the repair runs regardless
of ``DiffScope`` and, when it lands, FORCES the walk it would otherwise short-circuit:
the rows the winner was elected from have just changed.

The escalation FSM is rebuilt via ``EscalationFSM.from_ledger`` because the ledger is
the SoT for layer-stall counters across resume.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.config import freeze_campaign_config
from promptpotter.application.knobs import DiffScope, classify_config_diff

# Leaf import (not the package surface): rebuilding the foundational FSM via
# escalation/__init__ would load the firing driver, which imports resume_and_fork
# back → import cycle. See escalation/__init__ "MAPPED" note.
from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    _mint_fork,
)
from promptpotter.application.optimization.resume_and_fork.replayers import replay_decisions
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.run_records import ForkSpec, ForkTrigger
from promptpotter.shared.errors import ResumeDivergenceError
from promptpotter.shared.instrument import MeasuredCandidate, MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["resume_with_divergence_check"]


async def repair_incomplete_rounds(
    campaign_store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    prior: list[Any],
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
) -> list[int]:
    """Re-measure the cells an already-CLOSED round recorded without a measurement.

    A round that crowned a winner on a holed panel is not repaired by the panel gate —
    that gate stops the round it fires in, and rounds banked before it existed carry their
    holes forever. The evidence needed to spot one is the round's OWN rows, so the check is
    a re-derivation, not a recorded decision: an electable candidate holding an error row
    never had the panel its rivals were measured on.

    Each hole is plugged with a REAL measurement, never an archive row. That distinction is
    the whole point: a cached row for this ``(node_configs, sample_id)`` may have been
    produced as a ``BACKFILL`` — measured out of the round's shared order to fill someone
    else's paired comparison — and adopting it as this candidate's own panel cell is what
    made a repaired round unreproducible. ``force_fresh`` bypasses only the OUTER archive,
    so the spawn still resolves content-addressed and continues the furthest-along inner
    campaign banked for that cell rather than restarting it. Cost is therefore one inner
    run per hole, resumed from wherever it died.

    The already-measured cells are not re-run: a second gateway pass over the whole
    attempted set replays them from cache, and exists so the composite comes from the
    scoring gateway over the complete panel rather than a local computation stitched onto
    the partial return.

    Re-election is deliberately NOT done here. ``ROUND_WINNER`` is already ``REPLAYED``, so
    the divergence walk re-elects each repaired round from its repaired rows and answers
    "did completing the evidence change who won?" through the one mechanism that owns that
    question. This only makes the rows honest.
    """
    from promptpotter.application.optimization.l1.population import build_score_report
    from promptpotter.application.scoring.search_point_scorer import score_search_point
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import is_leader_eligible, unscoreable_cells
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.shared.errors import is_error_result

    by_id = {str(s.id): s for s in dataset}
    repaired: list[int] = []
    for t in prior:
        cached = campaign_store.load_round_candidates(campaign_id, cycle_id, t.round) or []
        opt_sps: dict[str, OptSearchPoint] = {}
        for entry in cached:
            try:
                osp = OptSearchPoint.model_validate(entry.get("opt_sp") or {})
            except Exception:  # a stale cache entry must not abort the resume
                continue
            opt_sps[osp.lineage.id] = osp
        changed = False
        for i, cs in enumerate(t.candidate_scores):
            rows = list(t.all_candidate_results.get(cs.candidate_id) or [])
            if not is_leader_eligible(cs) or not unscoreable_cells(rows):
                continue
            attempted = [by_id[sid] for r in rows if (sid := str(r.get("sample_id"))) in by_id]
            cand_osp = opt_sps.get(cs.candidate_id)
            if cand_osp is None or not attempted:
                logger.warning(
                    "Round %d candidate %s has unmeasured cells but no cached searchpoint "
                    "to re-measure them with — leaving it holed rather than guessing.",
                    t.round,
                    cs.label,
                )
                continue
            sp = JobSearchPoint(
                pipeline_params=cs.resolved_pipeline_params, prompt_fields=cs.prompt_fields
            )
            stamp = MeasuredCandidate(
                idx=i,
                candidate_id=cs.candidate_id,
                label=cs.label,
                role=MeasurementRole.REPAIR,
            )
            missing = [
                by_id[sid]
                for r in rows
                if is_error_result(r) and (sid := str(r.get("sample_id"))) in by_id
            ]
            # PHASE 1 — plug each hole with a REAL measurement, one cell at a time.
            # ``force_fresh`` bypasses the outer measurement archive only: the inner spawn
            # still resolves content-addressed, so this lands in ``_open_inner_campaign``
            # and CONTINUES the furthest-along campaign banked for this (candidate, cell)
            # instead of restarting it. Reusing the archive here is what must not happen —
            # the row sitting there may have been measured as a BACKFILL, out of the
            # round's shared order for someone else's paired comparison, and adopting it
            # as this candidate's own panel cell is precisely what made round 1
            # unreproducible.
            for hole in missing:
                await score_search_point(
                    sp,
                    [hole],
                    session,
                    label="round_repair",
                    opt_sp=None,
                    degradation_checks=None,
                    n_total_candidates=0,
                    axes=cycle.axes,
                    on_sample_scored=None,
                    on_sample_starting=None,
                    measured=stamp,
                    force_fresh=True,
                )
            # PHASE 2 — re-score the whole attempted set. Every cell is now in the archive,
            # so this is all cache hits; its purpose is that the composite comes from the
            # scoring gateway over the complete panel rather than from a second, local
            # computation stitched onto phase 1's partial return.
            results, scores, _signal = await score_search_point(
                sp,
                attempted,
                session,
                label="round_repair",
                opt_sp=None,
                degradation_checks=None,
                n_total_candidates=0,
                axes=cycle.axes,
                on_sample_scored=None,
                on_sample_starting=None,
                measured=stamp,
            )
            t.all_candidate_results[cs.candidate_id] = results
            t.candidate_scores[i] = build_score_report(
                cand_osp,
                cs.pipeline_params_override,
                scores,
                results,
                attempted,
                label=cs.label,
                resolved_pipeline_params=cs.resolved_pipeline_params,
                elimination_stopped=cs.elimination_stopped,
            )
            changed = True
            logger.warning(
                "Repaired round %d candidate %s: %d cell(s) had no measurement; "
                "re-measured %d, composite %.4f → %.4f",
                t.round,
                cs.label,
                unscoreable_cells(rows),
                len(results),
                cs.composite_fitness,
                t.candidate_scores[i].composite_fitness,
            )
        if changed:
            campaign_store.save_round_file(campaign_id, cycle_id, t)
            repaired.append(t.round)
    return repaired


async def resume_with_divergence_check(
    campaign_store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    resumed_from_round: int,
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool = False,
) -> ForkResult | None:
    """Rescore prior rounds under the active scorer; halt or fork on divergence.

    Short-circuits when the diff between the active ``cycle.config`` and the
    campaign's frozen snapshot (``campaign.json::config``) classifies as
    :attr:`DiffScope.NONE` or :attr:`DiffScope.POLICY_ONLY`: the parent's
    data trace is fully valid, past decisions stay as the audit record, and
    the active policy governs unevaluated rounds. No fork, no divergence
    walk. See :func:`promptpotter.application.knobs.classify_config_diff`.
    """
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_rounds_range(campaign_id, cycle_id, 0, resumed_from_round - 1)

    def _rescore(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = list(items or [])
        rescore_results(out, scorer, sc.scorer_id, sc.scorer_formula)
        return out

    for t in prior:
        _rescore(t.results)
        for items in t.all_candidate_results.values():
            _rescore(items)

    origin_results_rescored = _rescore(cycle.tracking.current_results)

    # Repair BEFORE the divergence walk, and independently of the config diff: a holed
    # round is incomplete, not divergent, so the config-diff short-circuit below must not
    # be what decides whether it is noticed. A repair that lands makes the walk mandatory —
    # the rows the recorded winner was elected from have just changed, and `ROUND_WINNER`
    # is the mechanism that asks whether it still wins. It forks rather than overwrites,
    # so a flipped crown mints a sibling and the original trajectory survives.
    repaired = await repair_incomplete_rounds(
        campaign_store, campaign_id, cycle_id, prior, session, cycle, dataset
    )
    if repaired:
        logger.warning(
            "Resume repaired unmeasured cells in round(s) %s; re-electing their winners "
            "(a changed winner forks a sibling rather than overwriting the record).",
            ", ".join(str(r) for r in repaired),
        )
        fork_on_divergence = True

    if not skip_divergence_check:
        campaign = campaign_store.load_campaign(campaign_id)
        frozen = campaign.config if campaign is not None else {}
        scope, diffed = classify_config_diff(cycle.config, frozen)
        if not repaired and scope in (DiffScope.NONE, DiffScope.POLICY_ONLY):
            if scope is DiffScope.POLICY_ONLY:
                logger.info(
                    "Resume: policy-only config diff (%s); continuing on cycle %s in-place",
                    ", ".join(diffed),
                    cycle_id,
                )
                # Refresh the campaign snapshot so future resumes diff
                # against current state.
                campaign_store.update_campaign(
                    campaign_id, {"config": freeze_campaign_config(cycle.config)}
                )
            cycle.replay_priors(prior)
            cycle.escalation = EscalationFSM.from_ledger(
                session.state.ledger, lives=cycle.config.optimization.lives
            )
            return None
        if scope is DiffScope.DATA_AFFECTING and diffed:
            logger.info(
                "Resume: data-affecting config diff (%s); running divergence check",
                ", ".join(diffed),
            )
        for i, t in enumerate(prior):
            div = replay_decisions(
                t,
                origin_results=origin_results_rescored,
                delta_scale=cycle.delta_scale,
            )
            if div is None:
                continue
            if fork_on_divergence:
                survivors = list(prior[:i])
                new_cycle_id = _mint_fork(
                    campaign_store,
                    campaign_id,
                    session.session_id,
                    cycle_id,
                    div.round_num,
                    ForkSpec(
                        trigger=ForkTrigger.SCORING_DIVERGENCE,
                        reason=f"scorer_mismatch:{div.kind}",
                        issued_by="system",
                    ),
                    surviving_rounds=survivors,
                )
                cycle.replay_priors(survivors)
                cycle.escalation = EscalationFSM.from_ledger(
                    session.state.ledger, lives=cycle.config.optimization.lives
                )
                logger.warning(
                    "Resume diverged at round %d (%s); forked → %s",
                    div.round_num,
                    div.kind,
                    new_cycle_id,
                )
                return ForkResult(
                    new_cycle_id=new_cycle_id,
                    new_resumed_from_round=div.round_num,
                )
            raise ResumeDivergenceError(
                round_num=div.round_num,
                kind=div.kind,
                recorded_outcome=div.recorded_outcome,
                current_outcome=div.current_outcome,
                diagnostics={
                    "scorer_id": sc.scorer_id,
                    "fork_hint": (
                        "rerun `optimize --fork-on-divergence` to branch a new "
                        "cycle here under the current scorer"
                    ),
                },
            )

    cycle.replay_priors(prior)
    cycle.escalation = EscalationFSM.from_ledger(
        session.state.ledger, lives=cycle.config.optimization.lives
    )
    return None
