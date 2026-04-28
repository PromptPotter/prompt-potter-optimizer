"""Per-candidate (individual) renderers — pre-scoring header + post-scoring summary.

``IndividualSummary`` is the structured output ``LiveDisplay`` and the
notebook's box renderer both compose into their respective layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from promptpotter.presentation.views.display_primitives import (
    CYAN,
    DIM,
    RESET,
    YELLOW,
    _fmt_delta,
    _pp_val,
)
from promptpotter.presentation.views.formatting import fmt_ci, fmt_pvalue
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
    """One-line pre-scoring header: ``cand k/N  <mutation>`` or ``cand k/N  parent re-eval``.

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


def format_elimination_summary(ctx: dict, prior_label: str | None = None) -> str:
    """One-line eliminated-candidate summary.

    Example: ``✂ eliminated q7/15  p=0.023 *  vs C3 (of 3 priors)``.
    ``prior_label`` is the resolved label of the prior that triggered the
    Wilcoxon rejection; falls back to the integer index in ctx when absent.
    """
    q = int(ctx.get("queries_scored", 0))
    qt = int(ctx.get("total_queries", 0))
    p = float(ctx.get("triggered_p", 1.0))
    n_priors = int(ctx.get("n_priors", 0))

    if prior_label is None:
        idx = ctx.get("triggered_by_prior_idx", ctx.get("triggered_by_prior", -1))
        prior_label = f"prior #{idx}" if isinstance(idx, int) and idx >= 0 else "prior"

    return (
        f"{YELLOW}✂ eliminated q{q}/{qt}{RESET}  "
        f"{fmt_pvalue(p)}  vs {prior_label} (of {n_priors} prior{'s' if n_priors != 1 else ''})"
    )


@dataclass(frozen=True)
class IndividualSummary:
    """Structured candidate render — displays pick plain vs box wrapping.

    Pieces the two displays can compose independently:

    - ``tag`` — compact status slot (``80.0% [77-82%]`` or ``INVALID``);
      notebook puts it on the top-right of the box, CLI appends it to
      ``cand k/N``.
    - ``body_line`` — the mutations + hits + delta line; the notebook
      renders it as an inner box line, the CLI as an indented second line.
    - ``detail_lines`` — ordered extras (elimination summary, composite,
      degraded count, or validation-failure entries); rendered inline by
      both displays, with the notebook folding the last entry into the
      bottom info rule when possible.
    """

    status: Literal["ok", "invalid", "aborted", "eliminated"]
    tag: str
    body_line: str
    detail_lines: tuple[str, ...]


def _fmt_validation_failure_lines(failures: list[dict]) -> tuple[str, ...]:
    """Render one '⚠ axis = value ∉ allowed' + '↳ scored 0' pair per failure."""
    out: list[str] = []
    for vf in failures:
        axis = vf.get("axis", "?")
        value = vf.get("value", "?")
        allowed = vf.get("allowed") or []
        allowed_str = ", ".join(allowed[:3]) + (
            f" (+{len(allowed) - 3})" if len(allowed) > 3 else ""
        )
        out.append(f"{YELLOW}⚠{RESET} {axis} = {value!r}  ∉ [{allowed_str}]")
        out.append("  ↳ scored 0 (no backend call); L2 directive will name this value")
    return tuple(out)


def build_individual_summary(scores: dict, baseline_acc: float) -> IndividualSummary:
    """Classify a candidate score report and pre-format all display pieces.

    Single source of truth for what the CLI and notebook show per candidate.
    Invalid > aborted > eliminated > ok precedence matches the report's
    exclusive flag semantics (invalid never runs the backend; aborted and
    eliminated are mutually exclusive by construction in
    ``_handle_scored_candidate``).
    """
    mutations = fmt_pp_override(scores.get("pipeline_params_override"))
    mutations_chunk = f"{CYAN}{mutations}{RESET}  " if mutations else ""

    if scores.get("invalid"):
        failures = scores["validation_failures"]
        return IndividualSummary(
            status="invalid",
            tag=f"{YELLOW}INVALID{RESET}",
            body_line="",
            detail_lines=_fmt_validation_failure_lines(failures),
        )

    acc = scores["accuracy"]
    hits = scores.get("hits", 0)
    n = scores.get("total", 0)
    ci_lo, ci_hi = wilson_ci(hits, n)
    delta = acc - baseline_acc
    tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"

    aborted = bool(scores.get("escalation_aborted"))
    if aborted:
        scored_q = scores.get("scored_queries", n)
        expected_q = scores.get("expected_queries", n)
        hit_str = f"{hits}/{scored_q} hits {YELLOW}⚠ aborted {scored_q}/{expected_q}{RESET}"
    else:
        hit_str = f"{hits}/{n} hits"
    body_line = f"{mutations_chunk}{hit_str}  vs baseline: {_fmt_delta(delta)}"

    detail_lines: list[str] = []
    if scores.get("elimination_stopped"):
        elim_ctx = scores["elimination_context"]
        prior_label = elim_ctx.get("triggered_by_prior_label")
        detail_lines.append(format_elimination_summary(elim_ctx, prior_label))

    # Composite + degraded share the bottom info rule — joined with two
    # spaces so the box bottom carries both numbers in one line. Computed
    # for every status (ok / aborted / eliminated) since the score report
    # carries them regardless of why scoring stopped.
    bottom_extras: list[str] = []
    comp = scores.get("composite")
    if comp is not None and comp != acc:
        bottom_extras.append(f"composite={comp:.4f}")
    degraded = scores.get("degraded_queries", 0)
    if degraded:
        bottom_extras.append(f"{YELLOW}⚠ {degraded}/{n} degraded{RESET}")
    if bottom_extras:
        detail_lines.append("  ".join(bottom_extras))

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
