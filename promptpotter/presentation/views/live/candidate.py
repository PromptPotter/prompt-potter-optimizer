from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.candidate_diff import flatten_sp_summary
from promptpotter.domain.results import EliminationGate
from promptpotter.presentation.views.display import (
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    fmt_ci,
)
from promptpotter.shared import truncate
from promptpotter.shared.composite import render_composite_fitness_oneliner


def fmt_pp_override(pp: dict[str, Any] | None) -> str:
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
    pp_override: dict[str, Any] | None,
) -> str:
    """``label`` is the candidate's ``C{round}.{n}``, so this header and the score box that closes
    the candidate name it identically — ``ind 2/2`` was a second vocabulary for one individual."""
    body = fmt_pp_override(pp_override)
    if not body and changes_description:
        body = truncate(changes_description.strip(), _HEADER_BODY_MAX)
    body = f"{DIM}parent re-eval{RESET}" if not body else f"{CYAN}{body}{RESET}"
    return f"  {label}/{total}  {body}"


@dataclass(frozen=True)
class IndividualSummary:
    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]


def individual_summary_from_dict(
    scores: dict[str, Any],
    origin_acc: float,
    *,
    origin_composite_fitness: float | None = None,
) -> IndividualSummary:
    """Classify a candidate score report and pre-format every display piece. Precedence: invalid > aborted > eliminated > ok."""
    mutations = fmt_pp_override(scores.get("pipeline_params_override"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if scores.get("invalid"):
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
            out.append("  ↳ scored 0 (no backend call); L2 brief will name this value")
        return IndividualSummary(
            status="invalid",
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=tuple(out),
        )

    acc = scores["accuracy"]
    n = scores.get("total", 0)
    # The served composite interval, not a Wilson band re-derived here: this row draws
    # the candidate's own numbers, and the CI must bracket one of them.
    delta = acc - origin_acc
    ci = fmt_ci(scores.get("mean_fitness_ci_lo"), scores.get("mean_fitness_ci_hi"), spec="{:.1%}")
    tag = f"{acc:.1%} {ci}"

    aborted = bool(scores.get("escalation_aborted"))
    if aborted:
        scored_q = scores.get("scored_samples", n)
        expected_q = scores.get("expected_samples", n)
        n_str = f"{scored_q} samples {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
    else:
        n_str = f"{n} samples"
    # 📖 is the per-sample tape's cache mark; this is its total for the candidate.
    n_cached = int(scores.get("cached_samples") or 0)
    if n_cached:
        n_str += f" ({n_cached}📖)"
    body_line = f"{mutations_chunk}{n_str}  vs origin: {_fmt_delta(delta)}"

    detail_lines: list[str] = []
    elim = scores.get("elimination_context") or {}
    degrad = scores.get("degradation_context") or {}

    # One dispatch on the gate the PRODUCER named. Never on which keys survived: only ε and
    # lock-in computed a posterior, so quoting one under any other gate invents it.
    gate = elim.get("gate")
    q = f"q{int(elim.get('queries_scored', 0))}/{int(elim.get('total_queries', 0))}"
    n_priors = int(elim.get("n_priors", 0))
    priors = f"(of {n_priors} prior{'' if n_priors == 1 else 's'})"
    p_best = float(elim.get("p_best", 0.0))
    if gate == EliminationGate.LOCK_IN:
        detail_lines.append(f"{GREEN}✓ leader locked {q}{RESET}  p_best={p_best:.1%} {priors}")
    elif gate == EliminationGate.COLLAPSED:
        detail_lines.append(
            f"{YELLOW}✂ answer collapsed {q}{RESET}  "
            "one label for every sample — no measurement of ability to score"
        )
    elif gate == EliminationGate.EPSILON:
        leader = elim.get("leader_label") or (elim.get("leader_id", "?") or "?")[:8]
        eps = float(elim.get("epsilon", POBB_DEFAULT_EPSILON))
        detail_lines.append(
            f"{YELLOW}✂ eliminated {q}{RESET}  p_best={p_best:.1%} < eps={eps:.0%}  "
            f"vs {leader} {priors}"
        )
    elif scores.get("elimination_stopped") and degrad:
        dc = int(degrad.get("degraded_count", 0))
        ts = int(degrad.get("total_scored", 0))
        rate = float(degrad.get("degraded_rate", 0.0))
        fatal = bool(degrad.get("fatal", False))
        reason = degrad.get("dominant_warning", "unknown")
        source = degrad.get("source", "degradation")
        tag = "fatal" if fatal else f"{rate:.0%} degraded"
        detail_lines.append(f"{YELLOW}✂ {source} q{dc}/{ts}{RESET}  {tag}  ({reason})")

    comp = scores.get("composite_fitness")
    degraded = scores.get("degraded_samples", 0)

    if comp is not None:
        detail_lines.append(
            render_composite_fitness_oneliner(comp, origin=origin_composite_fitness)
        )
    if degraded:
        detail_lines.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")

    status: Literal["ok", "aborted", "eliminated"]
    if aborted:
        status = "aborted"
    elif scores.get("elimination_stopped"):
        status = "eliminated"
    else:
        status = "ok"
    return IndividualSummary(
        status=status,
        tag=tag,
        body_line=body_line,
        detail_lines=tuple(detail_lines),
    )


__all__ = ["fmt_individual_header", "individual_summary_from_dict"]
