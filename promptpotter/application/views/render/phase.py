from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.primitives import (
    BOLD,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _node_line,
    fmt_coverage,
    fmt_fitness,
    overlap_series,
)
from promptpotter.domain.connector import MeasuredUnit, unit_count
from promptpotter.domain.results import (
    ROUND_ADVANCE_INFO,
    ArmOutcome,
    RoundAdvance,
    StallEffect,
    scoreboard_rank_key,
)
from promptpotter.domain.ruler import PLATEAU_ROUNDS, series_levels, theta_plateau

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundOutcome
    from promptpotter.domain.run_records import RoundClosedRecord


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def render_progress_table(rounds: Sequence[RoundOutcome]) -> str:
    if not rounds:
        return ""

    header = (
        f"{'Round':<7s} {'Accuracy':>9s} {'n':>5s} {'Composite':>10s}"
        f" {'Ability θ':>10s} {'Trend':>9s}"
    )
    lines: list[str] = [_node_line(header)]

    # Trend reads ABILITY, never accuracy: under `per_round_resubset` consecutive accuracies sit different exams.
    abilities = [rr.ability for rr in rounds]
    prev: float | None = None
    for rr, level in zip(rounds, series_levels(abilities), strict=True):
        th_str = "---" if rr.ability is None else f"{rr.ability.theta:+.3f}"
        trend = "-" if level is None or prev is None else f"{level - prev:+.3f}"
        if level is not None:
            prev = level
        row = (
            f"  {rr.round!s:<5s} {fmt_pct(rr.accuracy):>8s} {rr.total:>5d} "
            f"{fmt_fitness(rr.composite_fitness):>9s} {th_str:>10s} {trend:>9s}"
        )
        lines.append(_node_line(row))

    # Plateau advice is for a run whose rounds are won on θ; a peer's θ series decides nothing.
    if rounds[-1].elects_on == "ability" and (flat_at := theta_plateau(abilities)) is not None:
        lines.append(
            _node_line(
                f"{YELLOW}-- Plateau: ability flat at {flat_at:+.3f} "
                f"for {PLATEAU_ROUNDS} rounds{RESET}"
            )
        )

    lines.append(_node_line(""))
    return "\n".join(lines)


def round_verdict_basis(round_result: RoundOutcome) -> list[str]:
    lines: list[str] = []
    # The verdict line's point estimate reads the same on a round that resolved nothing; the interval separates them.
    selected = next(iter(round_result.selected_scores), None)
    reading = selected.vs_reference if selected else None
    if reading is not None and reading.headline is not None and reading.coverage is not None:
        lift = reading.headline.estimate
        verdict = (
            f"{YELLOW}spans 0 — not separated from its reference{RESET}"
            if lift.side == "spans"
            else "clears 0"
        )
        lines.append(
            f"lift vs reference: {lift.value:+.3f} [{lift.ci_lo:+.3f}, {lift.ci_hi:+.3f}]"
            f" on {fmt_coverage(reading.coverage)}  |  {verdict}"
        )

    # The ONLY line two rounds can be differenced on: every other number is read on the subset this round bought.
    if series := overlap_series(round_result.overlap):
        lines.append(f"overlap ({series})")
    return lines


def render_round_stats(
    round_result: RoundClosedRecord,
    pipeline_schema: PipelineSchema | None,
    unit: MeasuredUnit = "sample",
) -> str:
    lines: list[str] = []
    accuracy = round_result.accuracy
    total = round_result.total
    deprecated = round_result.deprecated
    if total == 0 and round_result.candidate_scores:
        # The scoreboard's first row, never a private accuracy-argmax that can star an arm nobody elected.
        best = max(
            round_result.candidate_scores,
            key=lambda s: scoreboard_rank_key(
                s.composite_fitness,
                s.accuracy,
                s.theta,
                is_leading=s.label == round_result.leading_label,
                is_partial=s.outcome is ArmOutcome.SKIPPED,
            ),
        )
        accuracy = best.accuracy
        total = best.total
        deprecated = 0
    suffix = f"  ({deprecated} deprecated)" if deprecated else ""
    lines.append(
        _node_line(
            f"accuracy: {fmt_pct(accuracy)} of {unit_count(total, unit)}{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    h = round_result.health
    if h is not None and h.grade == "critical":
        lines.append(_node_line(f"{BOLD}{RED}⛔ CRITICAL — {h.suggested_action}{RESET}"))
    elif h is not None and h.grade == "degraded":
        lines.append(_node_line(f"{YELLOW}⚠ DEGRADED — {h.suggested_action}{RESET}"))

    # No per-node tally: `terminal_node` is the deepest node each sample REACHED, constant on a healthy round.
    if round_result.degraded_samples and (n_results := len(round_result.cells.head)):
        lines.append(_node_line(f"Degradation: {round_result.degraded_samples / n_results:.0%}"))
    # Absent on an llm_only-style pipeline, which ranks nothing.
    if recall := round_result.recall_at:
        lines.append(
            _node_line("Recall: " + " ".join(f"top-{k}={share:.0%}" for k, share in recall.items()))
        )
    return "\n".join(lines)


def render_patience_status(advance: RoundAdvance, stall: int, patience: int | None) -> str:
    info = ROUND_ADVANCE_INFO[advance]
    if info.stall is StallEffect.RESETS:
        return _node_line(f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}")
    rounds = "round" if stall == 1 else "rounds"
    bound = "" if patience is None else f" (converges at {patience})"
    return _node_line(f"{YELLOW}⚠ {info.label} — {stall} {rounds} without an advance{bound}{RESET}")


__all__ = [
    "fmt_elapsed",
    "render_patience_status",
    "render_progress_table",
    "render_round_stats",
    "round_verdict_basis",
]
