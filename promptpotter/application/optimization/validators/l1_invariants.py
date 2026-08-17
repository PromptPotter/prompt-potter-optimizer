"""The round-local + cross-round COLLAPSE gates: a proposal that mutates nothing, duplicates a
sibling, or re-proposes an idea a prior round already measured and lost. Each rejection is a
synthetic-0 candidate that never burned an LLM call.

Distinct from ``l1_strict.py``, which checks a proposal against the pipeline SCHEMA. These three
compare proposals against each other and against history, so they need no schema at all — and the
cross-round one is the loop's most destructive rejection, which is why every bound on it
(measured losses only, a stricter threshold, the never-empty-a-round valve) lives here beside it."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.candidate_diff import (
    IDEA_MATCH_REJECT,
    candidate_delta,
    candidate_idea,
    same_idea,
)
from promptpotter.domain.escalation_signals import INVARIANT_REASONS, ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal, is_leader_eligible

logger = logging.getLogger(__name__)

__all__ = ["L1YieldStats", "detect_invariants", "lost_ideas"]


@dataclass(frozen=True)
class L1YieldStats:
    """Field names mirror ``RoundDiagnostics.l1_yield`` so callers spread via ``dataclasses.asdict``."""

    l1_yield: float  # n_valid / n_proposed (1.0 when no proposals)
    l1_n_no_op: int
    l1_n_duplicate: int
    # Cross-ROUND collapses (the other two are round-local): re-proposals of an idea a prior
    # round already measured and lost. Defaulted so the many construction sites that predate
    # the gate stay valid — the count is only ever non-zero where prior rounds are passed.
    l1_n_repeat: int = 0
    # Set when the optimizer prompt made L1's own output unparseable — the round then holds zero
    # candidates. `detect_invariants` never sets it (it only sees proposals that exist); it is
    # stamped from `l1_generate`'s return in `generate_or_load_candidates`.
    l1_parse_failure: str | None = None


# The reasons `detect_invariants` emits — a synthetic-0 candidate that never burned an LLM
# call. The SET now lives in `domain/escalation_signals.py`; this module writes the reasons,
# `RoundResult` reads them back to derive its collapse counts, and `presentation/views/display.py`
# filters on them when ranking. Re-exported here because three call sites already import it
# from this module and it is still the validator's own vocabulary.


def lost_ideas(prior_rounds: Sequence[Any]) -> list[tuple[int, frozenset[str]]]:
    """Measured losses only: ``accuracy == 0.0`` on an unmeasured candidate is the absence of
    evidence, not a defeat, and an ε-elimination IS the measurement while a lock-in is its opposite."""
    out: list[tuple[int, frozenset[str]]] = []
    for i, rr in enumerate(prior_rounds):
        parent = rr.prompt_fields
        parent_pp = prior_rounds[i - 1].pipeline_params if i > 0 else None
        for cand in rr.candidate_scores:
            if not cand.total or not is_leader_eligible(cand):
                continue
            if cand.elimination_stopped:
                if cand.elimination_context.get("leader_locked"):
                    continue
            elif cand.matched_parent_accuracy is None or (
                cand.accuracy > cand.matched_parent_accuracy
            ):
                continue
            if fp := candidate_idea(
                cand.prompt_fields, parent, cand.pipeline_params_override, parent_pp
            ):
                out.append((rr.round, fp))
    return out


def detect_invariants(
    proposals: list[CandidateProposal],
    parent_opt_sp: OptSearchPoint,
    parent_pipeline_params: dict[str, Any] | None,
    prior_rounds: Sequence[Any] = (),
) -> L1YieldStats:
    """Rejecting is destructive and leaves no trace, so a repeat is never allowed to EMPTY the
    round — if rejection would leave no live proposal, they are all restored."""
    parent_pp = parent_pipeline_params or {}
    for cp in proposals:
        cp.opt_sp.memory.wounds.validation_failures = [
            vf
            for vf in cp.opt_sp.memory.wounds.validation_failures
            if vf.reason not in INVARIANT_REASONS
        ]
    seen: dict[tuple[Any, ...], int] = {}
    n_no_op = 0
    n_duplicate = 0
    tried = lost_ideas(prior_rounds)
    # Repeats are collected, not applied inline: whether they may be rejected at all depends on
    # how many proposals SURVIVE the other two gates, which is only known after the loop.
    repeats: list[tuple[CandidateProposal, int]] = []
    n_live = 0
    parent_tc = parent_opt_sp.memory.task_context.to_dict()
    for i, cp in enumerate(proposals):
        child = cp.opt_sp
        pf, pp = candidate_delta(
            {f: getattr(child, f) for f in PROMPT_STRING_FIELDS},
            {f: getattr(parent_opt_sp, f) for f in PROMPT_STRING_FIELDS},
            cp.pipeline_params_override,
            parent_pp,
        )
        pf_delta = tuple(pf.items())
        child_tc = child.memory.task_context.to_dict()
        tc_delta = tuple(sorted((k, v) for k, v in child_tc.items() if v != parent_tc.get(k)))
        # json canon (not tuple-of-items) so a nested value — e.g. a slice-6 `layout`
        # dict riding alongside the prose edits — stays hashable for the `seen` sig.
        pp_delta = tuple(sorted((n, p, json.dumps(v, sort_keys=True)) for (n, p), v in pp.items()))
        sig = (pf_delta, tc_delta, pp_delta)
        if not any(sig):
            cp.opt_sp.memory.wounds.validation_failures = [
                *cp.opt_sp.memory.wounds.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value="(no mutation)",
                    allowed=["non-empty mutation"],
                    reason="no_op_variant",
                ),
            ]
            n_no_op += 1
            continue
        if sig in seen:
            twin = seen[sig]
            cp.opt_sp.memory.wounds.validation_failures = [
                *cp.opt_sp.memory.wounds.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"duplicate of C{twin + 1}",
                    allowed=["unique mutation"],
                    reason="duplicate_variant",
                ),
            ]
            n_duplicate += 1
            continue
        seen[sig] = i
        n_live += 1
        # The idea is the words the candidate ADDED to its parent — never the field names, and
        # never the changed field's whole value: both collapse the test into "touched the same
        # field" (see `candidate_idea`).
        fp = candidate_idea(
            {f: getattr(child, f) for f in PROMPT_STRING_FIELDS},
            {f: getattr(parent_opt_sp, f) for f in PROMPT_STRING_FIELDS},
            cp.pipeline_params_override,
            parent_pp,
        )
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
    n_repeat = 0
    if len(repeats) < n_live:
        for cp, echo in repeats:
            cp.opt_sp.memory.wounds.validation_failures = [
                *cp.opt_sp.memory.wounds.validation_failures,
                ValidationFailure(
                    axis="variant",
                    value=f"re-proposes the idea measured and lost in round {echo}",
                    allowed=["an idea this cycle has not already lost with"],
                    reason="repeat_variant",
                ),
            ]
            n_repeat += 1
    elif repeats:
        logger.debug(
            "repeat_variant: %d/%d proposals re-propose a lost idea — none rejected "
            "(rejecting all would leave the round with no candidates)",
            len(repeats),
            n_live,
        )
    n = len(proposals)
    yield_ = (n - n_no_op - n_duplicate - n_repeat) / n if n else 1.0
    return L1YieldStats(
        l1_yield=yield_,
        l1_n_no_op=n_no_op,
        l1_n_duplicate=n_duplicate,
        l1_n_repeat=n_repeat,
    )
