"""Round-summary renderers (``LiveDisplay.on_round_complete``). Pure: no campaign
I/O, no mutation (errors log, never abort the live readout)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.domain.rendering import display_fitness, display_rank_key
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
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def render_progress_table(rounds: list[dict[str, Any]], window: int = 8) -> str:
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
        rl = "G" if rd.get("round") == "grid" else str(rd.get("round", "?"))
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
    lines: list[str] = []
    accuracy = round_result.accuracy
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
                key=lambda s: display_rank_key(s.composite_fitness, s.accuracy),
            ),
        )
        accuracy = best.accuracy
        total = best.total
        deprecated = 0
    suffix = f"  ({deprecated} deprecated)" if deprecated else ""
    lines.append(
        _node_line(
            # Was `hits: 12/20`. The integer pair is the small readability cost of
            # dropping a scalar that meant nothing on a graded scorer; the percentage
            # is the same number the round reports everywhere else.
            f"accuracy: {accuracy:.1%} of {total} samples{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    # The winner's blocked lift over the matched origin WITH its interval — the served
    # `RoundResult` pair, not a recomputation. The header above prints a point estimate and every
    # other line reads the same on a round that resolved nothing as on one that resolved
    # something; this is the line that separates them. Silent when the round crowned nobody or the
    # panel held under two shared cells, where the absence is the honest answer.
    lo, hi = round_result.matched_parent_lift_ci_lo, round_result.matched_parent_lift_ci_hi
    if round_result.matched_parent_lift is not None and lo is not None and hi is not None:
        spans_zero = lo <= 0.0 <= hi
        verdict = (
            f"{YELLOW}spans 0 — not separable from the parent{RESET}" if spans_zero else "clears 0"
        )
        lines.append(
            _node_line(
                f"lift vs matched parent: {round_result.matched_parent_lift:+.3f} "
                f"[{lo:+.3f}, {hi:+.3f}]  |  {verdict}"
            )
        )

    # Degradation verdict — the served ``round_result.health`` (R-36: rendered,
    # not recomputed). Loudness scales with grade; ``healthy`` stays silent.
    h = round_result.health
    if h is not None and h.grade == "critical":
        lines.append(_node_line(f"{BOLD}{RED}⛔ CRITICAL — {h.suggested_action}{RESET}"))
    elif h is not None and h.grade == "degraded":
        # One arm, not two: ``degraded`` now requires ``degraded_rate >= FLAG``, which
        # cannot hold with zero degraded samples. The old "under-probed (CI …)" arm
        # belonged to the deleted untested-CI clause — a wide interval is a fact about
        # n, and it is reported as one beside the candidate, not as a health verdict.
        why = f"{h.structural_count + h.transient_count}/{h.samples} samples degraded"
        lines.append(_node_line(f"{YELLOW}⚠ DEGRADED — {why}; numbers soft{RESET}"))

    if not round_result.results:
        return "\n".join(lines)

    try:
        from promptpotter.application.optimization.pobb.classification import (
            get_ranked_items,
            ranked_item_keys_from_schema,
        )
        from promptpotter.application.scoring.diagnostics import find_rank

        ranked_item_keys = ranked_item_keys_from_schema(pipeline_schema)
        results = round_result.results
        n_results = len(results)
        degraded = 0
        for r in results:
            pd = r.get("pipeline_data") or {}
            if (pd.get("diagnostics") or {}).get("warnings"):
                degraded += 1

        # No "Pipeline: <node>:<n>" tally here. It counted `terminal_node`, the deepest node each
        # sample REACHED, so a healthy round printed every sample under the last node — a constant
        # dressed as a finding. Degradation below is the line that varies.
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
    if improved:
        return _node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}")
    return _node_line(f"{YELLOW}⚠ No improvement ({l1_stall_count}/{l1_patience} patience){RESET}")


__all__ = [
    "fmt_elapsed",
    "render_patience_status",
    "render_progress_table",
    "render_round_stats",
]
