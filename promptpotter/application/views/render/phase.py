from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, assert_never

from promptpotter.application.views.render.primitives import (
    BOLD,
    BOX_WIDTH,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _dbox_block,
    _node_line,
    fmt_coverage,
    fmt_fitness,
    fmt_pct,
    overlap_series,
    render_pipeline_overlay,
)
from promptpotter.domain.connector import MeasuredUnit, unit_count
from promptpotter.domain.origin_panel import ROUND_ADVANCE_INFO, RoundAdvance, StallEffect
from promptpotter.domain.phases import STOP_REASON_INFO, StopOutcome, StopReason
from promptpotter.domain.ruler import PLATEAU_ROUNDS, LevelSeries
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CycleResult, RoundOutcome
    from promptpotter.domain.run_records import RoundClosedRecord


def stop_lines(stop_reason: StopReason, *, detail: str = "") -> list[str]:
    """A stop at every surface that names one: its outcome, its label, what said so, the verb past it."""
    info = STOP_REASON_INFO[stop_reason]
    said = f" — {detail}" if detail else ""
    return [
        f"{info.outcome.value.upper()} · {info.label}{said}",
        *([f"next: {info.next_step}"] if info.next_step else []),
    ]


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def render_progress_table(rounds: Sequence[RoundOutcome], series: LevelSeries) -> str:
    if not rounds:
        return ""

    header = (
        f"{'Round':<7s} {'Accuracy':>9s} {'n':>5s} {'Composite':>10s}"
        f" {'Ability θ':>10s} {'Trend':>9s}"
    )
    lines: list[str] = [_node_line(header)]

    # Trend reads ABILITY, never accuracy: under `per_round_resubset` consecutive accuracies sit different exams.
    prev: float | None = None
    for rr in rounds:
        read = series.reading(rr.round)
        th_str = "---" if read is None else f"{read.reading.theta:+.3f}"
        level = series.level(rr.round)
        trend = "-" if level is None or prev is None else f"{level.theta - prev:+.3f}"
        if level is not None:
            prev = level.theta
        fold = rr.fold
        row = (
            f"  {rr.round!s:<5s} {fmt_pct(None if fold is None else fold.accuracy):>8s} "
            f"{0 if fold is None else fold.total:>5d} "
            f"{fmt_fitness(None if fold is None else fold.composite_fitness):>9s} "
            f"{th_str:>10s} {trend:>9s}"
        )
        lines.append(_node_line(row))

    # Plateau advice is for a run whose rounds are won on θ; a peer's θ series decides nothing.
    last = rounds[-1].election
    if (
        last is not None
        and last.elects_on == "ability"
        and (flat_at := series.plateau()) is not None
    ):
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
    election = round_result.election
    selected = None if election is None else election.selected
    fit = None if selected is None else round_result.fit(selected)
    reading = None if fit is None else fit.vs_reference
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
    fold = round_result.fold
    accuracy = None if fold is None else fold.accuracy
    total = 0 if fold is None else fold.total
    deprecated = 0 if fold is None else fold.deprecated
    if total == 0:
        # The scoreboard's first panel row, never a private argmax that can star an arm nobody elected.
        best = next(
            (
                own
                for row in round_result.ranked()
                if row.reading.arm.key in round_result.panel and (own := row.reading.own)
            ),
            None,
        )
        if best is not None:
            accuracy = None if best.accuracy is None else best.accuracy.value
            total, deprecated = best.n, 0
    suffix = f"  ({deprecated} deprecated)" if deprecated else ""
    lines.append(
        _node_line(
            f"accuracy: {fmt_pct(accuracy)} of {unit_count(total, unit)}{suffix}  |  evaluated: "
            f"{round_result.candidates_scored} candidates"
        )
    )

    h = round_result.health
    if h.grade == "critical":
        lines.append(_node_line(f"{BOLD}{RED}⛔ CRITICAL — {h.suggested_action}{RESET}"))
    elif h.grade == "degraded":
        lines.append(_node_line(f"{YELLOW}⚠ DEGRADED — {h.suggested_action}{RESET}"))

    # No per-node tally: `terminal_node` is the deepest node each sample REACHED, constant on a healthy round.
    if fold is not None and fold.degraded and fold.walked:
        lines.append(_node_line(f"Degradation: {fold.degraded / fold.walked:.0%}"))
    if fold is not None and (recall := fold.recall_at):
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


def render_completion(
    result: CycleResult,
    *,
    pipeline_schema: PipelineSchema | None = None,
    dataset_name: str | None = None,
    campaign_dir: Path | None = None,
) -> str:
    # The outcome, never the member: more than one stop reason is PAUSED.
    info = STOP_REASON_INFO[result.stop_reason]
    match info.outcome:
        case StopOutcome.PAUSED:
            title = f"{YELLOW}{BOLD}PAUSED{RESET} — resumable"
        case StopOutcome.FAILED:
            title = f"{RED}{BOLD}{info.label.upper()}{RESET} — no result"
        case StopOutcome.HALTED:
            title = f"{YELLOW}{BOLD}HALTED{RESET} — {info.label}; best so far kept"
        case StopOutcome.SUCCESS:
            title = f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
        case _:
            assert_never(info.outcome)

    headline = f"Rounds       {result.n_rounds_after_origin:<15d}"
    if result.result_accuracy is not None:
        headline += f"Selected     {result.result_accuracy:.1%} (round {result.result_round})"
    # Repeated under a terminal's readout: a notebook cell has no readout, only this box.
    fields: list[str] = [
        *stop_lines(result.stop_reason),
        f"Bench        {result.bench.line}",
        headline,
    ]
    if result.error is not None:
        fields.append(f"Error        {result.error.kind}: {result.error.message}")
    if result.spend is not None:
        fields.append(f"Spend        {result.spend.billed_beside_incurred()}")
    if dataset_name:
        fields.append(f"Dataset      {dataset_name}")
    if campaign_dir is not None:
        fields.append(f"Campaign     {campaign_dir.name}")
    if result.cycle_id:
        fields.append(f"Cycle ID     {result.cycle_id}")
    if trace_url := langfuse_trace_url(result.langfuse_trace_id):
        fields.append(f"Langfuse     {trace_url}")

    wrapped = [
        line
        for field in fields
        for line in textwrap.wrap(field, width=BOX_WIDTH - 4, subsequent_indent=" " * 13)
    ]
    out = ["", _dbox_block(title, *wrapped)]
    if overlay_block := render_pipeline_overlay(result.result_pipeline_params, pipeline_schema):
        out.append("")
        out.append(overlay_block)
    if campaign_dir is not None:
        out += [
            "",
            f"Directory: {campaign_dir}",
            "  campaign.json          — manifest",
            "  log.md                 — campaign digest",
            f"  cycles/{result.cycle_id or '?'}/  — session telemetry (dashboard.json) + rounds"
            " + readout.log",
        ]
    return "\n".join(out)
