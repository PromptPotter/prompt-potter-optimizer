"""Round-summary renderers (``LiveDisplay.on_round_complete``). Pure: no campaign
I/O, no mutation (errors log, never abort the live readout)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.connectors.protocol import MeasuredUnit, unit_count
from promptpotter.domain.rendering import display_fitness, display_rank_key
from promptpotter.domain.results import is_round_winner, overlap_series
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


# A θ band narrow enough that three readings inside it cannot be three different results: one
# round's own θ SE on a realistic panel is several times this, so the banner fires on a series
# that is genuinely flat and stays quiet on one that is merely noisy. It advises stopping, and a
# false stop costs more than a missed one.
_PLATEAU_THETA_BAND = 0.05


def render_progress_table(rounds: list[dict[str, Any]]) -> str:
    if not rounds:
        return ""

    header = (
        f"{'Round':<7s} {'Accuracy':>9s} {'n':>5s} {'Composite':>10s} "
        f"{'Ability θ':>10s} {'Trend':>9s}"
    )
    lines: list[str] = [_node_line(header)]

    # Trend and the plateau banner read ABILITY, never accuracy. Under `per_round_resubset` each
    # round scores a fresh hard-first draw, so consecutive accuracies belong to different exams
    # and their difference is mostly which cells were drawn. The frontier θ is the cycle's
    # one subset-invariant series. Accuracy stays on the table — it is a true fact about the
    # round — but it is badged with the `n` it was measured over rather than differenced, and
    # nothing here may average it across rounds: that mean names a number no configuration scored.
    thetas: list[float] = []
    prev: float | None = None
    for rd in rounds:
        acc = rd.get("accuracy") or 0
        theta = rd.get("theta")
        theta = float(theta) if isinstance(theta, int | float) else None
        if theta is None:
            th_str, trend = "---", "-"
        else:
            thetas.append(theta)
            th_str = f"{theta:+.3f}"
            trend = "-" if prev is None else f"{theta - prev:+.3f}"
            prev = theta
        rl = "G" if rd.get("round") == "grid" else str(rd.get("round", "?"))
        comp = display_fitness(rd.get("composite_fitness"), acc)
        # `28+33` when the origin panel is carried: the band this round chose to learn from,
        # then the fixed yardstick every round shares. One number would hide that two rounds
        # with the same `n` can have bought entirely different cells.
        n = int(rd.get("total") or 0)
        row = f"  {rl:<5s} {acc:>8.1%} {n:>5d} {comp:>9.4f} {th_str:>10s} {trend:>9s}"
        lines.append(_node_line(row))

    if len(thetas) >= 3:
        recent = thetas[-3:]
        mean = sum(recent) / 3
        if all(abs(t - mean) < _PLATEAU_THETA_BAND for t in recent):
            lines.append(
                _node_line(f"{YELLOW}-- Plateau: ability flat at {mean:+.3f} for 3 rounds{RESET}")
            )

    lines.append(_node_line(""))
    return "\n".join(lines)


def render_round_stats(
    round_result: RoundResult,
    pipeline_schema: PipelineSchema | None,
    unit: MeasuredUnit = "sample",
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
                key=lambda s: display_rank_key(
                    s.composite_fitness,
                    s.accuracy,
                    s.theta,
                    is_winner=is_round_winner(s.candidate_id, round_result.winner_id),
                    is_partial=bool(s.partial_reason),
                ),
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
            f"accuracy: {accuracy:.1%} of {unit_count(total, unit)}{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    # The winner's blocked lift over the matched parent WITH its interval — the served
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

    # The 1-to-1 series: the adopted line read on the cells all of it has answered. It is the
    # ONLY line here two rounds can be differenced on — every other number above is read on the
    # subset this round happened to buy. Silent until the line has a second member.
    if series := overlap_series(round_result.overlap):
        lines.append(_node_line(f"overlap ({series})"))

    # Degradation verdict — the served ``round_result.health`` (R-36: rendered,
    # not recomputed). Loudness scales with grade; ``healthy`` stays silent.
    h = round_result.health
    if h is not None and h.grade == "critical":
        lines.append(_node_line(f"{BOLD}{RED}⛔ CRITICAL — {h.suggested_action}{RESET}"))
    elif h is not None and h.grade == "degraded":
        lines.append(_node_line(f"{YELLOW}⚠ DEGRADED — {h.suggested_action}{RESET}"))

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
