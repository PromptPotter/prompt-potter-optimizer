"""``review.md`` per-cycle renderer — prompt-iteration feedback surface.

Pure function over loaded dicts (peer of presentation log_md). No I/O.

Inputs: ``index`` (campaigns/{cycle_id}/index.json), ``rounds`` (per-round
opt state), ``round_audits`` (per-round LLM I/O, same length), and three
task-context strings from ``cycle.task_context``."""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.l1.stats import L1Stats, compute_l1_stats
from promptpotter.application.optimization.validators.l1_behavior import (
    CHECK_REGISTRY,
    CheckResult,
    ValidatorContext,
    extract_l1_variants,
    run_all_checks,
)
from promptpotter.application.optimization.validators.l2_behavior import run_all_l2_checks

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

    behavior_per_round, l2_behavior_per_round = _compute_behavior_per_round(
        rounds, audits, ctx_items, param_unlock_round
    )
    final = index.get("final") or {}
    origin_composite_fitness = float(final.get("origin_composite_fitness") or 0.0)
    stats = compute_l1_stats(
        list(rounds),
        origin_composite_fitness=origin_composite_fitness,
        behavior_results=behavior_per_round,
        l2_behavior_results=l2_behavior_per_round,
        audits=audits,
    )

    repairs_per_round = [_schema_repair_count(a) for a in audits]
    calls_per_round = [_optimizer_call_count(a) for a in audits]
    parts: list[str] = []
    parts += _render_header(index, final, stats)
    parts += _render_stats_block(stats, repairs_per_round, calls_per_round)
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
            schema_repair_retries=repairs_per_round[i],
        )

    return "\n".join(parts).rstrip() + "\n"


def _schema_repair_count(audit: dict[str, Any] | None) -> int:
    """Sum ``schema_repair_attempts`` across optimizer nodes; non-zero ⇒ a second round-trip was paid.
    Cycle-wide rate is the cleanest single-number quality signal for an L1 meta-prompt."""
    if not audit:
        return 0
    nodes = audit.get("nodes") or {}
    if not isinstance(nodes, dict):
        return 0
    return sum(
        int(block.get("schema_repair_attempts") or 0)
        for block in nodes.values()
        if isinstance(block, dict)
    )


def _optimizer_call_count(audit: dict[str, Any] | None) -> int:
    """Count optimizer LLM nodes in this round's audit (excludes ``l1_score``)."""
    if not audit:
        return 0
    nodes = audit.get("nodes") or {}
    if not isinstance(nodes, dict):
        return 0
    return sum(1 for k, v in nodes.items() if k != "l1_score" and isinstance(v, dict))


# --- behaviour-check evaluation -------------------------------------------


def _compute_behavior_per_round(
    rounds: list[dict[str, Any]],
    audits: list[dict[str, Any] | None],
    context_object: list[str],
    param_unlock_round: int,
) -> tuple[list[list[CheckResult]], list[list[CheckResult]]]:
    """Per-round L1 + L2 behaviour-check results (same length as ``rounds``).
    L2 returns ``[]`` for rounds where L2 didn't fire — absent fire ≠ conformance failure."""
    l1_out: list[list[CheckResult]] = []
    l2_out: list[list[CheckResult]] = []
    prior_audits: list[dict[str, Any]] = []
    for i, round_data in enumerate(rounds):
        audit = audits[i] if i < len(audits) else None
        if audit is None:
            l1_out.append([])
            l2_out.append([])
            continue
        round_num = int(round_data.get("round") or i)
        peaked_raw = round_data.get("axis_memory_peaked") or []
        peaked_axes = frozenset(str(a) for a in peaked_raw if isinstance(a, str))
        ctx = ValidatorContext(
            round_num=round_num,
            prior_rounds=list(prior_audits),
            opt_search_point=dict(round_data.get("opt_search_point") or {}),
            context_object=context_object,
            param_unlock_round=param_unlock_round,
            peaked_axes=peaked_axes,
        )
        l1_out.append(run_all_checks(audit, ctx))
        l2_out.append(run_all_l2_checks(audit, ctx))
        prior_audits.append(audit)
    return l1_out, l2_out


# --- rendering helpers ----------------------------------------------------


def _render_header(index: dict[str, Any], final: dict[str, Any], stats: L1Stats) -> list[str]:
    cycle_id = index.get("cycle_id") or "(unknown cycle)"
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


def _render_stats_block(
    stats: L1Stats,
    repairs_per_round: list[int],
    calls_per_round: list[int],
) -> list[str]:
    rounds_to_95 = "—" if stats.rounds_to_95 is None else str(stats.rounds_to_95)
    # L2 conformance "n/a" when L2 never fired (distinguishes from a vacuous 1.0).
    l2_conf = "n/a" if stats.l2_fires == 0 else f"{stats.l2_behavior_pass_rate:.2f}"
    lines = [
        "## L1Stats",
        "",
        f"- **rounds_to_95**: {rounds_to_95}",
        f"- yield_rate: {stats.yield_rate:.2f}",
        f"- top_lift_mean: {stats.top_lift_mean:+.4f}",
        f"- behavior_pass_rate: {stats.behavior_pass_rate:.2f}",
        f"- l2_behavior_pass_rate: {l2_conf}",
        f"- stagnation_max: {stats.stagnation_max}",
        f"- l2_fires: {stats.l2_fires}",
    ]
    if stats.forbidden_axis_attempts > 0:
        healed = "healed" if stats.forbidden_axis_healed else "NOT healed"
        lines.append(f"- forbidden_axis_attempts: {stats.forbidden_axis_attempts} ({healed})")
    repairs_total = sum(repairs_per_round)
    calls_total = sum(calls_per_round)
    if calls_total:
        rate_pct = 100.0 * repairs_total / calls_total
        lines.append(
            f"- schema_repair_retries: {repairs_total}/{calls_total} optimizer calls "
            f"({rate_pct:.0f}% paid a second round-trip)"
        )
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
    schema_repair_retries: int = 0,
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
    if schema_repair_retries:
        parts.append(f"- schema_repair_retries: {schema_repair_retries}")
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

    critique = format_l1_critique_for_prompt(round_data.get("critique")).strip()
    if not critique:
        return []
    quoted = critique.replace("\n", "\n> ")
    return ["**Critique**", "", f"> {quoted}", ""]


def _is_generation_only(round_data: dict[str, Any]) -> bool:
    return str(round_data.get("status") or "").strip() == "generation_only"
