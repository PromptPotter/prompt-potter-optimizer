from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from promptpotter.application.views.render.primitives import (
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    fmt_ci,
    fmt_pct,
)
from promptpotter.domain.candidate_diff import flatten_sp_summary
from promptpotter.domain.connector import MeasuredUnit, unit_count
from promptpotter.domain.results import (
    Broken,
    Concluded,
    Cut,
    ScoreSummary,
    arm_elimination,
    arm_end,
)
from promptpotter.shared.answer_text import truncate
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


def individual_summary(
    report: Concluded, fold: ScoreSummary | None, *, unit: MeasuredUnit
) -> IndividualSummary:
    """Takes no parent: an arm concludes before the election pairs it against one."""
    mutations = fmt_pipeline_overlay(report.overlay)
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if arm_end(report) == "invalid":
        out: list[str] = []
        for vf in report.validation_failures:
            allowed_str = ", ".join(vf.allowed[:3]) + (
                f" (+{len(vf.allowed) - 3})" if len(vf.allowed) > 3 else ""
            )
            out.append(f"{YELLOW}⚠{RESET} {vf.axis} = {vf.value!r}  ∉ [{allowed_str}]")
            out.append("  ↳ never measured; the next l1_generate reads it in l1_wounds")
        return IndividualSummary(
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=tuple(out),
        )
    if fold is None:
        return IndividualSummary(
            tag=f"{YELLOW}{arm_end(report)}{RESET}",
            body_line=f"{mutations_chunk}{unit_count(0, unit)}",
            detail_lines=(),
        )

    ci = fmt_ci(fold.mean_fitness_ci_lo, fold.mean_fitness_ci_hi, spec="{:.1%}")
    tag = f"{fmt_pct(fold.accuracy)} {ci}"

    cut = report.reason if isinstance(report, Cut) else None
    if cut is not None:
        walked, expected_q = fold.walked, report.expected_samples
        n_str = f"{unit_count(walked, unit)} {YELLOW}⚠ {cut.kind} {walked}/{expected_q}{RESET}"
    else:
        n_str = unit_count(fold.total, unit)
    if fold.cached:
        n_str += f" ({fold.cached}📖)"
    body_line = f"{mutations_chunk}{n_str}"

    detail_lines: list[str] = []
    if (elimination := arm_elimination(report)) is not None:
        mark = f"{YELLOW}✂" if cut is not None else f"{GREEN}✓"
        detail_lines.append(f"{mark} {elimination.reason}{RESET}")
    elif isinstance(cut, Broken):
        degrad = cut.degradation
        tag = "fatal" if degrad["fatal"] else f"{degrad['degraded_rate']:.0%} degraded"
        detail_lines.append(
            f"{YELLOW}✂ broken ({degrad['source']}) "
            f"q{degrad['degraded_count']}/{degrad['total_scored']}{RESET}  "
            f"{tag}  ({degrad['dominant_warning']})"
        )

    if (comp := fold.composite_fitness) is not None:
        detail_lines.append(render_composite_fitness_oneliner(comp, reference=None))

    return IndividualSummary(
        tag=tag,
        body_line=body_line,
        detail_lines=tuple(detail_lines),
    )


__all__ = ["fmt_individual_header", "individual_summary"]
