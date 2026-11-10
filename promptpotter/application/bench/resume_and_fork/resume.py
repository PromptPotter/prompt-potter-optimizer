"""``repair.py`` runs regardless of the defaults pin; only the divergence check short-circuits on it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.resume_and_fork.fork_siblings import cut_fork
from promptpotter.application.bench.resume_and_fork.repair import apply_correction, round_packages
from promptpotter.application.bench.resume_and_fork.replayers import (
    ReplayMismatch,
    replay_decisions,
)
from promptpotter.application.pipeline_resolve import campaign_admits, defaults_drift
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import round_document_digest
from promptpotter.domain.run_records import ComparisonExit, ForkTrigger
from promptpotter.shared.errors import ResumeDivergenceError
from promptpotter.shared.identity import SYSTEM_PRINCIPAL

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import StandingRounds

logger = logging.getLogger(__name__)


def _stale_generation(
    standing: StandingRounds,
    prior: list[RoundResult],
    resumed_from_round: int,
    selected: SelectedOptimizer,
) -> ReplayMismatch | None:
    """No recorded digest is a divergence, not a shrug; nor is a walk banked by another optimizer
    or another prompt of its nodes, which its replay would carry in unseen."""

    held = standing.proposals.get(resumed_from_round)
    if held is None or not prior:
        return None
    cached = held.record
    recorded = (cached.consumed, cached.walked.manifest, cached.walked.prompt_hashes)
    current = (round_document_digest(prior[-1]), selected.name, selected.prompt_hashes())
    if recorded == current:
        return None
    logger.warning(
        "Round %d's cached candidates were generated from round %d as it read %s by %s, and it "
        "now reads %s by %s — regenerating on a branch rather than replaying them.",
        resumed_from_round,
        prior[-1].round,
        recorded[0] or "(unrecorded)",
        recorded[1],
        current[0],
        current[1],
    )
    return ReplayMismatch(
        round_num=resumed_from_round,
        kind="stale_generation",
        recorded_outcome=list(recorded),
        current_outcome=list(current),
        inputs_ref={"generated_from_round": prior[-1].round},
    )


def _optimizer_mismatches(
    prior: list[RoundResult], selected: SelectedOptimizer
) -> dict[int, ReplayMismatch]:
    """Asked PER ROUND so an edit forks from where it bites."""

    current = selected.prompt_hashes()
    out: dict[int, ReplayMismatch] = {}
    for t in prior:
        recorded = t.closed_state.prompt_hashes
        moved = sorted(n for n, h in recorded.items() if current.get(n) != h)
        if not moved:
            continue
        out[t.round] = ReplayMismatch(
            round_num=t.round,
            kind=f"optimizer_identity:{','.join(moved)}",
            recorded_outcome={n: recorded[n] for n in moved},
            current_outcome={n: current.get(n) for n in moved},
            inputs_ref={"nodes": moved},
        )
        logger.warning(
            "Round %d was produced by a different optimizer than the one loaded now — %s "
            "changed (template, injection layout or resolved model/config).",
            t.round,
            ", ".join(moved),
        )
    return out


async def resume_with_divergence_check(
    cycle: Cycle,
    dataset: list[Any],
    callbacks: RunCallbacks,
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool,
    rewind: int | None,
) -> str | None:
    """The cycle opens on the standing the last close of the line it continues stated, a fork's included."""
    session = cycle.session
    campaign_store, hop = session.store.campaigns, session.hop
    resumed_from_round = session.state.resumed_from_round
    scorer = session.state.scoring.require_scorer()
    prior = closed_rounds(session.store, hop, scorer, before_round=resumed_from_round, moment=None)

    # Fingerprinted BEFORE the repair, so the corrected set differs by exactly what it changed.
    packages_before = round_packages(cycle, prior, callbacks)
    cycle.replay_priors(prior)

    # Decided on the round this run enters, before its rewind is written: a halt displaces nothing.
    standing = campaign_store.standing_rounds(hop, moment=None)
    if rewind is not None:
        standing = standing.displaced(resumed_from_round, resumed_from_round)
    found = None if skip_divergence_check else _divergence(cycle, prior, standing)
    if found is not None and not found[1] and not fork_on_divergence:
        div = found[0]
        raise ResumeDivergenceError(
            round_num=div.round_num,
            kind=div.kind,
            recorded_outcome=div.recorded_outcome,
            current_outcome=div.current_outcome,
            diagnostics={
                "scorer_id": scorer.id,
                "fork_hint": (
                    "rerun `resume --fork-on-divergence` to branch a new cycle "
                    "here; this one keeps what the superseded package produced"
                ),
            },
        )
    if rewind is not None:
        campaign_store.rewind_to_round(hop, rewind)
        campaign_store.leave_comparison(hop, ComparisonExit.REWIND)
    if skip_divergence_check:
        campaign_store.leave_comparison(hop, ComparisonExit.NO_DIVERGENCE_CHECK)

    fork = await apply_correction(cycle, prior, packages_before, dataset, callbacks)
    if fork is None and found is not None:
        fork = _branch(cycle, prior, *found)
    line = hop if fork is None else CycleHop(campaign_id=hop.campaign_id, cycle_id=fork)
    cycle.standing = campaign_store.standing_rounds(line, moment=None).standing
    return fork


def _branch(
    cycle: Cycle, prior: list[RoundResult], div: ReplayMismatch, self_inflicted: bool
) -> str:
    session = cycle.session
    hop = session.hop
    new_cycle_id = cut_fork(
        session.store,
        hop,
        div.round_num,
        trigger=ForkTrigger.SCORING_DIVERGENCE,
        reason=f"resume_divergence:{div.kind}",
        issued_by=SYSTEM_PRINCIPAL,
    ).take(session.store)
    if not self_inflicted:
        # The operator's flag ran the line past a divergence it halts on.
        session.store.campaigns.leave_comparison(
            CycleHop(campaign_id=hop.campaign_id, cycle_id=new_cycle_id),
            ComparisonExit.FORK_ON_DIVERGENCE,
        )
    if div.round_num == 0:
        cycle.reopen_origin()
    else:
        cycle.replay_priors([t for t in prior if t.round < div.round_num])
    logger.warning(
        "Resume diverged at round %d (%s); branched → %s. That cycle is the "
        "continuation; this one keeps what the superseded package produced.",
        div.round_num,
        div.kind,
        new_cycle_id,
    )
    return new_cycle_id


def _divergence(
    cycle: Cycle, prior: list[RoundResult], standing: StandingRounds
) -> tuple[ReplayMismatch, bool] | None:
    """The first divergence of the line, and whether this resume CAUSED it."""
    session = cycle.session
    optimizer_mismatches = _optimizer_mismatches(prior, cycle.optimizer)
    stale = _stale_generation(standing, prior, session.state.resumed_from_round, cycle.optimizer)
    campaign = session.store.campaigns.load_campaign(session.campaign_id)
    # The declaration never moves; the defaults it is a delta over can, under the same name.
    running = cycle.optimizer.treatment(
        campaign_admits(cycle.config, session.pipeline_schema), session.store.block_packs
    )
    drifted = campaign is not None and defaults_drift(campaign, running=running) is not None
    if not optimizer_mismatches and stale is None and not drifted:
        return None
    for t in prior:
        div = optimizer_mismatches.get(t.round) or replay_decisions(
            t,
            standing.decisions.get(t.round, []),
            ruler=cycle.difficulty.ruler_of(t.ruler),
        )
        if div is not None:
            return div, False
    # The round about to run has no round file, so the loop above cannot visit it.
    return None if stale is None else (stale, True)
