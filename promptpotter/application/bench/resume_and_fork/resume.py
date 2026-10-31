"""``repair.py`` runs regardless of the defaults pin; only the divergence check short-circuits on it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.resume_and_fork.fork_siblings import mint_fork
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
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)


def _stale_generation(
    campaign_store: CampaignStore,
    hop: CycleHop,
    prior: list[RoundResult],
    resumed_from_round: int,
) -> ReplayMismatch | None:
    """No recorded digest is a divergence, not a shrug."""

    cached = campaign_store.round_proposals(hop, resumed_from_round)
    if cached is None or not prior:
        return None
    consumed = cached.consumed
    current = round_document_digest(prior[-1])
    if consumed == current:
        return None
    logger.warning(
        "Round %d's cached candidates were generated from round %d as it read %s, and it "
        "now reads %s — regenerating on a branch rather than replaying them.",
        resumed_from_round,
        prior[-1].round,
        consumed or "(unrecorded)",
        current,
    )
    return ReplayMismatch(
        round_num=resumed_from_round,
        kind="stale_generation",
        recorded_outcome=consumed,
        current_outcome=current,
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
    fork_on_divergence: bool = False,
) -> str | None:
    """The cycle opens on the standing the last close of the line it continues stated, a fork's included."""
    fork = await _replay_line(
        cycle,
        dataset,
        callbacks,
        skip_divergence_check=skip_divergence_check,
        fork_on_divergence=fork_on_divergence,
    )
    hop = cycle.session.hop
    line = hop if fork is None else CycleHop(campaign_id=hop.campaign_id, cycle_id=fork)
    cycle.standing = cycle.session.store.campaigns.standing_rounds(line, moment=None).standing
    return fork


async def _replay_line(
    cycle: Cycle,
    dataset: list[Any],
    callbacks: RunCallbacks,
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool,
) -> str | None:
    session = cycle.session
    campaign_store, hop = session.store.campaigns, session.hop
    resumed_from_round = session.state.resumed_from_round
    scorer = session.state.scoring.require_scorer()
    prior = closed_rounds(session.store, hop, scorer, before_round=resumed_from_round, moment=None)

    # Fingerprinted BEFORE the repair, so the corrected set differs by exactly what it changed.
    packages_before = round_packages(cycle, prior, callbacks)
    cycle.replay_priors(prior)
    # Over the cycle's whole history: a fork's lifted rounds made theirs on its parent's ledger.
    standing = campaign_store.standing_rounds(hop, moment=None)

    correction = await apply_correction(cycle, prior, packages_before, dataset, callbacks)
    if correction is not None:
        return correction

    ledger_decisions = standing.decisions

    if not skip_divergence_check:
        optimizer_mismatches = _optimizer_mismatches(prior, cycle.optimizer)
        stale = _stale_generation(campaign_store, hop, prior, resumed_from_round)
        campaign = campaign_store.load_campaign(hop.campaign_id)
        # The declaration never moves; the defaults it is a delta over can, under the same name.
        running = cycle.optimizer.treatment(
            campaign_admits(cycle.config, session.pipeline_schema), session.store.block_packs
        )
        drifted = campaign is not None and defaults_drift(campaign, running=running) is not None
        if not optimizer_mismatches and stale is None and not drifted:
            return None

        def _branch_or_halt(div: ReplayMismatch, *, self_inflicted: bool) -> str:
            """A branch this resume CAUSED takes itself; a change from OUTSIDE is the operator's call."""
            if not fork_on_divergence and not self_inflicted:
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
            new_cycle_id = mint_fork(
                campaign_store,
                hop,
                div.round_num,
                trigger=ForkTrigger.SCORING_DIVERGENCE,
                reason=f"resume_divergence:{div.kind}",
                issued_by=SYSTEM_PRINCIPAL,
            )
            if not self_inflicted:
                # The operator's flag ran the line past a divergence it halts on.
                campaign_store.leave_comparison(
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

        for t in prior:
            div = optimizer_mismatches.get(t.round) or replay_decisions(
                t,
                ledger_decisions.get(t.round, []),
                ruler=cycle.difficulty.ruler_of(t.ruler),
            )
            if div is not None:
                return _branch_or_halt(div, self_inflicted=False)

        # The round about to run has no round file, so the loop above cannot visit it.
        if stale is not None:
            return _branch_or_halt(stale, self_inflicted=True)

    return None
