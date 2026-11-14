"""A resume asks the one validity question ``ab`` asks (`ab_replay.py::replay_verdict`)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.resume_and_fork.ab_replay import line_divergence
from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut, cut_fork
from promptpotter.application.bench.resume_and_fork.repair import apply_correction
from promptpotter.application.bench.resume_and_fork.replayers import ReplayMismatch
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.domain.results import round_document_digest
from promptpotter.domain.run_mode import OnDivergence
from promptpotter.domain.run_records import ComparisonExit, ForkTrigger
from promptpotter.shared.errors import ResumeDivergenceError
from promptpotter.shared.identity import SYSTEM_PRINCIPAL

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.run_mode import RunMode
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


async def resume_with_divergence_check(
    cycle: Cycle, dataset: list[Any], mode: RunMode
) -> ForkCut | None:
    """The cycle opens on the standing the last close of the line it continues stated, a fork's
    included; a fork is answered cut and untaken (`runner/mint.py::onto_fork` takes it)."""
    session = cycle.session
    campaign_store, hop = session.store.campaigns, session.hop
    resumed_from_round = session.state.resumed_from_round
    scorer = session.state.scoring.scorer
    prior = closed_rounds(session.store, hop, scorer, before_round=resumed_from_round, moment=None)
    cycle.replay_priors(prior)

    # Decided on the round this run enters, before its rewind is written: a halt displaces nothing.
    rewind, accept = mode.from_round, mode.on_divergence == OnDivergence.ACCEPT
    standing = campaign_store.standing_rounds(hop, moment=None)
    if rewind is not None:
        standing = standing.displaced(resumed_from_round, resumed_from_round)
    found = None if accept else _divergence(cycle, prior, standing)
    if found is not None and not found[1] and mode.on_divergence == OnDivergence.HALT:
        div = found[0]
        raise ResumeDivergenceError(
            round_num=div.round_num,
            kind=div.kind,
            recorded_outcome=div.recorded_outcome,
            current_outcome=div.current_outcome,
            diagnostics={"scorer_id": scorer.id},
        )
    if rewind is not None:
        campaign_store.rewind_to_round(hop, rewind)
        campaign_store.leave_comparison(hop, ComparisonExit.REWIND)
    if accept:
        campaign_store.leave_comparison(hop, ComparisonExit.NO_DIVERGENCE_CHECK)

    fork = await apply_correction(cycle, prior, dataset)
    if fork is None and found is not None:
        fork = _branch(cycle, prior, *found)
    line = hop if fork is None else fork.child
    cycle.standing = campaign_store.standing_rounds(line, moment=None).standing
    return fork


def _branch(
    cycle: Cycle, prior: list[RoundResult], div: ReplayMismatch, self_inflicted: bool
) -> ForkCut:
    session = cycle.session
    hop = session.hop
    fork = cut_fork(
        session.store,
        hop,
        div.round_num,
        trigger=ForkTrigger.SCORING_DIVERGENCE,
        reason=f"resume_divergence:{div.kind}",
        issued_by=SYSTEM_PRINCIPAL,
    )
    if not self_inflicted:
        # The operator's flag ran the line past a divergence it halts on.
        session.store.campaigns.leave_comparison(fork.child, ComparisonExit.FORK_ON_DIVERGENCE)
    if div.round_num == 0:
        cycle.reopen_origin()
    else:
        cycle.replay_priors([t for t in prior if t.round < div.round_num])
    logger.warning(
        "Resume diverged at round %d (%s); branched → %s. That cycle is the "
        "continuation; this one keeps what the superseded package produced.",
        div.round_num,
        div.kind,
        fork.child.cycle_id,
    )
    return fork


def _divergence(
    cycle: Cycle, prior: list[RoundResult], standing: StandingRounds
) -> tuple[ReplayMismatch, bool] | None:
    """The first divergence of the line, and whether this resume CAUSED it."""
    session = cycle.session
    div = line_divergence(session, cycle.config, cycle.optimizer, prior, standing.decisions)
    if div is not None:
        return div, False
    # The round about to run has no round file, so the walk cannot visit it.
    stale = _stale_generation(standing, prior, session.state.resumed_from_round, cycle.optimizer)
    return None if stale is None else (stale, True)
