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
from promptpotter.domain.results import ArmOutcome
from promptpotter.shared import truncate
from promptpotter.shared.composite import render_composite_fitness_oneliner


def fmt_pipeline_overlay(pp: dict[str, Any] | None) -> str:
    """Render a nested pipeline_params override. A JOIN over the canonical ``flatten_sp_summary``, never a second implementation — the
    hand-rolled twin carried its own float formatter and flattened only one level."""
    return "  ".join(f"{k}: {v}" for k, v in flatten_sp_summary(pp).items())


# A prose fallback is unbounded at its source, and one measured line ran 435 chars — wider than
# the score box it heads. Roughly one terminal row once the label is prefixed.
_HEADER_BODY_MAX = 120


def fmt_individual_header(
    label: str,
    total: int,
    changes_description: str,
    pipeline_overlay: dict[str, Any] | None,
) -> str:
    """``label`` is the candidate's ``C{round}.{n}``, so this header and the score box that closes
    the candidate name it identically — ``ind 2/2`` was a second vocabulary for one individual."""
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


def individual_summary_from_dict(
    scores: dict[str, Any],
    *,
    unit: MeasuredUnit = "sample",
) -> IndividualSummary:
    """Pre-format every display piece of a candidate score report, off the outcome it names.

    Takes no parent: the comparison against it is SERVED (``reference_*``), never differenced
    here. See the note on ``body_line`` below for why a view may not compute one."""
    mutations = fmt_pipeline_overlay(scores.get("pipeline_overlay"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    outcome = scores.get("outcome")
    if outcome == ArmOutcome.INVALID:
        out: list[str] = []
        for vf in scores["validation_failures"]:
            allowed = vf.get("allowed") or []
            allowed_str = ", ".join(allowed[:3]) + (
                f" (+{len(allowed) - 3})" if len(allowed) > 3 else ""
            )
            out.append(
                f"{YELLOW}⚠{RESET} {vf.get('axis', '?')} = {vf.get('value', '?')!r}  "
                f"∉ [{allowed_str}]"
            )
            out.append("  ↳ scored 0 (no backend call); the next l1_generate reads it in l1_wounds")
        return IndividualSummary(
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=tuple(out),
        )

    acc = scores["accuracy"]
    n = scores.get("total", 0)
    # The served accuracy interval, not a Wilson band re-derived here: this row draws
    # the candidate's own numbers, and the CI must bracket one of them.
    ci = fmt_ci(scores.get("mean_fitness_ci_lo"), scores.get("mean_fitness_ci_hi"), spec="{:.1%}")
    tag = f"{fmt_pct(acc)} {ci}"

    if outcome is not None and ArmOutcome(outcome).cut_short:
        scored_q = scores.get("scored_samples", n)
        expected_q = scores.get("expected_samples", n)
        n_str = f"{unit_count(scored_q, unit)} {YELLOW}⚠ {outcome} {scored_q}/{expected_q}{RESET}"
    else:
        n_str = unit_count(n, unit)
    # 📖 is the per-sample tape's cache mark; this is its total for the candidate.
    n_cached = int(scores.get("cached_samples") or 0)
    if n_cached:
        n_str += f" ({n_cached}📖)"
    # THE SERVED LIFT, never `acc - parent_acc` recomputed here: `reference_lift` is the
    # paired difference on the cells the arm and its reference BOTH measured, `None` until round
    # measurement stamps it (`runner/measurement.py`) or below two shared cells. Absent means absent —
    # a cut arm's rate on its own prefix outruns the reference's on a fuller panel.
    lift = scores.get("reference_lift")
    vs_reference = ""
    if isinstance(lift, int | float):
        band = fmt_ci(
            scores.get("reference_lift_ci_lo"),
            scores.get("reference_lift_ci_hi"),
            spec="{:+.1%}",
        )
        vs_reference = f"  vs reference: {_fmt_delta(float(lift))} {band}"
    body_line = f"{mutations_chunk}{n_str}{vs_reference}"

    detail_lines: list[str] = []
    degrad = scores.get("degradation_context") or {}

    # The eliminator words its own stops; the terminal only marks which way it decided.
    if reason := scores.get("elimination_reason"):
        mark = f"{GREEN}✓" if outcome == ArmOutcome.LOCKED_IN else f"{YELLOW}✂"
        detail_lines.append(f"{mark} {reason}{RESET}")
    elif outcome == ArmOutcome.BROKEN and degrad:
        dc = int(degrad.get("degraded_count", 0))
        ts = int(degrad.get("total_scored", 0))
        rate = float(degrad.get("degraded_rate", 0.0))
        fatal = bool(degrad.get("fatal", False))
        reason = degrad.get("dominant_warning", "unknown")
        source = degrad.get("source", "degradation")
        tag = "fatal" if fatal else f"{rate:.0%} degraded"
        detail_lines.append(f"{YELLOW}✂ broken ({source}) q{dc}/{ts}{RESET}  {tag}  ({reason})")

    comp = scores.get("composite_fitness")
    degraded = scores.get("degraded_samples", 0)

    if comp is not None:
        # Same rule, same source: the reference's composite ON THIS ARM'S CELLS, or no Δ at all.
        # The cycle-wide `parent_composite_fitness` was read over the parent's own panel.
        detail_lines.append(
            render_composite_fitness_oneliner(comp, reference=scores.get("reference_composite"))
        )
    if degraded:
        detail_lines.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")

    return IndividualSummary(
        tag=tag,
        body_line=body_line,
        detail_lines=tuple(detail_lines),
    )


__all__ = ["fmt_individual_header", "individual_summary_from_dict"]
