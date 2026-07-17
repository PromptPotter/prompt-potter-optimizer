"""L2 behaviour checks — programmatic conformance for one ``l2_context`` fire.

Parallel to :mod:`l1_behavior`. Each check is a pure
``(round_dict, ctx) -> CheckResult`` function over a round whose audit
carries an ``l2_context`` node (i.e. L2 fired that round). Conformance
here is **dataset-independent**: it scores how well the optimizer LLM
honoured the ``l2_context`` meta-prompt's contract, not whether the task
accuracy moved. That decoupling is what makes the metric a usable anchor
for iterating the meta-prompt across many datasets.

Adding a check is one function plus one entry in ``L2_CHECK_REGISTRY``.
The registry is the single source of truth, so every surface that enumerates the
checks (``review.md``, the round-1 verdict) reads the same set.

The L2 output shape walked here is :class:`L2ContextOutput`
(``dispatch/schemas.py``): ``task_context`` refinement dict,
``axis_targeted``, ``l1_layout``, ``l1_overrides``, supplemental
rules / situational examples, and ``rationale``. ``L2ContextOutput`` has
``extra="forbid"`` and carries no ``pipeline_params`` field, so the
"L2 never touches pipeline_params" rule is enforced by the schema — no
behaviour check needed for it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from promptpotter.application.optimization.validators.l1_behavior import (
    CheckResult,
    ValidatorContext,
)
from promptpotter.domain.search_point import TaskDecomposition

__all__ = [
    "L2_CHECK_REGISTRY",
    "L2_RATIONALE_FLOOR_CHARS",
    "extract_l2_output",
    "l2_fired",
    "run_all_l2_checks",
]


# An L2 rationale below this is a stub, not a diagnosis — mirrors the
# l3_plan length floor in `l3_output.py`.
L2_RATIONALE_FLOOR_CHARS = 40

# A digit or a `#N` sample reference — the cheap evidence-anchor signal.
_EVIDENCE_RE = re.compile(r"#\d+|\d")

CheckFn = Callable[[dict[str, Any], ValidatorContext], CheckResult]


def extract_l2_output(round_dict: dict[str, Any] | None) -> dict[str, Any]:
    """Return the parsed ``l2_context`` response on a round/audit dict.

    Walks ``nodes.l2_context.output.response`` — the ``L2ContextOutput``
    shape. Empty dict when L2 did not fire this round or the response is
    malformed.
    """
    if not round_dict:
        return {}
    nodes = round_dict.get("nodes") or {}
    node = nodes.get("l2_context") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    return response if isinstance(response, dict) else {}


def l2_fired(round_dict: dict[str, Any] | None) -> bool:
    """True iff this round's audit carries a non-empty ``l2_context`` node."""
    return bool(extract_l2_output(round_dict))


# --- checks ----------------------------------------------------------------


def _check_rationale_substantive(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """L2's ``rationale`` must carry real diagnostic content, not a stub."""
    rationale = str(extract_l2_output(round_dict).get("rationale") or "").strip()
    if len(rationale) >= L2_RATIONALE_FLOOR_CHARS:
        return CheckResult("l2_rationale_substantive", True, f"rationale {len(rationale)} chars")
    return CheckResult(
        "l2_rationale_substantive",
        False,
        f"rationale {len(rationale)} chars < floor {L2_RATIONALE_FLOOR_CHARS}",
    )


def _check_evidence_anchored(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """L2's refinement must be evidence-anchored — name a targeted axis or
    cite a specific sample / number, not a speculative 'maybe try X'.

    Per ``promptpotter/CLAUDE.md`` L2 contract: the refinement *cites a
    specific axis, sample, or yield number*; speculative refinements are
    out of contract.
    """
    out = extract_l2_output(round_dict)
    axis = str(out.get("axis_targeted") or "").strip()
    if axis:
        return CheckResult("l2_evidence_anchored", True, f"axis_targeted={axis!r}")
    if _EVIDENCE_RE.search(str(out.get("rationale") or "")):
        return CheckResult("l2_evidence_anchored", True, "rationale cites a sample/number")
    return CheckResult(
        "l2_evidence_anchored",
        False,
        "no axis_targeted and rationale cites no sample/axis/number",
    )


def _check_task_context_not_verbatim(
    round_dict: dict[str, Any], ctx: ValidatorContext
) -> CheckResult:
    """A proposed ``task_context`` refinement must change ≥1 field vs the
    prior OSP framing — a no-op refinement is a wasted L2 fire."""
    proposed = extract_l2_output(round_dict).get("task_context")
    if not isinstance(proposed, dict) or not proposed:
        return CheckResult("l2_task_context_not_verbatim", True, "no task_context proposed")
    prior_raw = ctx.opt_search_point.get("task_context") if ctx.opt_search_point else None
    prior = TaskDecomposition.coerce(prior_raw)
    if prior.merge_changes_nothing(proposed):
        return CheckResult(
            "l2_task_context_not_verbatim",
            False,
            f"all {len(proposed)} proposed task_context field(s) repeat the prior framing",
        )
    return CheckResult("l2_task_context_not_verbatim", True, "proposal carries a real delta")


def _check_targets_l1_surface(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """An L2 fire must change *something* L1 reads — ``task_context``,
    ``l1_layout``, or ``l1_overrides``. A fire that changes nothing is a
    wasted escalation."""
    out = extract_l2_output(round_dict)
    if not out:
        return CheckResult("l2_targets_l1_surface", True, "L2 did not fire")
    touched = [
        name
        for name in (
            "task_context",
            "l1_layout",
            "l1_overrides",
        )
        if out.get(name)
    ]
    if touched:
        return CheckResult("l2_targets_l1_surface", True, f"L2 touched: {touched}")
    return CheckResult("l2_targets_l1_surface", False, "L2 fired but changed nothing L1 reads")


# --- registry --------------------------------------------------------------

L2_CHECK_REGISTRY: dict[str, CheckFn] = {
    "l2_rationale_substantive": _check_rationale_substantive,
    "l2_evidence_anchored": _check_evidence_anchored,
    "l2_task_context_not_verbatim": _check_task_context_not_verbatim,
    "l2_targets_l1_surface": _check_targets_l1_surface,
}


def run_all_l2_checks(round_dict: dict[str, Any], ctx: ValidatorContext) -> list[CheckResult]:
    """Run every L2 behaviour check against one round, in registry order.

    Empty list when L2 did not fire this round — there is nothing to
    score, and an absent fire must not count as a conformance failure.
    """
    if not l2_fired(round_dict):
        return []
    return [fn(round_dict, ctx) for fn in L2_CHECK_REGISTRY.values()]
