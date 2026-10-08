"""The round-local + cross-round COLLAPSE gates: a proposal that mutates nothing, duplicates a
sibling, or re-proposes an idea a prior round already measured and lost. Each rejection is a
synthetic-0 candidate that never burned an LLM call.

Distinct from ``l1_strict.py``, which checks a proposal against the pipeline SCHEMA. These three
compare proposals against each other and against history, so they need no schema at all — and the
cross-round one is the loop's most destructive rejection, which is why every bound on it
(measured losses only, a stricter threshold, the never-empty-a-round valve) lives here beside it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.domain.candidate_diff import (
    IDEA_MATCH_REJECT,
    candidate_delta,
    candidate_idea,
    same_idea,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    ArmOutcome,
    CandidateProposal,
    RoundResult,
    is_leader_eligible,
)
from promptpotter.domain.wounds import INVARIANT_REASONS, ValidationFailure

logger = logging.getLogger(__name__)

__all__ = ["detect_invariants", "lost_ideas"]


def lost_ideas(prior_rounds: Sequence[RoundResult]) -> list[tuple[int, frozenset[str]]]:
    """Measured losses only, each on a paired reading: an ε cut, PoBB's posterior, or a walked arm
    whose ``reference_lift`` interval sits wholly below zero. Each reads against the round BEFORE its own."""
    out: list[tuple[int, frozenset[str]]] = []
    for parent_round, rr in pairwise(prior_rounds):
        parent, parent_pp = parent_round.prompt_fields, parent_round.pipeline_params
        for cand in rr.candidate_scores:
            if not cand.total or not is_leader_eligible(cand):
                continue
            if cand.outcome is ArmOutcome.ELIMINATED:
                # ε alone measured this arm against its priors; COLLAPSED is no measurement.
                if cand.elimination_context.get("gate") != EliminationGate.EPSILON:
                    continue
            elif cand.reference_lift_ci_hi is None or cand.reference_lift_ci_hi >= 0.0:
                continue
            if fp := candidate_idea(cand.prompt_fields, parent, cand.pipeline_overlay, parent_pp):
                out.append((rr.round, fp))
    return out


def detect_invariants(
    proposals: list[CandidateProposal],
    parent_opt_sp: OptSearchPoint,
    parent_pipeline_params: dict[str, Any] | None,
    prior_rounds: Sequence[RoundResult] = (),
) -> None:
    """Stamps each collapse on its proposal. A repeat is never allowed to EMPTY the round — if
    rejection would leave no live proposal, none is rejected."""
    parent_pp = parent_pipeline_params or {}
    for cp in proposals:
        cp.validation_failures = [
            vf for vf in cp.validation_failures if vf.reason not in INVARIANT_REASONS
        ]
    seen: dict[tuple[Any, ...], int] = {}
    tried = lost_ideas(prior_rounds)
    # Repeats are collected, not applied inline: whether they may be rejected at all depends on
    # how many proposals SURVIVE the other two gates, which is only known after the loop.
    repeats: list[tuple[CandidateProposal, int]] = []
    n_live = 0
    # Both whole lists, so a variant that only moved its shots is an edit and one that dropped
    # every shot is too.
    parent_fields = {**parent_opt_sp.prompt_fields(), "shot_ids": parent_opt_sp.shot_ids}
    for i, cp in enumerate(proposals):
        child_fields = {**cp.opt_sp.prompt_fields(), "shot_ids": cp.opt_sp.shot_ids}
        delta = candidate_delta(child_fields, parent_fields, cp.pipeline_overlay, parent_pp)
        if not delta:
            cp.validation_failures = [
                *cp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value="(no mutation)",
                    allowed=["non-empty mutation"],
                    reason="no_op_variant",
                ),
            ]
            continue
        sig = delta.signature()
        if sig in seen:
            twin = seen[sig]
            cp.validation_failures = [
                *cp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"duplicate of C{twin + 1}",
                    allowed=["unique mutation"],
                    reason="duplicate_variant",
                ),
            ]
            continue
        seen[sig] = i
        n_live += 1
        # The idea is the words the candidate ADDED to its parent — never the field names, and
        # never the changed field's whole value: both collapse the test into "touched the same
        # field" (see `candidate_idea`).
        fp = candidate_idea(child_fields, parent_fields, cp.pipeline_overlay, parent_pp)
        echo = next(
            (rnd for rnd, prev in tried if same_idea(fp, prev, threshold=IDEA_MATCH_REJECT)),
            None,
        )
        if echo is not None:
            repeats.append((cp, echo))

    # Safety valve — a repeat may cost the round a candidate, never the whole round. `n_live`
    # counts proposals that cleared no-op + duplicate; if every one of them is also a repeat,
    # none is rejected. The loop then re-tests a known-dead idea for one round, which the
    # ALREADY TRIED panel still marks — strictly better than handing PoBB an empty population
    # and burning the turn on nothing.
    if len(repeats) < n_live:
        for cp, echo in repeats:
            cp.validation_failures = [
                *cp.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"re-proposes the idea measured and lost in round {echo}",
                    allowed=["an idea this cycle has not already lost with"],
                    reason="repeat_variant",
                ),
            ]
    elif repeats:
        logger.debug(
            "repeat_variant: %d/%d proposals re-propose a lost idea — none rejected "
            "(rejecting all would leave the round with no candidates)",
            len(repeats),
            n_live,
        )
