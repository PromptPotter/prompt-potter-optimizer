"""``review.md`` — the per-cycle conformance report, rendered from an ``index.json`` blob plus its
rounds and audits. Pure: no ``Session``, no ``Cycle``, no disk. That is what lets ``scripts/render_review.py``
re-render a finished cycle from what is already on disk, and it is why this is not in ``output.py``:
sharing a module with the session-scoped writers left ``render_review_md`` under a second ``__all__``
that silently shadowed the first, so the file's one externally-called function was never exported."""

from __future__ import annotations

from typing import Any

from promptpotter.application.optimization.l1.stats import L1Stats, compute_l1_stats
from promptpotter.application.optimization.validators.behavior_base import (
    CheckResult,
    ValidatorContext,
)
from promptpotter.application.optimization.validators.l1_behavior import (
    CHECK_REGISTRY,
    extract_l1_variants,
    run_all_checks,
)
from promptpotter.application.optimization.validators.l2_behavior import run_all_l2_checks
from promptpotter.domain.escalation_signals import exploration_budget
from promptpotter.domain.phases import StopReason
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import (
    DegradationHealth,
    RoundResult,
    ScoredCandidate,
    candidate_label,
    is_round_winner,
    overlap_series,
)

__all__ = ["render_review_md"]


def render_review_md(
    index: dict[str, Any],
    rounds: list[RoundResult],
    *,
    round_audits: list[dict[str, Any] | None] | None = None,
    context_object: list[str] | None = None,
    l1_patience: int,
) -> str:
    audits = list(round_audits or [None] * len(rounds))
    if len(audits) < len(rounds):
        audits.extend([None] * (len(rounds) - len(audits)))
    ctx_items = [c for c in (context_object or []) if isinstance(c, str) and c.strip()]

    behavior_per_round, l2_behavior_per_round = _compute_behavior_per_round(
        rounds, audits, ctx_items, l1_patience
    )
    final = index.get("final") or {}
    # Absent means the origin was never scored, which is not the same as scoring 0.0 — `_top_lifts`
    # drops round 0's lift rather than measuring it against a bar nothing established.
    origin_cf = final.get("origin_composite_fitness")
    origin_composite_fitness = float(origin_cf) if isinstance(origin_cf, int | float) else None
    stats = compute_l1_stats(
        list(rounds),
        origin_composite_fitness=origin_composite_fitness,
        behavior_results=behavior_per_round,
        l2_behavior_results=l2_behavior_per_round,
    )

    repairs_per_round = [_schema_repair_count(a) for a in audits]
    calls_per_round = [_optimizer_call_count(a) for a in audits]
    halt = _halt_info(index, rounds)
    parts: list[str] = []
    parts += _render_header(index, final, stats, halt)
    parts += _render_stats_block(stats, repairs_per_round, calls_per_round, halt)
    parts += _render_behavior_summary(behavior_per_round)
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
    Cycle-wide rate is the cleanest single-number quality signal for an L1 optimizer prompt."""
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
    rounds: list[RoundResult],
    audits: list[dict[str, Any] | None],
    context_object: list[str],
    l1_patience: int,
) -> tuple[list[list[CheckResult]], list[list[CheckResult]]]:
    """Per-round L1 + L2 behaviour-check results (same length as ``rounds``).
    L2 returns ``[]`` for rounds where L2 didn't fire — absent fire ≠ conformance failure."""
    l1_out: list[list[CheckResult]] = []
    l2_out: list[list[CheckResult]] = []
    prior_audits: list[dict[str, Any]] = []
    # Stall depth entering each round, reconstructed from the persisted ``improved``
    # flags (the round file doesn't carry the live l1_stall_count). Same recurrence as
    # ``EscalationFSM.observe_round``: reset to 0 on improvement, else +1. Read BEFORE
    # the update so each round's exploration_budget matches what its L1 generation saw.
    stall = 0
    for i, round_data in enumerate(rounds):
        round_num = round_data.round
        budget = exploration_budget(stall, l1_patience).value if round_num >= 1 else None
        audit = audits[i] if i < len(audits) else None
        if round_num >= 1:
            stall = 0 if round_data.improved else stall + 1
        if audit is None:
            l1_out.append([])
            l2_out.append([])
            continue
        opt_sp = round_data.opt_sp
        ctx = ValidatorContext(
            round_num=round_num,
            prior_rounds=list(prior_audits),
            opt_sp=opt_sp.model_dump() if opt_sp else {},
            context_object=context_object,
            exploration_budget=budget,
            peaked_axes=frozenset(round_data.axis_memory_peaked),
        )
        l1_out.append(run_all_checks(audit, ctx))
        l2_out.append(run_all_l2_checks(audit, ctx))
        prior_audits.append(audit)
    return l1_out, l2_out


# --- rendering helpers ----------------------------------------------------


def _halt_info(index: dict[str, Any], rounds: list[RoundResult]) -> dict[str, str] | None:
    """The cycle's terminal health story, or ``None`` when it ended cleanly. Gated on the cycle's
    TERMINAL state, never on a critical round in history that L2 then self-healed away."""
    stop_reason = (index.get("stop_reason") or "").strip()
    terminated = "yes" if stop_reason == StopReason.ABORT else ""
    last_health: DegradationHealth | None = None
    last_critical: DegradationHealth | None = None
    for r in rounds:
        if r.health is not None:
            last_health = r.health  # ends as the last GRADED round (probes carry None)
            if r.health.grade == "critical":
                last_critical = r.health
    ended_critical = last_health is not None and last_health.grade == "critical"
    if not ended_critical and not terminated:
        return None
    # The terminate-triggering round is the last completed (critical) round; reuse it
    # to name the dead node (the ended-critical path uses the same round).
    if last_critical is not None:
        tag = last_critical.cause or "critical"
        return {
            "tag": tag,
            "node": last_critical.dominant_node or "",
            "action": (last_critical.suggested_action or "").strip(),
            "terminated": terminated,
        }
    return {"tag": "terminate_proposal", "node": "", "action": "", "terminated": terminated}


def _render_header(
    index: dict[str, Any], final: dict[str, Any], stats: L1Stats, halt: dict[str, str] | None
) -> list[str]:
    cycle_id = index.get("cycle_id") or "(unknown cycle)"
    mode = (final.get("mode") or "full").strip() or "full"
    parts: list[str] = [
        f"# Review — {cycle_id}",
        "",
        f"_mode: **{mode}** · round-1 conformance: **{stats.round_1_verdict}**_",
        "",
    ]
    if halt is not None:
        where = f" — node `{halt['node']}`" if halt["node"] else ""
        parts.append(f"> **HALTED — {halt['tag']}**{where}")
        if halt["action"]:
            parts.append(">")
            parts.append(f"> {halt['action']}")
        parts.append("")
    hashes = final.get("prompt_hashes") or {}
    if hashes:
        parts.append("**Prompt hashes**")
        parts.append("")
        # Walk what the stamp CONTAINS, never a hand-listed subset: a name missing from the list
        # renders two cycles under different optimizer prompts as identical hash blocks.
        for name in sorted(hashes):
            short = (hashes.get(name) or "")[:8]
            if short:
                parts.append(f"- `{name}`: `{short}`")
        parts.append("")
    return parts


def _render_stats_block(
    stats: L1Stats,
    repairs_per_round: list[int],
    calls_per_round: list[int],
    halt: dict[str, str] | None,
) -> list[str]:
    def _rate(value: float | None, spec: str = ".2f") -> str:
        """An unmeasured rate renders as ``—``, never as a number the cycle never produced."""
        return "—" if value is None else format(value, spec)

    lines = [
        "## L1Stats",
        "",
        f"- **rounds_to_95**: {'—' if stats.rounds_to_95 is None else stats.rounds_to_95}",
        f"- yield_rate: {_rate(stats.yield_rate)}",
        f"- top_lift_mean: {_rate(stats.top_lift_mean, '+.4f')}",
        f"- behavior_pass_rate: {_rate(stats.behavior_pass_rate)}",
        f"- l2_behavior_pass_rate: {_rate(stats.l2_behavior_pass_rate)}",
        f"- stagnation_max: {stats.stagnation_max}",
        f"- l2_fires: {stats.l2_fires}",
    ]
    # A terminate is an L2 fire that produces no l2-sourced round, so `l2_fires`
    # alone reads 0 — name it explicitly so an L2 halt isn't invisible.
    if halt is not None and halt["terminated"]:
        node = f" ({halt['node']})" if halt["node"] else ""
        lines.append(f"- l2_terminated: {halt['tag']}{node}")
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
    behavior_per_round: list[list[CheckResult]],
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
    parts.append("")
    return parts


def _render_round(
    round_data: RoundResult,
    audit: dict[str, Any] | None,
    checks: list[CheckResult],
    *,
    is_peek: bool,
    schema_repair_retries: int = 0,
) -> list[str]:
    opt_sp = round_data.opt_sp.model_dump() if round_data.opt_sp else {}
    lineage = opt_sp.get("lineage") or {}
    suffix = " (next-gen peek)" if is_peek else ""
    parts: list[str] = [
        f"### Round {round_data.round}{suffix}",
        "",
    ]
    if not is_peek:
        parts += [
            f"- accuracy: {round_data.accuracy:.1%}",
            f"- composite_fitness: `{round_data.composite_fitness:.4f}`",
            f"- improved: **{'yes' if round_data.improved else 'no'}**",
        ]
        if series := overlap_series(round_data.overlap):
            parts.append(f"- overlap: {series}")
        if round_data.verdict_reason:
            # The round is won on θ-lift, so `improved` above names the outcome and nothing on
            # this page named the number behind it.
            parts.append(f"- verdict: {round_data.verdict_reason}")
    if schema_repair_retries:
        parts.append(f"- schema_repair_retries: {schema_repair_retries}")
    parts += _render_l1_inputs(opt_sp, lineage)
    parts += _render_check_checklist(checks)
    parts += _render_variants_table(audit, round_data, scored=not is_peek)
    parts += _render_critique(round_data)
    return parts


def _render_l1_inputs(opt_sp: dict[str, Any], lineage: dict[str, Any]) -> list[str]:
    parts: list[str] = ["", "**L1 inputs**", ""]
    tc = (opt_sp.get("memory") or {}).get("task_context") or {}
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


def _render_variants_table(
    audit: dict[str, Any] | None,
    round_data: RoundResult,
    *,
    scored: bool,
) -> list[str]:
    """Per-variant row: the audit dict carries what L1 PROPOSED, ``round_data`` what it MEASURED,
    joined on :func:`candidate_label`. Join on anything else and every score column prints ``—``."""
    variants = extract_l1_variants(audit)
    if not variants:
        return []
    parts: list[str] = ["**Variants**", ""]
    if scored:
        by_label = {c.label: c for c in round_data.candidate_scores}
        parts.append(
            "| variant | composite_fitness | acc | θ | lift vs parent | won | evidence | changes |"
        )
        parts.append("|---|---|---|---|---|---|---|---|")
        for i, v in enumerate(variants):
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            label = candidate_label(round_data.round, i)
            cells = _score_cells(by_label.get(label), round_data.winner_id)
            parts.append(f"| `{label}` | {cells} | {evidence} | {changes} |")
    else:
        parts.append("| cand_id | changes | derived_axes | evidence |")
        parts.append("|---|---|---|---|")
        for i, v in enumerate(variants):
            changes = (v.get("changes_description") or "").replace("|", "\\|").strip()[:80]
            axes = ", ".join(sorted((v.get("pipeline_params_override") or {}).keys()))
            evidence = _fmt_evidence_cell(v.get("evidence_grounding"))
            parts.append(f"| `C{i + 1}` | {changes} | {axes} | {evidence} |")
    parts.append("")
    return parts


def _score_cells(c: ScoredCandidate | None, winner_id: str) -> str:
    """``—`` only where there genuinely is no number — a variant the round never scored, one
    outside the election fit, or one sharing under two cells with its parent, where ``None`` is
    deliberate: a 0.0 there reads as a measurement.

    The ``won`` column is the ELECTED id, and the margin beside it is the θ-lift with its
    interval. Both replace a composite Δ and a ``✓`` derived from it — which is not the election
    rule and carried no interval, so the glyph read as a verdict the round had not made."""
    if c is None:
        return "— | — | — | — | —"
    theta = "—" if c.theta is None else f"{c.theta:+.3f}"
    lo, hi = c.matched_parent_lift_ci_lo, c.matched_parent_lift_ci_hi
    lift = (
        f"{c.matched_parent_lift:+.3f} [{lo:+.3f}, {hi:+.3f}]"
        if c.matched_parent_lift is not None and lo is not None and hi is not None
        else "—"
    )
    won = "✓" if is_round_winner(c.candidate_id, winner_id) else "·"
    return f"`{c.composite_fitness:.4f}` | {c.accuracy:.1%} | {theta} | {lift} | {won}"


def _fmt_evidence_cell(raw: object) -> str:
    if not isinstance(raw, dict):
        return "—"
    field_name = str(raw.get("field") or "").strip()
    citation = str(raw.get("citation") or "").replace("|", "\\|").strip()
    if not field_name:
        return "—"
    if citation:
        return f"`{field_name}` — {citation[:60]}"
    return f"`{field_name}` _(no citation)_"


def _render_critique(round_data: RoundResult) -> list[str]:
    critique = format_l1_critique_for_prompt(round_data.critique).strip()
    if not critique:
        return []
    quoted = critique.replace("\n", "\n> ")
    return ["**Critique**", "", f"> {quoted}", ""]


def _is_generation_only(round_data: RoundResult) -> bool:
    return round_data.status == "generation_only"
