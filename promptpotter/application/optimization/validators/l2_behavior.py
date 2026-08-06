"""L2 behaviour checks — dataset-INDEPENDENT, which is what makes the metric a usable anchor for iterating the optimizer prompt across
datasets. The registry is the SoT; a check for something ``extra="forbid"`` already makes unreachable is noise."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from promptpotter.application.optimization.validators.l1_behavior import (
    CheckResult,
    ValidatorContext,
)

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
    """The parsed ``l2_context`` response on a round dict. Empty dict when L2 did not fire this round, or the response
    was malformed."""
    if not round_dict:
        return {}
    nodes = round_dict.get("nodes") or {}
    node = nodes.get("l2_context") or {}
    response = ((node.get("output") or {}).get("response")) or {}
    return response if isinstance(response, dict) else {}


def l2_fired(round_dict: dict[str, Any] | None) -> bool:
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
    """L2's refinement must be EVIDENCE-ANCHORED — a targeted axis or a cited sample / number, never a speculative
    "maybe try X". The contract is stated in ``promptpotter/CLAUDE.md``."""
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


def _check_targets_l1_surface(round_dict: dict[str, Any], ctx: ValidatorContext) -> CheckResult:
    """An L2 fire must change something L1 READS — ``l1_layout`` or ``l1_overrides``. **This is the instrument deciding whether
    the L2 call earns its cost.** ``axis_targeted`` is not a surface: it is prose, and L1 reads its axes from measurement."""
    out = extract_l2_output(round_dict)
    if not out:
        return CheckResult("l2_targets_l1_surface", True, "L2 did not fire")
    touched = [name for name in ("l1_layout", "l1_overrides") if out.get(name)]
    if touched:
        return CheckResult("l2_targets_l1_surface", True, f"L2 touched: {touched}")
    return CheckResult("l2_targets_l1_surface", False, "L2 fired but changed nothing L1 reads")


# --- registry --------------------------------------------------------------

L2_CHECK_REGISTRY: dict[str, CheckFn] = {
    "l2_rationale_substantive": _check_rationale_substantive,
    "l2_evidence_anchored": _check_evidence_anchored,
    "l2_targets_l1_surface": _check_targets_l1_surface,
}


def run_all_l2_checks(round_dict: dict[str, Any], ctx: ValidatorContext) -> list[CheckResult]:
    """Run every L2 behaviour check in registry order. EMPTY when L2 did not fire this round — there is nothing to score,
    and an absent fire must not count as a conformance failure."""
    if not l2_fired(round_dict):
        return []
    return [fn(round_dict, ctx) for fn in L2_CHECK_REGISTRY.values()]
