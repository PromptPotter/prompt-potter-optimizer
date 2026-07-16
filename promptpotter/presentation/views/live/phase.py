"""Round-summary renderers (``LiveDisplay.on_round_complete``). Pure: no campaign
I/O, no mutation (errors log, never abort the live readout)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.domain.rendering import display_fitness, round_winner_key
from promptpotter.domain.results import is_round_winner
from promptpotter.presentation.views.display import (
    BOLD,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _node_line,
)
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult

logger = logging.getLogger(__name__)


def fmt_elapsed(seconds: float) -> str:
    """Wall-clock duration as ``Xm YYs`` or ``Xh YYm``."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def render_progress_table(rounds: list[dict[str, Any]], window: int = 8) -> str:
    """Round-over-round trajectory table: accuracy, composite_fitness, rolling avg, trend, plateau."""
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
        comp = display_fitness(rd.get("composite_fitness"), acc)
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
    """hits/total, candidate count, pipeline terminations, degradation%, recall@1/5."""
    lines: list[str] = []
    hits = round_result.hits
    total = round_result.total
    deprecated = round_result.deprecated
    if total == 0 and round_result.candidate_scores:
        # Stand-in when the round-level rollup is empty: the ELECTED winner's row
        # (the label the round adopted), falling back to the shared composite-first
        # display ordering — never a private accuracy-argmax that can star a
        # candidate the engine didn't elect.
        best = next(
            (
                s
                for s in round_result.candidate_scores
                if is_round_winner(s.candidate_id, round_result.winner_id)
            ),
            max(
                round_result.candidate_scores,
                key=lambda s: round_winner_key(s.composite_fitness, s.accuracy),
            ),
        )
        hits = best.hits
        total = best.total
        deprecated = 0
    suffix = f"  ({deprecated} deprecated)" if deprecated else ""
    lines.append(
        _node_line(
            f"hits: {hits}/{total}{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    # Degradation verdict — the served ``round_result.health`` (R-36: rendered,
    # not recomputed). Loudness scales with grade; ``healthy`` stays silent.
    h = round_result.health
    if h is not None and h.grade == "critical":
        lines.append(_node_line(f"{BOLD}{RED}⛔ CRITICAL — {h.suggested_action}{RESET}"))
    elif h is not None and h.grade == "degraded":
        why = (
            f"{h.structural_count + h.transient_count}/{h.samples} samples degraded"
            if (h.structural_count + h.transient_count)
            else f"under-probed (CI {h.ci_lo:.0%}-{h.ci_hi:.0%})"
        )
        lines.append(_node_line(f"{YELLOW}⚠ DEGRADED — {why}; numbers soft{RESET}"))

    if not round_result.results:
        return "\n".join(lines)

    try:
        from collections import Counter

        from promptpotter.application.optimization.pobb.elimination.classification import (
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

        # Skip recall@k for llm_only-style pipelines — no ranked_items to match against ground_truth.
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
        # Resilient by design — a render glitch must not abort the live readout —
        # but surface it (R-48 fail-loud), never swallow silently.
        logger.warning("round-stats render block failed; lines dropped", exc_info=True)

    return "\n".join(lines)


def render_patience_status(improved: bool, l1_stall_count: int, l1_patience: int) -> str:
    """Green tick on improvement; yellow patience counter on stall (informational only)."""
    if improved:
        return _node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}")
    return _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")


__all__ = [
    "fmt_elapsed",
    "render_patience_status",
    "render_progress_table",
    "render_round_stats",
]
