"""Round-summary renderers — single-caller (``LiveDisplay.on_round_complete``).

Pure: no I/O, no mutation. The progress table, the round-stats block,
and the patience-status footer all run after L1_SCORE:exit and are the
last things that hit the terminal before the next round starts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.presentation.views.display import (
    GREEN,
    RESET,
    YELLOW,
    _node_line,
)
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult


def fmt_elapsed(seconds: float) -> str:
    """Render a wall-clock duration as ``Xm YYs`` or ``Xh YYm``."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def render_progress_table(rounds: list[dict], window: int = 8) -> str:
    """Round-over-round trajectory table: accuracy, composite_fitness, rolling avg, trend, plateau.

    Items in ``rounds`` must have at minimum ``round`` and ``accuracy``.
    The ``Composite`` column is always shown so the operator never has to
    wonder whether composite_fitness was hidden because it equalled
    accuracy on every round so far.
    """
    if not rounds:
        return ""

    header = f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s} {'Rolling Avg':>13s} {'Trend':>8s}"
    lines: list[str] = [_node_line(header)]

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
                trend = "+0.0%  <-- plateau"
            elif d > 0:
                trend = f"+{d:.1%}"
            else:
                trend = f"{d:.1%}"
        if rd.get("round") == "grid":
            rl = "G"
        elif rd.get("label") == "origin":
            rl = "0"
        else:
            rl = str(rd.get("round", "?"))
        comp = rd.get("composite_fitness") if rd.get("composite_fitness") is not None else acc
        row = f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f} {rolling:>12.1%}  {trend}"
        lines.append(_node_line(row))

    if len(accs) >= 3:
        recent_avg = sum(accs[-3:]) / 3
        if all(abs(a - recent_avg) < 0.005 for a in accs[-3:]):
            lines.append(
                _node_line(
                    f"{YELLOW}-- Plateau: rolling avg stable at"
                    f" {recent_avg:.1%} for 3 rounds{RESET}"
                )
            )

    lines.append(_node_line(""))
    return "\n".join(lines)


def render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
) -> str:
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5.

    Best-effort: the pipeline-stats block is wrapped in try/except and
    returns just the hits line when ``round_result.results`` is empty.
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

        from promptpotter.application.optimization.pobb.elimination import (
            get_ranked_items,
            ranked_item_keys_from_schema,
        )
        from promptpotter.application.scoring.metrics import find_rank

        ranked_item_keys = ranked_item_keys_from_schema(pipeline_schema)
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

        # Recall@k requires a ranker / candidate_source node to produce
        # ranked_items; for llm_only-style pipelines (single-shot
        # generation tagged ``node_role: ranker``) the item shape can't be
        # matched against ground_truth, so the previous unconditional
        # "Recall: top-1=0% top-5=0%" was misleading. Skip when no
        # ranked-item-emitting node exists, OR when find_rank could not
        # place ground_truth for any valid sample (rank-shaped data
        # missing).
        valid = [r for r in results if not is_error_result(r)]
        if valid and ranked_item_keys:
            ranks = [
                find_rank(
                    get_ranked_items(r, ranked_item_keys),
                    r.get("ground_truth", ""),
                )
                for r in valid
            ]
            if any(rk is not None for rk in ranks):

                def recall_at_k(k: int) -> float:
                    return sum(1 for rk in ranks if rk is not None and rk <= k) / len(valid)

                lines.append(
                    _node_line(f"Recall: top-1={recall_at_k(1):.0%} top-5={recall_at_k(5):.0%}")
                )
    except Exception:
        pass

    return "\n".join(lines)


def render_patience_status(improved: bool, l1_stall_count: int, l1_patience: int) -> str:
    """Green tick on improvement; yellow patience counter on stall.

    The patience counter is informational — exhausting it does not stop
    the loop on its own (L2/L3 escalation may extend). The actual stop
    banner is printed by the runner at cycle teardown with the real
    stop_reason; printing a speculative "Stopping" line here previously
    contradicted the next round actually running.
    """
    if improved:
        return _node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}")
    return _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")


__all__ = [
    "fmt_elapsed",
    "render_patience_status",
    "render_progress_table",
    "render_round_stats",
]
