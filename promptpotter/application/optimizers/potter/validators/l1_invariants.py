from __future__ import annotations

import logging
from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
from promptpotter.domain.arms import Cut, Eliminated, Measured, Proposal
from promptpotter.domain.candidate_diff import (
    IDEA_MATCH_REJECT,
    candidate_delta,
    candidate_idea,
    same_idea,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.results import RoundResult
from promptpotter.domain.wounds import INVARIANT_REASONS, ValidationFailure
from promptpotter.shared.hashing import shapes_optimizer_prompt

logger = logging.getLogger(__name__)


@shapes_optimizer_prompt
def lost(rr: RoundResult, key: ArmKey) -> bool:
    """Measured and lost against its parent, the ONE reading the repeat gate rejects on and
    ALREADY TRIED shows: ε cut it on cells it scored, or its lift on the parent's whole set reads
    below. COLLAPSED is no measurement."""
    fold = rr.folds.get(key)
    if fold is None or not fold.total:
        return False
    match rr.panel.get(key):
        case Cut(reason=Eliminated(elimination=cut)):
            return cut.context.get("gate") == EliminationGate.EPSILON
        case Measured():
            fit = rr.fit(key)
            whole = (
                None if fit is None or fit.vs_reference is None else fit.vs_reference.on_whole_set
            )
            return whole is not None and whole.estimate.side == "below"
        case _:
            return False


def lost_ideas(prior_rounds: Sequence[RoundResult]) -> list[tuple[int, frozenset[str]]]:
    """Each read against the round BEFORE its own."""
    out: list[tuple[int, frozenset[str]]] = []
    for parent_round, rr in pairwise(prior_rounds):
        if (head := parent_round.head) is None:
            continue
        parent, parent_pp = head.individual.prompts, head.individual.pipeline_params
        for key, arm in rr.panel.items():
            if not lost(rr, key):
                continue
            prompts = arm.individual.prompts
            if fp := candidate_idea(prompts, parent, arm.overlay, parent_pp):
                out.append((rr.round, fp))
    return out


def detect_invariants(
    proposals: list[Proposal],
    parent_opt_sp: OptSearchPoint,
    parent_pipeline_params: dict[str, Any] | None,
    prior_rounds: Sequence[RoundResult] = (),
) -> list[Proposal]:
    """Each proposal with its invariant failures re-derived: any it carried in are dropped first."""
    parent_pp = parent_pipeline_params or {}
    failures = [
        [vf for vf in cp.validation_failures if vf.reason not in INVARIANT_REASONS]
        for cp in proposals
    ]
    seen: dict[tuple[Any, ...], int] = {}
    tried = lost_ideas(prior_rounds)
    # Collected, not applied inline: rejecting depends on how many SURVIVE the other two gates.
    repeats: list[tuple[int, int]] = []
    n_live = 0
    # Whole prompts, so a variant that only moved its shots, or dropped every one, is an edit.
    parent_fields = parent_opt_sp.prompts
    for i, cp in enumerate(proposals):
        child_fields = cp.individual.prompts
        delta = candidate_delta(child_fields, parent_fields, cp.overlay, parent_pp)
        if not delta:
            failures[i].append(
                ValidationFailure(
                    axis="variant",
                    value="(no mutation)",
                    allowed=["non-empty mutation"],
                    reason="no_op_variant",
                )
            )
            continue
        sig = delta.signature()
        if sig in seen:
            failures[i].append(
                ValidationFailure(
                    axis="variant",
                    value=f"duplicate of C{seen[sig] + 1}",
                    allowed=["unique mutation"],
                    reason="duplicate_variant",
                )
            )
            continue
        seen[sig] = i
        n_live += 1
        # The words ADDED to the parent, never field names or whole values (see `candidate_idea`).
        fp = candidate_idea(child_fields, parent_fields, cp.overlay, parent_pp)
        echo = next(
            (rnd for rnd, prev in tried if same_idea(fp, prev, threshold=IDEA_MATCH_REJECT)),
            None,
        )
        if echo is not None:
            repeats.append((i, echo))

    # A repeat may cost the round a candidate, never the whole round.
    if len(repeats) < n_live:
        for i, echo in repeats:
            failures[i].append(
                ValidationFailure(
                    axis="variant",
                    value=f"re-proposes the idea measured and lost in round {echo}",
                    allowed=["an idea this cycle has not already lost with"],
                    reason="repeat_variant",
                )
            )
    elif repeats:
        logger.debug(
            "repeat_variant: %d/%d proposals re-propose a lost idea — none rejected "
            "(rejecting all would leave the round with no candidates)",
            len(repeats),
            n_live,
        )
    return [
        cp.model_copy(update={"validation_failures": vfs})
        for cp, vfs in zip(proposals, failures, strict=True)
    ]
