"""Per-candidate header + summary classification. Pure: no I/O, no mutation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.opt_search_point import flatten_sp_summary
from promptpotter.presentation.views.display import (
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    fmt_ci,
)
from promptpotter.shared.composite import render_composite_fitness_oneliner
from promptpotter.shared.statistics import wilson_ci


def fmt_pp_override(pp: dict[str, Any] | None) -> str:
    """Render a nested pipeline_params override as ``node.key: val  …``; ``""`` when empty.

    One flattener, one float format: this is a JOIN over the canonical
    ``flatten_sp_summary`` (`domain/opt_search_point.py`), not a second implementation of
    it. The hand-rolled version this replaced also carried its own float formatter
    (``_pp_val``), byte-identical to the domain one, and flattened only one level — so a
    nested param printed as a dict repr here and as ``node.param.leaf`` on every other
    surface. Routing through the domain helper also puts the "reserved key or non-dict"
    question where ``node_config_items`` already owns it.
    """
    return "  ".join(f"{k}: {v}" for k, v in flatten_sp_summary(pp).items())


def fmt_individual_header(
    idx: int,
    total: int,
    changes_description: str,
    pp_override: dict[str, Any] | None,
) -> str:
    """Pre-scoring header: ``ind k/N  <mutation>`` or ``ind k/N  parent re-eval``."""
    label = f"ind {idx + 1}/{total}"
    body = fmt_pp_override(pp_override)
    if not body and changes_description:
        body = changes_description.strip()
    body = f"{DIM}parent re-eval{RESET}" if not body else f"{CYAN}{body}{RESET}"
    return f"  {label}  {body}"


@dataclass(frozen=True)
class IndividualSummary:
    """Structured candidate render — ``tag`` + ``body_line`` + ordered ``detail_lines``."""

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
    """Classify a candidate score report and pre-format all display pieces.

    Precedence: invalid > aborted > eliminated > ok.
    """
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
    hits = scores.get("hits", 0)
    n = scores.get("total", 0)
    ci_lo, ci_hi = wilson_ci(hits, n)
    delta = acc - origin_acc
    tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"

    aborted = bool(scores.get("escalation_aborted"))
    if aborted:
        scored_q = scores.get("scored_samples", n)
        expected_q = scores.get("expected_samples", n)
        hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
    else:
        hit_str = f"{hits}/{n} hits"
    body_line = f"{mutations_chunk}{hit_str}  vs origin: {_fmt_delta(delta)}"

    detail_lines: list[str] = []
    elim = scores.get("elimination_context") or {}
    degrad = scores.get("degradation_context") or {}

    # Three disjoint exit branches — operator can tell if cut by PoBB or DegradationCheck.
    if elim.get("leader_locked"):
        eq = int(elim.get("queries_scored", 0))
        eqt = int(elim.get("total_queries", 0))
        n_priors = int(elim.get("n_priors", 0))
        prior_s = "" if n_priors == 1 else "s"
        detail_lines.append(
            f"{GREEN}✓ leader locked q{eq}/{eqt}{RESET}  "
            f"p_best={float(elim.get('p_best', 0.0)):.1%} (of {n_priors} prior{prior_s})"
        )
    elif scores.get("elimination_stopped") and elim:
        eq = int(elim.get("queries_scored", 0))
        eqt = int(elim.get("total_queries", 0))
        n_priors = int(elim.get("n_priors", 0))
        prior_s = "" if n_priors == 1 else "s"
        leader_label = elim.get("leader_label") or (elim.get("leader_id", "?") or "?")[:8]
        p_best_pct = float(elim.get("p_best", 0.0))
        eps_pct = float(elim.get("epsilon", POBB_DEFAULT_EPSILON))
        detail_lines.append(
            f"{YELLOW}✂ eliminated q{eq}/{eqt}{RESET}  "
            f"p_best={p_best_pct:.1%} < eps={eps_pct:.0%}  "
            f"vs {leader_label} (of {n_priors} prior{prior_s})"
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


__all__ = [
    "IndividualSummary",
    "fmt_individual_header",
    "fmt_pp_override",
    "individual_summary_from_dict",
]
