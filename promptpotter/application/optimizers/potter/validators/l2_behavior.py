"""Dataset-INDEPENDENT checks, so the metric anchors optimizer-prompt iteration across datasets."""

from __future__ import annotations

import re
from typing import Any

from promptpotter.application.optimizers.nodes import CheckResult
from promptpotter.application.optimizers.potter.validators.behavior_base import (
    CheckFn,
    ValidatorContext,
)
from promptpotter.domain.round_audit import RoundAudit

__all__ = ["run_all_l2_checks"]


L2_RATIONALE_FLOOR_CHARS = 40

# A bare integer is not a citation: `\d` alone passes "round 2".
_EVIDENCE_RE = re.compile(r"#\d+|\d+\.\d+|\d+\s*%|\d+\s*/\s*\d+")


def extract_l2_output(audit: RoundAudit) -> dict[str, Any]:
    block = audit.nodes.get("l2_context")
    response = None if block is None else block.output.response
    return response if isinstance(response, dict) else {}


def _check_rationale_substantive(audit: RoundAudit, ctx: ValidatorContext) -> CheckResult:
    rationale = str(extract_l2_output(audit).get("rationale") or "").strip()
    if len(rationale) >= L2_RATIONALE_FLOOR_CHARS:
        return CheckResult("l2_rationale_substantive", True, f"rationale {len(rationale)} chars")
    return CheckResult(
        "l2_rationale_substantive",
        False,
        f"rationale {len(rationale)} chars < floor {L2_RATIONALE_FLOOR_CHARS}",
    )


def _check_evidence_anchored(audit: RoundAudit, ctx: ValidatorContext) -> CheckResult:
    out = extract_l2_output(audit)
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


def _check_targets_l1_surface(audit: RoundAudit, ctx: ValidatorContext) -> CheckResult:
    """``axis_targeted`` is not a surface: it is prose, and L1 reads its axes from measurement."""
    out = extract_l2_output(audit)
    if not out:
        return CheckResult("l2_targets_l1_surface", True, "L2 did not fire")
    touched = [name for name in ("l1_layout", "l1_overrides") if out.get(name)]
    if touched:
        return CheckResult("l2_targets_l1_surface", True, f"L2 touched: {touched}")
    return CheckResult("l2_targets_l1_surface", False, "L2 fired but changed nothing L1 reads")


L2_CHECK_REGISTRY: dict[str, CheckFn] = {
    "l2_rationale_substantive": _check_rationale_substantive,
    "l2_evidence_anchored": _check_evidence_anchored,
    "l2_targets_l1_surface": _check_targets_l1_surface,
}


def run_all_l2_checks(audit: RoundAudit, ctx: ValidatorContext) -> list[CheckResult]:
    """EMPTY when L2 did not fire: an absent fire is not a conformance failure."""
    if not extract_l2_output(audit):
        return []
    return [fn(audit, ctx) for fn in L2_CHECK_REGISTRY.values()]
