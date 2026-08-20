"""Resume entry point — rescore prior rounds under the active scorer, then halt or fork on
divergence. The repair that makes a holed round re-derive is ``repair.py``: incompleteness and
divergence are different questions, so the repair runs regardless of config scope and this file
short-circuits on a ``NONE`` / ``POLICY_ONLY`` diff while the repair never does."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign_config import freeze_campaign_config
from promptpotter.application.knobs import DiffScope, classify_config_diff
from promptpotter.application.optimization.resume_and_fork.fork_siblings import (
    ForkResult,
    _mint_fork,
)
from promptpotter.application.optimization.resume_and_fork.repair import (
    apply_correction,
    round_packages,
)
from promptpotter.application.optimization.resume_and_fork.replayers import (
    ReplayMismatch,
    replay_decisions,
)
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_records import ForkSpec, ForkTrigger
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_decisions,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import ResumeDivergenceError

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.cycle import Cycle
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
    """Is the round about to run holding candidates its own predecessor no longer matches? Both
    sides are persisted, so this survives a restart. No recorded digest ⇒ a divergence, not a shrug."""
    from promptpotter.domain.results import round_document_digest

    cached = campaign_store.load_round_candidates(hop, resumed_from_round)
    if cached is None or not prior:
        return None
    _candidates, consumed = cached
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


def _optimizer_mismatches(prior: list[RoundResult]) -> dict[int, ReplayMismatch]:
    """Rounds produced by a DIFFERENT optimizer than the one loaded now. Asked PER ROUND so an edit
    forks from where it bites; an unstamped round is REPORTED, never guessed."""
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        compute_optimizer_prompt_hashes,
    )

    current = compute_optimizer_prompt_hashes()
    out: dict[int, ReplayMismatch] = {}
    unstamped = [t.round for t in prior if not t.optimizer_prompt_hashes]
    if unstamped:
        logger.warning(
            "Round(s) %s carry no optimizer stamp, so whether they ran under the optimizer "
            "loaded now cannot be asked — they are neither confirmed nor diverged.",
            ", ".join(str(r) for r in unstamped),
        )
    for t in prior:
        recorded = t.optimizer_prompt_hashes
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
    """Rescore prior rounds under the active scorer; halt or fork on divergence. Short-circuits on a
    ``NONE`` / ``POLICY_ONLY`` config diff — the parent's data trace is fully valid."""
    sc = session.scoring
    scorer = sc.scorer
    assert scorer is not None, "session.scoring.scorer required for divergence replay"
    prior = campaign_store.load_rounds_range(hop, 0, resumed_from_round - 1)

    def _rescore(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = list(items or [])
        rescore_results(out, scorer, sc.scorer_id, sc.scorer_formula)
        return out

    for t in prior:
        _rescore(t.results)
        for items in t.all_candidate_results.values():
            _rescore(items)

    origin_results_rescored = _rescore(cycle.tracking.current_results)

    # Fingerprinted BEFORE any repair, from ONE cycle, so both sets differ by exactly what the
    # repair changed. Deep copies because the repair mutates `prior` in place. No pre-replay:
    # `_round_packages` seeds every round's state itself, k=0 included.
    packages_before = round_packages(cycle, [t.model_copy(deep=True) for t in prior])

    correction = await apply_correction(
        campaign_store, hop, prior, packages_before, session, cycle, dataset
    )
    if correction is not None:
        return correction

    # Read the decisions once, from the cycle's OWN ledger — a fork carries the lifted rounds'
    # records because the mint copies them, the same reason it copies their round files.
    ledger_decisions = scan_ledger_decisions(CycleLayout(campaign_store.cycle_dir(hop)).ledger)

    if not skip_divergence_check:
        # Both ABOVE the config short-circuit, deliberately: optimizer prompts and layouts are
        # not KNOBS, so `DiffScope.NONE` says nothing about them; and a stale generation is the
        # one check answering for an EARLIER resume, which by now diffs clean.
        optimizer_mismatches = _optimizer_mismatches(prior)
        stale = _stale_generation(campaign_store, hop, prior, resumed_from_round)
        campaign = campaign_store.load_campaign(hop.campaign_id)
        frozen = campaign.config if campaign is not None else {}
        scope, diffed = classify_config_diff(cycle.config, frozen)
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
                # Refresh the campaign snapshot so future resumes diff
                # against current state.
                campaign_store.update_campaign(
                    hop.campaign_id, {"config": freeze_campaign_config(cycle.config)}
                )
            return None
        if scope is DiffScope.DATA_AFFECTING and diffed:
            logger.info(
                "Resume: data-affecting config diff (%s); running divergence check",
                ", ".join(diffed),
            )

        def _branch_or_halt(
            div: ReplayMismatch, survivors: list[RoundResult], *, self_inflicted: bool
        ) -> ForkResult:
            """The ONE exit every divergence takes: branch at ``div.round_num``, or halt. Branches this resume
            CAUSED take themselves; a change from OUTSIDE is the operator's call."""
            if not fork_on_divergence and not self_inflicted:
                raise ResumeDivergenceError(
                    round_num=div.round_num,
                    kind=div.kind,
                    recorded_outcome=div.recorded_outcome,
                    current_outcome=div.current_outcome,
                    diagnostics={
                        "scorer_id": sc.scorer_id,
                        "fork_hint": (
                            "rerun `resume --fork-on-divergence` to branch a new cycle "
                            "here; this one keeps what the superseded package produced"
                        ),
                    },
                )
            new_cycle_id = _mint_fork(
                campaign_store,
                hop,
                session.session_id,
                div.round_num,
                ForkSpec(
                    trigger=ForkTrigger.SCORING_DIVERGENCE,
                    reason=f"resume_divergence:{div.kind}",
                    issued_by="system",
                ),
                surviving_rounds=survivors,
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
            # A different OPTIMIZER produced it, or its OUTCOME no longer re-derives. One
            # `ReplayMismatch`, one exit. Both come from outside this resume.
            div = optimizer_mismatches.get(t.round) or replay_decisions(
                t,
                ledger_decisions.get(t.round),
                origin_results=origin_results_rescored,
                ruler=cycle.ruler,
            )
            if div is not None:
                return _branch_or_halt(div, list(prior[:i]), self_inflicted=False)

        # The round about to run has no round file, so the loop above cannot visit it — same
        # exit, every prior round crossing the cut. This one IS ours: a generation left stale
        # by an earlier resume is the resume doing its job, so it branches without asking.
        if stale is not None:
            return _branch_or_halt(stale, list(prior), self_inflicted=True)

    return None
