"""Per-candidate header + summary classification.

Pure: no I/O, no mutation. Two flows:

* :func:`fmt_individual_header` — fired pre-scoring from
  ``LiveDisplay.on_candidate_started`` (the ``ind k/N  <mutation>`` line).
* :class:`IndividualSummary` + :func:`individual_summary_from_dict` —
  fired post-scoring from ``LiveDisplay.on_candidate_scored``; also feeds
  notebook box rendering. Invalid > aborted > eliminated > ok precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from promptpotter.presentation.views.display import (
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    _pp_val,
    fmt_ci,
    fmt_pvalue,
)
from promptpotter.shared.composite import render_composite_fitness_oneliner
from promptpotter.shared.statistics import wilson_ci


def fmt_pp_override(pp: dict | None) -> str:
    """Flatten a nested pipeline_params override to ``node.key: val  …``.

    Returns ``""`` when the override is empty or None. Callers supply the
    leading label (e.g. ``cand 2/5  ``) and any ANSI wrapping.
    """
    if not pp:
        return ""
    parts: list[str] = []
    for node, val in pp.items():
        if isinstance(val, dict):
            for k, v in val.items():
                parts.append(f"{node}.{k}: {_pp_val(v)}")
        else:
            parts.append(f"{node}: {_pp_val(val)}")
    return "  ".join(parts)


def fmt_individual_header(
    idx: int,
    total: int,
    changes_description: str,
    pp_override: dict | None,
) -> str:
    """One-line pre-scoring header: ``ind k/N  <mutation>`` or ``ind k/N  parent re-eval``.

    Fired from ``on_candidate_started`` before the backend calls start. The
    mutation body comes from ``pp_override`` when present (authoritative
    machine-readable diff), else falls back to the human-written
    ``changes_description`` from the L1 variant plan, else the parent tag.
    """
    label = f"ind {idx + 1}/{total}"
    body = fmt_pp_override(pp_override)
    if not body and changes_description:
        body = changes_description.strip()
    body = f"{DIM}parent re-eval{RESET}" if not body else f"{CYAN}{body}{RESET}"
    return f"  {label}  {body}"


@dataclass(frozen=True)
class IndividualSummary:
    """Structured candidate render — displays pick plain vs box wrapping.

    Pieces the two displays can compose independently:

    - ``tag`` — compact status slot (``80.0% [77-82%]`` or ``INVALID``);
      notebook puts it on the top-right of the box, CLI appends it to
      ``cand k/N``.
    - ``body_line`` — the mutations + hits + delta line; the notebook
      renders it as an inner box line, the CLI as an indented second line.
    - ``detail_lines`` — ordered extras (elimination summary, the 1-line
      composite_fitness-with-Δ render, degraded-count tag, or validation-failure
      entries); rendered inline by both displays, with the last entry
      folded onto the bottom info rule by callers that support it.
    """

    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]


def individual_summary_from_dict(
    scores: dict,
    origin_acc: float,
    *,
    origin_composite_fitness: float | None = None,
) -> IndividualSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).

    Per-candidate composite_fitness render is intentionally 1 line —
    ``composite_fitness=0.6042  (Δ+0.103 vs origin 0.5012)`` — so 5 candidates
    don't dump 60 lines of identical formula text into the terminal. The
    formula + per-evaluator breakdown lands once per round in the round
    summary block.

    *origin_composite_fitness* anchors the Δ against the campaign's first-round
    composite_fitness — even at deep rounds the operator sees how far the run
    has come from origin. ``None`` collapses to the no-Δ form.
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
    if scores.get("elimination_stopped") or elim.get("leader_locked"):
        eq = int(elim.get("queries_scored", 0))
        eqt = int(elim.get("total_queries", 0))
        n_priors = int(elim.get("n_priors", 0))
        prior_s = "" if n_priors == 1 else "s"
        if scores.get("elimination_stopped"):
            label = elim.get("triggered_by_prior_label")
            if label is None:
                pi = elim.get("triggered_by_prior_idx", -1)
                label = f"prior #{pi}" if isinstance(pi, int) and pi >= 0 else "prior"
            detail_lines.append(
                f"{YELLOW}✂ eliminated q{eq}/{eqt}{RESET}  "
                f"{fmt_pvalue(float(elim.get('triggered_p', 1.0)))}  "
                f"vs {label} (of {n_priors} prior{prior_s})"
            )
        else:
            detail_lines.append(
                f"{GREEN}✓ leader locked q{eq}/{eqt}{RESET}  "
                f"p_best={float(elim.get('p_best', 0.0)):.1%} (of {n_priors} prior{prior_s})"
            )

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
