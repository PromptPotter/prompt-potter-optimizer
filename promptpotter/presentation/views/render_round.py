"""Round-summary renderers — progress table, round stats, patience banner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.presentation.views.display_primitives import (
    GREEN,
    RED,
    RESET,
    YELLOW,
    _node_line,
)
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.results import RoundResult
    from promptpotter.domain.pipeline_schema import PipelineSchema


def render_progress_table(
    rounds: list[dict],
    window: int = 8,
    *,
    framed: bool = True,
) -> str:
    """Round-over-round trajectory table: accuracy, composite, rolling avg, trend, plateau.

    Items in ``rounds`` must have at minimum ``round`` and ``accuracy``.
    The ``Composite`` column is always shown so the operator never has to
    wonder whether composite was hidden because it equalled accuracy on
    every round so far.

    ``framed=True`` (default) wraps each line in ``_node_line()`` for
    box-drawing terminals (live CLI / notebook live view). ``framed=False``
    emits plain text with a ``PROGRESS`` title for plain-text reports.
    """
    if not rounds:
        return "" if framed else "No rounds to display."

    def wrap(s: str) -> str:
        return _node_line(s) if framed else s

    row_rnd_w = 5 if framed else 7
    row_comp_w = 9 if framed else 10
    trend_w = 8 if framed else 10
    plateau_step = "+0.0%  <-- plateau" if framed else "+0.0% plateau"

    header = (
        f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
        f" {'Rolling Avg':>13s} {'Trend':>{trend_w}s}"
    )

    lines: list[str] = []
    if framed:
        lines.append(wrap(header))
    else:
        lines.append("\nPROGRESS")
        lines.append(f"\n  {header}")

    accs: list[float] = []
    for rd in rounds:
        acc = rd.get("accuracy") or 0
        accs.append(acc)
        rolling = sum(accs[-window:]) / len(accs[-window:])
        if len(accs) <= 1:
            trend = "-"
        else:
            d = acc - accs[-2]
            if abs(d) < 0.001:
                trend = plateau_step
            elif d > 0:
                trend = f"+{d:.1%}"
            else:
                trend = f"{d:.1%}"
        rl = "G" if rd.get("round") == "grid" else str(rd.get("round", "?"))
        comp = rd.get("composite") if rd.get("composite") is not None else acc
        row = f"  {rl:<{row_rnd_w}s} {acc:>8.1%} {comp:>{row_comp_w}.4f} {rolling:>12.1%}  {trend}"
        lines.append(wrap(row))

    if len(accs) >= 3:
        recent_avg = sum(accs[-3:]) / 3
        if all(abs(a - recent_avg) < 0.005 for a in accs[-3:]):
            if framed:
                lines.append(
                    wrap(
                        f"{YELLOW}-- Plateau: rolling avg stable at"
                        f" {recent_avg:.1%} for 3 rounds{RESET}"
                    )
                )
            else:
                lines.append(f"  -- Plateau: rolling avg stable at {recent_avg:.1%} for 3 rounds")

    if framed:
        lines.append(wrap(""))
    return "\n".join(lines)


def render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5.

    Best-effort: the pipeline-stats block is wrapped in try/except and
    returns an empty string when ``round_result.results`` is empty.
    """
    lines: list[str] = []
    hits = round_result.hits
    total = round_result.total
    deprecated = round_result.deprecated
    if total == 0 and round_result.candidate_scores:
        best = max(round_result.candidate_scores, key=lambda s: s.get("accuracy", 0))
        hits = best.get("hits", 0)
        total = best.get("total", 0)
        deprecated = best.get("deprecated", 0)
    suffix = f"  ({deprecated} deprecated)" if deprecated else ""
    lines.append(
        _node_line(
            f"hits: {hits}/{total}{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    if not round_result.results:
        return "\n".join(lines)

    try:
        from collections import Counter

        from promptpotter.application.optimization.utils import (
            candidate_keys_from_schema,
            get_candidates,
        )
        from promptpotter.application.scoring.metrics import find_rank

        candidate_keys = candidate_keys_from_schema(pipeline_schema)
        results = round_result.results
        n_results = len(results)
        terminations: Counter[str] = Counter()
        degraded = 0
        for r in results:
            pd = r.get("pipeline_data") or {}
            terminations[pd.get("terminated_at", "unknown")] += 1
            if (pd.get("diagnostics") or {}).get("warnings"):
                degraded += 1

        if terminations:
            lines.append(
                _node_line(
                    f"Pipeline: {' | '.join(f'{k}:{v}' for k, v in terminations.most_common())}"
                )
            )
        if degraded > 0:
            lines.append(_node_line(f"Degradation: {degraded / n_results:.0%}"))

        valid = [r for r in results if not is_error_result(r)]
        if valid:

            def recall_at_k(k: int) -> float:
                hit_count = 0
                for r in valid:
                    rank = find_rank(
                        get_candidates(r, candidate_keys),
                        r.get("ground_truth", ""),
                    )
                    if rank is not None and rank <= k:
                        hit_count += 1
                return hit_count / len(valid)

            lines.append(
                _node_line(f"Recall: top-1={recall_at_k(1):.0%} top-5={recall_at_k(5):.0%}")
            )
    except Exception:
        pass

    return "\n".join(lines)


def render_patience_status(
    improved: bool,
    l1_stall_count: int,
    l1_patience: int,
) -> str:
    """Green tick on improvement; yellow patience counter; red stop on exhaustion."""
    lines: list[str] = []
    if improved:
        lines.append(_node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
        return "\n".join(lines)
    lines.append(
        _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")
    )
    if l1_stall_count >= l1_patience:
        lines.append(
            _node_line(
                f"{RED}Stopping: patience exhausted ({l1_patience} consecutive stalls){RESET}"
            )
        )
    return "\n".join(lines)
