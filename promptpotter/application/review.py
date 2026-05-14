"""``review.md`` per-cycle renderer — M10's prompt-iteration feedback surface.

Pure function over loaded dicts (peer of ``presentation/views/log_md.py``).
No I/O. Wiring + write site live in ``runner.py`` so the renderer stays
testable as a transformation.

Inputs
------
- ``index`` — ``campaigns/{cycle_id}/index.json`` payload (carries
  origin / best / final block with ``origin_composite_fitness``,
  ``prompt_hashes``, ``mode``).
- ``rounds`` — per-round optimizer state from ``rounds/trial_NNNN.json``,
  in round order. Carries ``opt_search_point`` (lineage, task_context,
  critique), composite_fitness, accuracy, l1_yield.
- ``round_audits`` — per-round LLM I/O from ``.runtime/cache/rounds/round_NNNN.json``,
  same length and order as ``rounds``. Source of L1 variants for the
  variants table + behaviour checks. ``None`` for rounds with no audit.
- ``context_object`` — three task-context strings the wiring layer pulls
  off ``cycle.task_context`` (pipeline_purpose / optimization_goals /
  key_challenges by default).

Output is a self-contained markdown document the operator and the
``potter-l1-meta-campaign`` skill consume after each round.
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.l1.stats import L1Stats, compute_l1_stats
from promptpotter.application.optimization.validators.l1_behavior import (
    CHECK_REGISTRY,
    CheckContext,
    CheckResult,
    extract_l1_variants,
    run_all_checks,
)

__all__ = ["render_review_md"]


def render_review_md(
    index: dict[str, Any],
    rounds: list[dict[str, Any]],
    *,
    round_audits: list[dict[str, Any] | None] | None = None,
    context_object: list[str] | None = None,
    param_unlock_round: int = 3,
) -> str:
    """Render ``review.md`` from index + rounds + per-round audit dicts."""
    audits = list(round_audits or [None] * len(rounds))
    if len(audits) < len(rounds):
        audits.extend([None] * (len(rounds) - len(audits)))
    ctx_items = [c for c in (context_object or []) if isinstance(c, str) and c.strip()]

    behavior_per_round = _compute_behavior_per_round(rounds, audits, ctx_items, param_unlock_round)
    final = index.get("final") or {}
    origin_composite_fitness = float(final.get("origin_composite_fitness") or 0.0)
    stats = compute_l1_stats(
        list(rounds),
        origin_composite_fitness=origin_composite_fitness,
        behavior_results=behavior_per_round,
        audits=audits,
    )

    parts: list[str] = []
    parts += _render_header(index, final, stats)
    parts += _render_stats_block(stats)
    parts += _render_behavior_summary(behavior_per_round, stats)
    parts += ["## Rounds", ""]

    sweep_mode = (final.get("mode") or "").strip() == "sweep"
    last_idx = len(rounds) - 1
    for i, round_data in enumerate(rounds):
        is_peek = sweep_mode and i == last_idx and _is_generation_only(round_data)
        parts += _render_round(
            round_data,
            audits[i],
            behavior_per_round[i] if i < len(behavior_per_round) else [],
            is_peek=is_peek,
        )

    return "\n".join(parts).rstrip() + "\n"


# --- behaviour-check evaluation -------------------------------------------


def _compute_behavior_per_round(
    rounds: list[dict[str, Any]],
    audits: list[dict[str, Any] | None],
    context_object: list[str],
    param_unlock_round: int,
) -> list[list[CheckResult]]:
    out: list[list[CheckResult]] = []
    prior_audits: list[dict[str, Any]] = []
    for i, round_data in enumerate(rounds):
        audit = audits[i] if i < len(audits) else None
        if audit is None:
            out.append([])
            continue
        round_num = int(round_data.get("round") or i)
        ctx = CheckContext(
            round_num=round_num,
            prior_rounds=list(prior_audits),
            opt_search_point=dict(round_data.get("opt_search_point") or {}),
            context_object=context_object,
            param_unlock_round=param_unlock_round,
        )
        out.append(run_all_checks(audit, ctx))
        prior_audits.append(audit)
    return out


# --- rendering helpers ----------------------------------------------------


def _render_header(index: dict[str, Any], final: dict[str, Any], stats: L1Stats) -> list[str]:
    cycle_id = index.get("campaign_id") or "(unknown cycle)"
    mode = (final.get("mode") or "full").strip() or "full"
    parts: list[str] = [
        f"# Review — {cycle_id}",
        "",
        f"_mode: **{mode}** · round-1 verdict: **{stats.round_1_verdict}**_",
        "",
    ]
    hashes = final.get("prompt_hashes") or {}
    if hashes:
        parts.append("**Prompt hashes**")
        parts.append("")
        for name in ("l1_generate", "l1_critique", "l2_context", "l3_plan"):
            short = (hashes.get(name) or "")[:8]
            if short:
                parts.append(f"- `{name}`: `{short}`")
        parts.append("")
    return parts


def _render_stats_block(stats: L1Stats) -> list[str]:
    rounds_to_95 = "—" if stats.rounds_to_95 is None else str(stats.rounds_to_95)
    lines = [
        "## L1Stats",
        "",
        f"- **rounds_to_95**: {rounds_to_95}",
        f"- yield_rate: {stats.yield_rate:.2f}",
        f"- top_lift_mean: {stats.top_lift_mean:+.4f}",
        f"- behavior_pass_rate: {stats.behavior_pass_rate:.2f}",
        f"- stagnation_max: {stats.stagnation_max}",
        f"- l2_fires: {stats.l2_fires}",
    ]
    if stats.forbidden_axis_attempts > 0:
        healed = "healed" if stats.forbidden_axis_healed else "NOT healed"
        lines.append(f"- forbidden_axis_attempts: {stats.forbidden_axis_attempts} ({healed})")
    lines.append("")
    return lines


def _render_behavior_summary(
    behavior_per_round: list[list[CheckResult]], stats: L1Stats
) -> list[str]:
    if not behavior_per_round or not any(behavior_per_round):
        return []
    parts: list[str] = ["## Behaviour-check summary", ""]
    for check_id in CHECK_REGISTRY:
        fails = sum(
            1
            for round_res in behavior_per_round
            for c in round_res
            if c.check_id == check_id and not c.passed
        )
        runs = sum(
            1 for round_res in behavior_per_round for c in round_res if c.check_id == check_id
        )
        marker = "✗" if fails else "✓"
        parts.append(f"- {marker} `{check_id}` — {runs - fails}/{runs} rounds passed")
        if check_id == "forbidden_axes_honored" and stats.forbidden_axis_attempts > 0:
            verb = "healed by validator + L2" if stats.forbidden_axis_healed else "NOT healed"
            parts.append(f"  → {verb} ({stats.forbidden_axis_attempts} attempts across cycle)")
    parts.append("")
    return parts


def _render_round(
    round_data: dict[str, Any],
    audit: dict[str, Any] | None,
    checks: list[CheckResult],
    *,
    is_peek: bool,
) -> list[str]:
    round_num = round_data.get("round", "?")
    osp = round_data.get("opt_search_point") or {}
    lineage = osp.get("lineage") or {}
    suffix = " (next-gen peek)" if is_peek else ""
    parts: list[str] = [
        f"### Round {round_num}{suffix}",
        "",
    ]
    if not is_peek:
        parts += [
            f"- accuracy: {float(round_data.get('accuracy', 0.0) or 0.0):.1%}",
            f"- composite_fitness: `{float(round_data.get('composite_fitness', 0.0) or 0.0):.4f}`",
            f"- improved: **{'yes' if round_data.get('improved') else 'no'}**",
        ]
    parts += _render_l1_inputs(osp, lineage)
    parts += _render_check_checklist(checks)
    parts += _render_variants_table(audit, scored=not is_peek)
    parts += _render_critique(round_data)
    return parts


def _render_l1_inputs(osp: dict[str, Any], lineage: dict[str, Any]) -> list[str]:
    parts: list[str] = ["", "**L1 inputs**", ""]
    tc = osp.get("task_context") or {}
    if isinstance(tc, dict) and tc:
        keys = ", ".join(sorted(k for k, v in tc.items() if v))
        parts.append(f"- task_context fields: {keys or '_(empty)_'}")
    else:
        parts.append("- task_context: _(empty)_")
    src = (lineage.get("source") or "").strip()
    if src:
        parts.append(f"- lineage source: `{src}`")
    changes = (lineage.get("changes_description") or "").strip()
    if changes:
        parts.append(f"- parent changes: {changes}")
    parts.append("")
    return parts


def _render_check_checklist(checks: list[CheckResult]) -> list[str]:
    if not checks:
        return ["**Behaviour checks:** _(no audit available)_", ""]
    parts: list[str] = ["**Behaviour checks**", ""]
    for c in checks:
        marker = "✓" if c.passed else "✗"
        parts.append(f"- {marker} `{c.check_id}` — {c.evidence}")
    parts.append("")
    return parts


def _render_variants_table(audit: dict[str, Any] | None, *, scored: bool) -> list[str]:
    variants = extract_l1_variants(audit)
    if not variants:
        return []
    parts: list[str] = ["**Variants**", ""]
    if scored:
        parts.append(
            "| variant | composite_fitness | acc | Δ_parent | Δ_origin | beat | evidence | changes |"
        )
        parts.append("|---|---|---|---|---|---|---|---|")
        # Without per-variant scores in the audit dict the table degrades to
        # changes_description only — full per-variant scoring lives on the
        # round_data dict's candidate_scores array, surfaced when available.
        for v in variants:
            name = str(v.get("variant_name") or "?")
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            parts.append(f"| `{name}` | — | — | — | — | — | {evidence} | {changes} |")
    else:
        parts.append("| cand_id | changes | derived_axes | evidence |")
        parts.append("|---|---|---|---|")
        for v in variants:
            name = str(v.get("variant_name") or "?")
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            axes = ", ".join(sorted((v.get("pipeline_params_override") or {}).keys()))
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            parts.append(f"| `{name}` | {changes} | {axes} | {evidence} |")
    parts.append("")
    return parts


def _fmt_evidence_cell(raw: object) -> str:
    """Render evidence_grounding for the variants table — one cell, terse."""
    if not isinstance(raw, dict):
        return "—"
    field_name = str(raw.get("field") or "").strip()
    citation = str(raw.get("citation") or "").replace("|", "\\|").strip()
    if not field_name:
        return "—"
    if citation:
        return f"`{field_name}` — {citation[:60]}"
    return f"`{field_name}` _(no citation)_"


def _render_critique(round_data: dict[str, Any]) -> list[str]:
    from promptpotter.application.optimization.dispatch.hub import (
        format_l1_critique_for_prompt,
    )

    critique = format_l1_critique_for_prompt(round_data.get("critique") or {}).strip()
    if not critique:
        return []
    quoted = critique.replace("\n", "\n> ")
    return ["**Critique**", "", f"> {quoted}", ""]


def _is_generation_only(round_data: dict[str, Any]) -> bool:
    return str(round_data.get("status") or "").strip() == "generation_only"
