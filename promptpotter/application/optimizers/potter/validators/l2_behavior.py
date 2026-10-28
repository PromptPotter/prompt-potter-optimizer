"""Dataset-INDEPENDENT checks, so the metric anchors optimizer-prompt iteration across datasets."""

from __future__ import annotations

import re
from typing import Any

from promptpotter.application.optimizers.nodes import CheckResult
from promptpotter.application.optimizers.potter.validators.behavior_base import (
    CheckFn,
    ValidatorContext,
    judged,
)
from promptpotter.domain.round_audit import RoundAudit

L2_RATIONALE_FLOOR_CHARS = 40

# A bare integer is not a citation: `\d` alone passes "round 2".
_EVIDENCE_RE = re.compile(r"#\d+|\d+\.\d+|\d+\s*%|\d+\s*/\s*\d+")


def extract_l2_output(audit: RoundAudit) -> dict[str, Any]:
    block = audit.nodes.get("l2_context")
    response = None if block is None else block.output.response
    return response if isinstance(response, dict) else {}


L2Output = dict[str, Any]


def _check_rationale_substantive(
    out: L2Output, _audit: RoundAudit, _ctx: ValidatorContext
) -> tuple[bool, str]:
    rationale = str(out.get("rationale") or "").strip()
    if len(rationale) >= L2_RATIONALE_FLOOR_CHARS:
        return True, f"rationale {len(rationale)} chars"
    return False, f"rationale {len(rationale)} chars < floor {L2_RATIONALE_FLOOR_CHARS}"


def _check_evidence_anchored(
    out: L2Output, _audit: RoundAudit, _ctx: ValidatorContext
) -> tuple[bool, str]:
    if axis := str(out.get("axis_targeted") or "").strip():
        return True, f"axis_targeted={axis!r}"
    if _EVIDENCE_RE.search(str(out.get("rationale") or "")):
        return True, "rationale cites a sample/number"
    return False, "no axis_targeted and rationale cites no sample/axis/number"


def _check_targets_l1_surface(
    out: L2Output, _audit: RoundAudit, _ctx: ValidatorContext
) -> tuple[bool, str]:
    """``axis_targeted`` is not a surface: it is prose, and L1 reads its axes from measurement."""
    if touched := [name for name in ("l1_layout", "l1_overrides") if out.get(name)]:
        return True, f"L2 touched: {touched}"
    return False, "L2 fired but changed nothing L1 reads"


L2_CHECK_REGISTRY: dict[str, CheckFn[L2Output]] = {
    "l2_rationale_substantive": _check_rationale_substantive,
    "l2_evidence_anchored": _check_evidence_anchored,
    "l2_targets_l1_surface": _check_targets_l1_surface,
}


def run_all_l2_checks(audit: RoundAudit, ctx: ValidatorContext) -> list[CheckResult]:
    """EMPTY when L2 did not fire: an absent fire is not a conformance failure."""
    out = extract_l2_output(audit)
    return judged(L2_CHECK_REGISTRY, out, audit, ctx) if out else []
