"""``repair.py`` runs regardless of config scope; only the divergence check short-circuits on it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.resume_and_fork.fork_siblings import (
    ForkResult,
    mint_fork,
)
from promptpotter.application.bench.resume_and_fork.repair import apply_correction
from promptpotter.application.bench.resume_and_fork.replayers import (
    ReplayMismatch,
    replay_decisions,
)
from promptpotter.application.knobs import DiffScope, classify_config_diff
from promptpotter.application.pipeline_resolve import frozen_config
from promptpotter.application.scoring.cells import closed_rounds
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import round_document_digest
from promptpotter.domain.run_records import ForkSpec, ForkTrigger
from promptpotter.shared.errors import ResumeDivergenceError

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

logger = logging.getLogger(__name__)

__all__ = ["resume_with_divergence_check"]


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
        recorded = t.optimizer_state.prompt_hashes
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
    campaign_store: CampaignStore,
    hop: CycleHop,
    resumed_from_round: int,
    session: Session,
    cycle: Cycle,
    dataset: list[Any],
    *,
    skip_divergence_check: bool,
    fork_on_divergence: bool = False,
) -> ForkResult | None:
    sc = session.scoring
    scorer = sc.require_scorer()
    prior = closed_rounds(session.store, hop, scorer, before_round=resumed_from_round)

    # For the CYCLE's own state, never as a comparison anchor.
    cycle.tracking.current_results = [
        scorer.grade(cell.facts) for cell in cycle.tracking.current_results
    ]

    # Fingerprinted BEFORE the repair, so the corrected set differs by exactly what it changed.
    packages_before = cycle.optimizer.runtime.round_packages(cycle, prior)
    cycle.replay_priors(prior)

    correction = await apply_correction(
        campaign_store, hop, prior, packages_before, session, cycle, dataset
    )
    if correction is not None:
        return correction

    # Over the cycle's whole history: a fork's lifted rounds made theirs on its parent's ledger.
    ledger_decisions = campaign_store.standing_rounds(hop).decisions

    if not skip_divergence_check:
        # Both ABOVE the config short-circuit: neither is a knob, so a clean diff misses them.
        optimizer_mismatches = _optimizer_mismatches(prior, cycle.optimizer)
        stale = _stale_generation(campaign_store, hop, prior, resumed_from_round)
        campaign = campaign_store.load_campaign(hop.campaign_id)
        frozen = frozen_config(session.store, campaign) if campaign is not None else {}
        scope, diffed = classify_config_diff(
            cycle.config, frozen, arm=campaign is not None and campaign.arm is not None
        )
        if (
            not optimizer_mismatches
            and stale is None
            and scope in (DiffScope.NONE, DiffScope.POLICY_ONLY)
        ):
            if scope is DiffScope.POLICY_ONLY:
                logger.info(
                    "Resume: policy-only config diff (%s); continuing on cycle %s in-place",
                    ", ".join(diffed),
                    hop.cycle_id,
                )
            return None
        if scope in (DiffScope.DATA_AFFECTING, DiffScope.TREATMENT) and diffed:
            logger.info(
                "Resume: %s config diff (%s); running divergence check", scope, ", ".join(diffed)
            )

        def _branch_or_halt(
            div: ReplayMismatch, survivors: list[RoundResult], *, self_inflicted: bool
        ) -> ForkResult:
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
                ForkSpec(
                    trigger=ForkTrigger.SCORING_DIVERGENCE,
                    reason=f"resume_divergence:{div.kind}",
                    issued_by="system",
                ),
            )
            cycle.replay_priors(survivors)
            logger.warning(
                "Resume diverged at round %d (%s); branched → %s. That cycle is the "
                "continuation; this one keeps what the superseded package produced.",
                div.round_num,
                div.kind,
                new_cycle_id,
            )
            return ForkResult(new_cycle_id=new_cycle_id, new_resumed_from_round=div.round_num)

        for i, t in enumerate(prior):
            div = optimizer_mismatches.get(t.round) or replay_decisions(
                t,
                ledger_decisions.get(t.round, []),
                ruler=cycle.difficulty.ruler,
            )
            if div is not None:
                return _branch_or_halt(div, list(prior[:i]), self_inflicted=False)

        # The round about to run has no round file, so the loop above cannot visit it.
        if stale is not None:
            return _branch_or_halt(stale, list(prior), self_inflicted=True)

    return None
