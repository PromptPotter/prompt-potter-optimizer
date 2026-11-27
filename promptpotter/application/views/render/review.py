"""The optimizer's review of its rounds as readout text: each round's checks at its close (its
critique is the round block's note), the cycle's stats at its stop.
``optimizers/nodes.py::Reviewer`` reads; this words it."""

from __future__ import annotations

from collections.abc import Sequence

from promptpotter.application.optimizers.nodes import CheckResult, ReviewReading, ReviewStat
from promptpotter.application.views.render.primitives import _node_line
from promptpotter.domain.round_audit import RoundAudit


def schema_repairs(audit: RoundAudit | None) -> int:
    return 0 if audit is None else sum(len(b.schema_repair_errors) for b in audit.nodes.values())


def _stat(stat: ReviewStat) -> str:
    return "—" if stat.value is None else format(stat.value, stat.spec)


def _check(c: CheckResult) -> str:
    return f"{'✓' if c.passed else '✗'} {c.check_id} — {c.evidence}"


def render_round_review(checks: Sequence[CheckResult], repairs: int) -> list[str]:
    """A round's behaviour checks (scored, never gating) and its schema repairs."""
    lines = [f"behaviour check {_check(c)}" for c in checks]
    if repairs:
        lines.append(f"schema_repair_retries: {repairs}")
    return [_node_line(line) for line in lines]


def render_cycle_review(
    review: ReviewReading | None, audits: Sequence[RoundAudit | None], optimizer: str
) -> list[str]:
    """The cycle's review at a stop: the optimizer's verdict and stats, each check's pass count
    over the rounds, and the share of optimizer calls a schema repair paid twice."""
    if review is None:
        lines = [f"the optimizer's own stats: N/A — `{optimizer}` keeps none"]
    else:
        lines = [f"{s.name}: {_stat(s)}" for s in (*filter(None, [review.verdict]), *review.stats)]
        for check_id in review.check_ids:
            ran = [c for checks in review.checks for c in checks if c.check_id == check_id]
            if ran:
                passed = sum(c.passed for c in ran)
                lines.append(f"behaviour check {check_id}: {passed}/{len(ran)} rounds passed")
    calls = sum(0 if a is None else len(a.nodes) for a in audits)
    if calls:
        repairs = sum(map(schema_repairs, audits))
        lines.append(
            f"schema_repair_retries: {repairs}/{calls} optimizer calls "
            f"({100.0 * repairs / calls:.0f}% paid a second round-trip)"
        )
    return [f"  review · {line}" for line in lines]
