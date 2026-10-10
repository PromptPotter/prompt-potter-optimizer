from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.primitives import (
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    fmt_ci,
)
from promptpotter.domain.candidate_diff import flatten_sp_summary
from promptpotter.domain.connector import MeasuredUnit, unit_count
from promptpotter.domain.results import ArmOutcome, ScoredCandidate
from promptpotter.shared import truncate
from promptpotter.shared.composite import render_composite_fitness_oneliner


def fmt_pipeline_overlay(pp: dict[str, Any] | None) -> str:
    """A JOIN over the canonical ``flatten_sp_summary``, never a second implementation."""
    return "  ".join(f"{k}: {v}" for k, v in flatten_sp_summary(pp).items())


_HEADER_BODY_MAX = 120


def fmt_individual_header(
    label: str,
    total: int,
    changes_description: str,
    pipeline_overlay: dict[str, Any] | None,
) -> str:
    body = fmt_pipeline_overlay(pipeline_overlay)
    if not body and changes_description:
        body = truncate(changes_description.strip(), _HEADER_BODY_MAX)
    body = f"{DIM}no change described{RESET}" if not body else f"{CYAN}{body}{RESET}"
    return f"  {label}/{total}  {body}"


@dataclass(frozen=True)
class IndividualSummary:
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]
    lift: float | None


def individual_summary(report: ScoredCandidate, *, unit: MeasuredUnit) -> IndividualSummary:
    """Takes no parent: the comparison against it is SERVED (``vs_reference``), never differenced here."""
    mutations = fmt_pipeline_overlay(report.pipeline_overlay)
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    outcome = report.outcome
    if outcome == ArmOutcome.INVALID:
        out: list[str] = []
        for vf in report.validation_failures:
            allowed_str = ", ".join(vf.allowed[:3]) + (
                f" (+{len(vf.allowed) - 3})" if len(vf.allowed) > 3 else ""
            )
            out.append(f"{YELLOW}⚠{RESET} {vf.axis} = {vf.value!r}  ∉ [{allowed_str}]")
            out.append("  ↳ scored 0 (no backend call); the next l1_generate reads it in l1_wounds")
        return IndividualSummary(
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=tuple(out),
            lift=None,
        )

    n = report.total
    ci = fmt_ci(report.mean_fitness_ci_lo, report.mean_fitness_ci_hi, spec="{:.1%}")
    tag = f"{fmt_pct(report.accuracy)} {ci}"

    if outcome.cut_short:
        scored_q, expected_q = report.scored_samples, report.expected_samples
        n_str = (
            f"{unit_count(scored_q, unit)} {YELLOW}⚠ {outcome.value} {scored_q}/{expected_q}{RESET}"
        )
    else:
        n_str = unit_count(n, unit)
    if report.cached_samples:
        n_str += f" ({report.cached_samples}📖)"
    # No differenced fallback: a cut arm's rate on its own prefix outruns the reference's on a fuller panel.
    reading = report.vs_reference
    lift = reading.headline.estimate if reading and reading.headline else None
    vs_reference = ""
    if lift is not None:
        band = fmt_ci(lift.ci_lo, lift.ci_hi, spec="{:+.1%}")
        vs_reference = f"  vs reference: {_fmt_delta(lift.value)} {band}"
    body_line = f"{mutations_chunk}{n_str}{vs_reference}"

    detail_lines: list[str] = []
    degrad = report.degradation_context

    if reason := report.elimination_reason:
        mark = f"{GREEN}✓" if outcome == ArmOutcome.LOCKED_IN else f"{YELLOW}✂"
        detail_lines.append(f"{mark} {reason}{RESET}")
    elif outcome == ArmOutcome.BROKEN and degrad:
        tag = "fatal" if degrad["fatal"] else f"{degrad['degraded_rate']:.0%} degraded"
        detail_lines.append(
            f"{YELLOW}✂ broken ({degrad['source']}) "
            f"q{degrad['degraded_count']}/{degrad['total_scored']}{RESET}  "
            f"{tag}  ({degrad['dominant_warning']})"
        )

    if (comp := report.composite_fitness) is not None:
        # The reference's composite ON THIS ARM'S CELLS; the cycle-wide one was read over the parent's own panel.
        detail_lines.append(
            render_composite_fitness_oneliner(
                comp, reference=reading.reference_level("objective") if reading else None
            )
        )

    return IndividualSummary(
        tag=tag,
        body_line=body_line,
        detail_lines=tuple(detail_lines),
        lift=None if lift is None else lift.value,
    )


__all__ = ["fmt_individual_header", "individual_summary"]
