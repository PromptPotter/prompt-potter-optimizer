"""Dispatch-message registry — single declarative catalogue of optimizer-prompt inputs.

Every L1 (generate/critique) / L2 / L3 prompt receives a ``{{dispatch_msg}}``
block assembled here. Each section is one function ``(cycle, **kwargs) -> str``
that returns its rendered text or ``""`` when not applicable.
:func:`assemble_dispatch_msg` walks :data:`LAYER_ORDER` for the target layer,
calls each section, and joins the non-empty results.

On L1_GENERATE the L2 directive supersedes the L1 critique when both are
populated (sliding window of 1).

L1_CRITIQUE sections share cross-cutting state (anomalies, near-miss query
set). A pre-pass — :func:`_compute_critique_context` — computes those facts
once and stashes them in a ``_CritiqueContext`` carried through kwargs;
sections then stay pure.
"""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.nodes.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
    format_axis_digest_block,
    format_escalation_report,
    format_runtime_failure_line,
    summarize_warning_inventory,
)
from promptpotter.application.optimization.utils import (
    candidate_keys_from_schema,
    get_candidates,
)
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.nodes.l1.score import L1ScoringResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult


class Layer(enum.StrEnum):
    """Optimizer layer that consumes a dispatch_msg."""

    L1_GENERATE = "L1_GENERATE"
    L1_CRITIQUE = "L1_CRITIQUE"
    L2 = "L2"
    L3 = "L3"


_PROMPT_BLOAT_CHARS = 3000
_RF_CFG_AXES = ("model", "temperature", "max_tokens", "reasoning_effort")

_L1_AXIS_LABELS: dict[str, str] = {
    "failure_clusters": "Common failure patterns",
    "dead_queries": "Dead queries (never hit)",
    "top_axes": "High-impact axes",
    "top_values": "Best-performing values",
}
_L1C_AXIS_LABELS: dict[str, str] = {
    "discriminating_queries": "Discriminating queries",
    "failure_clusters": "Failure clusters",
    "tractability": "Query tractability",
    "exhausted_axes": "Exhausted axes (DO NOT suggest these)",
    "value_trends": "Value trends",
    "improvement_attribution": "WHAT WORKED",
}
_L2_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_group_insights": "Failure group x axis",
    "persistent_failures": "Persistent failures",
    "volatile_queries": "Volatile queries (oscillating)",
}
_L3_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_clusters": "Failure clusters",
    "persistent_failures": "Persistent failures",
}

_TASK_CONTEXT_SKIP = frozenset({"raw_description", "upstream_context", "downstream_context"})


# ---------------------------------------------------------------------------
# L1_CRITIQUE pre-pass: cross-cutting facts computed once.
# ---------------------------------------------------------------------------


@dataclass
class _CritiqueContext:
    prompt_chars: int = 0
    candidate_keys: list[str] | None = None
    nm_queries: set[str] = field(default_factory=set)
    anomalies: list[str] = field(default_factory=list)
    rank_text: str = ""
    evolution_text: str = ""


def _compute_critique_context(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
) -> _CritiqueContext:
    candidate_keys = candidate_keys_from_schema(schema)
    prompt_chars = len(cycle.opt_sp.render())
    rank_text, nm_queries = _compute_rank_analysis(scoring_result.winner_results, candidate_keys)
    evolution_text, anomalies = _compute_round_evolution(cycle)
    return _CritiqueContext(
        prompt_chars=prompt_chars,
        candidate_keys=candidate_keys,
        nm_queries=nm_queries,
        anomalies=anomalies,
        rank_text=rank_text,
        evolution_text=evolution_text,
    )


def _compute_rank_analysis(
    results: list[QueryResult], candidate_keys: list[str] | None
) -> tuple[str, set[str]]:
    """Return (section_text, near_miss_query_set)."""
    keys = candidate_keys or None
    rank_map: dict[int, int | None] = {
        i: find_rank(get_candidates(r, keys), r.get("ground_truth", ""))
        for i, r in enumerate(results)
        if not is_error_result(r)
    }
    rank_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "not_found": 0}
    near_misses: list[dict] = []
    for i, r in enumerate(results):
        if is_error_result(r):
            continue
        rank = rank_map.get(i)
        if rank == 1:
            rank_buckets["1"] += 1
        elif rank is not None and rank <= 10:
            rank_buckets["2-5" if rank <= 5 else "6-10"] += 1
            near_misses.append(
                {
                    "query": r["query"][:80],
                    "ground_truth": r.get("ground_truth", "")[:60],
                    "rank": rank,
                    "predicted": r.get("predicted", "?")[:60],
                }
            )
        elif rank is not None and rank <= 20:
            rank_buckets["11-20"] += 1
        else:
            rank_buckets["not_found"] += 1
    nm_queries = {nm["query"] for nm in near_misses}

    n_valid = sum(1 for r in results if not is_error_result(r))
    if not n_valid:
        return "", nm_queries
    lines = [
        "## CANDIDATE RANK ANALYSIS",
        "Where does ground truth appear in candidate list?",
    ]
    for bucket, count in rank_buckets.items():
        lines.append(f"  Rank {bucket}: {count}")
    for k in (1, 3, 5, 10):
        in_top_k = sum(1 for rank in rank_map.values() if rank is not None and rank <= k)
        lines.append(f"  top-{k}: {in_top_k / n_valid:.0%}")
    if near_misses:
        lines.append(f"\nNear misses ({len(near_misses)} — GT in candidates but not rank 1):")
        for nm in near_misses[:15]:
            lines.append(
                f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                f"(GT: {nm['ground_truth']})"
            )
    return "\n".join(lines), nm_queries


def _compute_round_evolution(cycle: Cycle) -> tuple[str, list[str]]:
    """Return (section_text, anomalies). Plateau detection emits a [MEDIUM] flag."""
    anomalies: list[str] = []
    rounds = cycle.rounds
    if not rounds:
        return "", anomalies
    lines = [
        "## ROUND EVOLUTION",
        "Round  Accuracy  Delta   Degraded  Candidates",
    ]
    prev_acc: float | None = None
    plateau_count = 0
    for r in rounds:
        acc = r.accuracy
        delta = acc - prev_acc if prev_acc is not None else 0.0
        lines.append(
            f"  {r.round:>5}  {acc:>7.1%}  {delta:>+6.1%}  "
            f"{getattr(r, 'degraded_queries', 0):>8}  {len(r.candidate_scores):>10}"
        )
        plateau_count = plateau_count + 1 if abs(delta) < 0.01 else 0
        prev_acc = acc
    for i in range(1, len(rounds)):
        prev_pp = rounds[i - 1].pipeline_params or {}
        curr_pp = rounds[i].pipeline_params or {}
        changed = {
            k
            for k in set(prev_pp) | set(curr_pp)
            if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
        }
        if changed:
            lines.append(
                f"  Round {rounds[i - 1].round}→{rounds[i].round}: {', '.join(sorted(changed))}"
            )
    if plateau_count >= 2:
        anomalies.append(
            f"[MEDIUM] plateau_signal: {plateau_count} consecutive rounds with <1% improvement."
        )
    return "\n".join(lines), anomalies


# ---------------------------------------------------------------------------
# Section renderers — each returns the section text or "" when inactive.
# Layer is passed via kwarg so a single section can label itself differently
# under different layers (only ``_section_l2_directive`` uses this today).
# ---------------------------------------------------------------------------


def _section_pipeline_schema_text(
    _cycle: Cycle, *, pipeline_schema_text: str = "", **_: object
) -> str:
    return pipeline_schema_text or ""


def _section_failure_analysis(cycle: Cycle, **_: object) -> str:
    fa = cycle.opt_sp.failure_analysis
    if not fa or not fa.patterns:
        return ""
    lines = [f"FAILURE ANALYSIS ({fa.total_failures} failures / {fa.total_results} total):"]
    for i, pat in enumerate(fa.patterns[:2], 1):
        lines.append(f"  {i}. {pat.name} — {pat.query_count} queries ({pat.fraction:.0%})")
        if pat.example_queries:
            lines.append(f'     Example: "{pat.example_queries[0][:60]}"')
        sig = {
            k: v for k, v in pat.signals.items() if k not in ("error", "degraded", "total_time_ms")
        }
        if sig:
            sig_str = ", ".join(f"{k}={v}" for k, v in list(sig.items())[:2])
            lines.append(f"     Signals: {sig_str}")
    return "\n".join(lines)


def _section_axes_l1(cycle: Cycle, **_: object) -> str:
    if cycle.axes is None:
        return ""
    digest = cycle.axes.digest_for_l1_generate()
    return (
        format_axis_digest_block(digest, _L1_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if digest
        else ""
    )


def _section_task_context(cycle: Cycle, **_: object) -> str:
    tc = cycle.opt_sp.task_context
    if not tc:
        return ""
    lines = "\n".join(
        f"  {k}: {val}" for k, val in tc.items() if val and k not in _TASK_CONTEXT_SKIP
    )
    return f"CONTEXT:\n{lines}" if lines else ""


def _section_escalation_probe(cycle: Cycle, **_: object) -> str:
    """Probe-round per-query warning block — fires only when probe AND journal present."""
    if not cycle.probe_next_round:
        return ""
    journal = cycle.opt_sp.escalation_journal
    if not journal:
        return ""
    lines = [
        "PROBE ROUND: queries have recurring pipeline warnings. "
        "Generate candidates that address pipeline robustness."
    ]
    warning_inventory = cycle.opt_sp.warning_inventory or None
    if warning_inventory:
        inv = summarize_warning_inventory(warning_inventory)
        if inv:
            lines.extend(["", inv])
        step_counts: Counter[str] = Counter()
        for entry in warning_inventory.values():
            for wtype in entry.get("warnings", {}):
                step_counts[wtype.split(":", 1)[0]] += 1
        if step_counts:
            dom_step, dom_count = step_counts.most_common(1)[0]
            lines.append(f"\nDominant step: {dom_step} ({dom_count} warnings)")
            tried = [ej for ej in journal if ej.get("problem_step") == dom_step][-3:]
            if tried:
                lines.append(f"Previous attempts at {dom_step}:")
                lines.extend(
                    f"  - {ej.get('degraded_rate', 0):.0%} degraded, {ej.get('warning_types', {})}"
                    for ej in tried
                )
    return "\n".join(lines)


def _section_escalation_alert(cycle: Cycle, **_: object) -> str:
    """Non-probe aggregated alert — suppressed by an active l2_directive."""
    if cycle.probe_next_round:
        return ""
    if cycle.opt_sp.l2_directive:
        return ""
    journal = cycle.opt_sp.escalation_journal
    if not journal:
        return ""
    latest = journal[-1]
    alert = [
        f"PIPELINE ISSUE: {latest.get('degraded_rate', 0):.0%} of queries "
        f"degrade at {latest.get('problem_step', 'unknown')}. "
        "Address pipeline instability."
    ]
    if len(journal) > 1:
        alert.append(f"{len(journal)} prior attempts unresolved.")
    if latest.get("warning_types"):
        alert.append(f"Warnings: {latest['warning_types']}")
    return "\n".join(alert)


def _section_l2_directive(cycle: Cycle, *, layer: Layer, **_: object) -> str:
    v = cycle.opt_sp.l2_directive
    if not v:
        return ""
    label = "DIRECTIVE:" if layer is Layer.L1_GENERATE else "PREVIOUS DIRECTIVE:"
    return f"{label}\n{v}"


def _section_l1_critique_text(cycle: Cycle, **_: object) -> str:
    v = cycle.opt_sp.l1_critique_text
    return f"CRITIQUE:\n{v}" if v else ""


def _section_plan(cycle: Cycle, **_: object) -> str:
    v = cycle.opt_sp.plan
    return f"PLAN:\n{v}" if v else ""


def _escalation_report_text(
    cycle: Cycle, escalation_check_result: dict | None, pipeline_params: dict | None
) -> str:
    if not escalation_check_result:
        return ""
    schema = cycle.session.pipeline_schema if cycle.session is not None else None
    text = format_escalation_report(
        escalation_check_result,
        cycle.opt_sp.escalation_journal or None,
        pipeline_params,
        pipeline_schema=schema,
    )
    return text or ""


def _section_escalation_section(
    cycle: Cycle,
    *,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    **_: object,
) -> str:
    return _escalation_report_text(cycle, escalation_check_result, pipeline_params)


def _section_warning_inventory(
    cycle: Cycle,
    *,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    **_: object,
) -> str:
    """L2 fallback: per-query warning inventory when no escalation section."""
    if _escalation_report_text(cycle, escalation_check_result, pipeline_params):
        return ""
    inventory = cycle.opt_sp.warning_inventory
    if not inventory:
        return ""
    text = summarize_warning_inventory(inventory)
    return (text + "\n") if text else ""


def _section_validation_failures(
    _cycle: Cycle,
    *,
    candidate_scores: list[dict] | None = None,
    **_: object,
) -> str:
    vfs: list[dict] = []
    for cs in candidate_scores or []:
        vfs.extend(cs["validation_failures"])
    if not vfs:
        return ""
    lines = ["L1 VALIDATION FAILURES (prior round produced structurally invalid candidates):"]
    for vf in vfs:
        allowed = vf.get("allowed") or []
        allowed_str = ", ".join(allowed[:5]) + (
            f" (+{len(allowed) - 5} more)" if len(allowed) > 5 else ""
        )
        lines.append(
            f"  ⚠ axis={vf.get('axis')} proposed={vf.get('value')!r} reason={vf.get('reason')}"
        )
        lines.append(f"    allowed: [{allowed_str}]")
    lines.append(
        "  ↳ Required L2 action: produce a directive that names the disallowed "
        "value(s) explicitly and instructs L1 to choose only from the allowed "
        'set. Example: "For llm_only.model, use ONLY one of: <list>. Do NOT '
        'propose any other value such as gpt-4o." Self-healing depends on the '
        "directive being explicit."
    )
    return "\n".join(lines)


def _section_runtime_failures(cycle: Cycle, *, round_num: int = 0, **_: object) -> str:
    rfs = [rf.to_dict() for rf in cycle.opt_sp.runtime_failures]
    if not rfs:
        return ""
    rfs_new = [rf for rf in rfs if rf.get("first_seen_round", 0) == round_num]
    rfs_acc = [rf for rf in rfs if rf.get("first_seen_round", 0) != round_num]
    lines = [
        "RUNTIME FAILURES — L2 SELF-HEALING EVIDENCE",
        "  (candidates ran but produced high warning rates; L2 must adjust "
        "its own strategy — directive, task_context, optimizer_params — to "
        "steer L1 away from the failing config region)",
    ]
    if rfs_new:
        lines.append("")
        lines.append("NEW (this round):")
        for rf in rfs_new:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    if rfs_acc:
        lines.append("")
        lines.append(
            f"ACCUMULATED (surviving from earlier rounds, {len(rfs_acc)} patterns — "
            "L2's prior strategy adjustments did NOT reduce these):"
        )
        for rf in rfs_acc:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    lines.append("")
    lines.append(
        "  ↳ Required L2 action: this is L2 self-healing, not L1 correction. "
        "Update your OWN outputs — tighten the directive to name the failing "
        "config range, refine task_context with the discovered constraint, or "
        "adjust optimizer_params (creativity, n_variants) to narrow L1's search "
        "around the safe region. Do NOT just parrot 'don't use X' to L1 — "
        'restructure the search. Example directive: "Reasoning models on this '
        "task need max_tokens ≥ 2000 to emit a final answer; propose variants "
        'only within that range." '
        "If ACCUMULATED entries persist across multiple L2 attempts, L3 will "
        "replan the pipeline itself next."
    )
    return "\n".join(lines)


def _section_axes_l2(cycle: Cycle, **_: object) -> str:
    if cycle.axes is None:
        return ""
    digest = cycle.axes.digest_for_l2()
    return (
        format_axis_digest_block(digest, _L2_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if digest
        else ""
    )


def _section_axes_l3(cycle: Cycle, **_: object) -> str:
    if cycle.axes is None:
        return ""
    digest = cycle.axes.digest_for_l3()
    return (
        format_axis_digest_block(digest, _L3_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if digest
        else ""
    )


# ---------------------------------------------------------------------------
# L1_CRITIQUE section renderers (moved from critique.py).
# Cross-cutting facts (anomalies, near-miss queries) come via _CritiqueContext.
# ---------------------------------------------------------------------------


def _section_l1c_scoring_summary(
    cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    round_num: int = 0,
    ctx: _CritiqueContext | None = None,
    **_: object,
) -> str:
    if scoring_result is None or ctx is None:
        return ""
    n_results = len(scoring_result.winner_results)
    lines = [
        "## SCORING SUMMARY",
        f"Accuracy: {scoring_result.winner_accuracy:.1%} | "
        f"Composite: {scoring_result.winner_composite:.4f} | "
        f"Degraded: {scoring_result.degraded_queries}/{n_results}",
        f"Round {round_num} | L1 stall count: {cycle.escalation.l1_stall_count} | "
        f"Best so far: {cycle.best_accuracy:.1%} (round {cycle.best_round})",
    ]
    if ctx.prompt_chars:
        bloat = (
            " — prompt is bloated; favour compression in priority_fix"
            if ctx.prompt_chars > _PROMPT_BLOAT_CHARS
            else ""
        )
        lines.append(f"Current prompt size: {ctx.prompt_chars} chars{bloat}")
    return "\n".join(lines)


def _section_l1c_anomaly_flags(
    _cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    ctx: _CritiqueContext | None = None,
    **_: object,
) -> str:
    if scoring_result is None or ctx is None:
        return ""
    if not scoring_result.winner_results or not ctx.anomalies:
        return ""
    return "## ANOMALY FLAGS ({})\n{}".format(
        len(ctx.anomalies), "\n".join(f"  {a}" for a in ctx.anomalies)
    )


def _section_l1c_pipeline_health(
    _cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    **_: object,
) -> str:
    if scoring_result is None:
        return ""
    results = scoring_result.winner_results
    total = len(results)
    if not total:
        return ""
    web_warning_count = 0
    termination: Counter[str] = Counter()
    error_count = 0
    for r in results:
        pd = r.get("pipeline_data") or {}
        diag = pd.get("diagnostics") or {}
        if diag.get("warnings"):
            web_warning_count += 1
        termination[pd.get("terminated_at", "unknown")] += 1
        if is_error_result(r):
            error_count += 1
    lines = ["## PIPELINE HEALTH"]
    if termination:
        lines.append("Termination distribution:")
        for step, count in termination.most_common():
            lines.append(f"  {step}: {count}/{total}")
    lines.append(f"Step degradation: {web_warning_count / total:.0%} of queries")
    lines.append(f"Error rate: {error_count / total:.0%}")
    return "\n".join(lines)


def _section_l1c_runtime_failures(
    _cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    **_: object,
) -> str:
    if scoring_result is None:
        return ""
    failures = [
        {
            "candidate_desc": (cs.get("changes_description") or cs.get("candidate_id", ""))[:60],
            **rf,
        }
        for cs in scoring_result.candidate_scores
        for rf in cs["runtime_failures"]
    ]
    if not failures:
        return ""
    by_warning: dict[str, list[dict]] = {}
    for e in failures:
        by_warning.setdefault(str(e.get("dominant_warning", "unknown")), []).append(e)
    lines = ["## RUNTIME FAILURES THIS ROUND (Rail 2 — treat as hard constraints)"]
    for dom in sorted(by_warning):
        lines.append(f"  {dom}:")
        for e in by_warning[dom]:
            rate = float(e.get("degraded_rate", 0.0)) * 100
            dc = e.get("degraded_count", 0)
            tot = e.get("total_scored", 0)
            cfg = e.get("observed_config") or {}
            cfg_bits = ", ".join(f"{k}={cfg[k]}" for k in _RF_CFG_AXES if k in cfg)
            lines.append(
                f"    {e.get('candidate_desc', '?')}: {rate:.0f}% ({dc}/{tot}) @ {cfg_bits}"
            )
    lines.append(
        "  These configurations are broken — do NOT propose the same or similar values next round."
    )
    return "\n".join(lines)


def _section_l1c_rank_analysis(
    _cycle: Cycle,
    *,
    ctx: _CritiqueContext | None = None,
    **_: object,
) -> str:
    return ctx.rank_text if ctx else ""


def _section_l1c_round_evolution(
    _cycle: Cycle,
    *,
    ctx: _CritiqueContext | None = None,
    **_: object,
) -> str:
    return ctx.evolution_text if ctx else ""


def _section_l1c_query_categories(
    _cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    **_: object,
) -> str:
    if scoring_result is None:
        return ""
    step_counts: Counter[str] = Counter()
    for r in scoring_result.winner_results:
        if r.get("hit") or is_error_result(r):
            continue
        pd = r.get("pipeline_data") or {}
        step_counts[pd.get("terminated_at", "unknown")] += 1
    if not step_counts:
        return ""
    lines = ["## QUERY CATEGORIES", "Failures by termination step:"]
    for step, count in step_counts.most_common():
        lines.append(f"  {step}: {count}")
    return "\n".join(lines)


def _section_l1c_failure_details(
    _cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    pipeline_schema: PipelineSchema | None = None,
    ctx: _CritiqueContext | None = None,
    **_: object,
) -> str:
    if scoring_result is None or ctx is None:
        return ""
    keys = ctx.candidate_keys or None
    failures = [
        r
        for r in scoring_result.winner_results
        if not r.get("hit") and not is_error_result(r) and r.get("query", "") not in ctx.nm_queries
    ]
    if not failures:
        return ""
    lines = [f"## FAILURE DETAILS ({len(failures)} non-near-miss failures)"]
    for r in failures[:8]:
        pd = r.get("pipeline_data") or {}
        gt = r.get("ground_truth", "?")
        rank = find_rank(get_candidates(r, keys), gt)
        rank_str = f"rank {rank}" if rank else "not in candidates"
        diag = pd.get("diagnostics") or {}
        warn = "degraded" if diag.get("warnings") else ""
        diag_str = ""
        if pipeline_schema:
            sd = extract_sample_diagnostics(r, pipeline_schema)
            sig_parts = [
                f"{k}={sd[k]}" for k in ("gt_in_source", "gt_in_ranked", "terminated_at") if k in sd
            ]
            if sig_parts:
                diag_str = " | " + ", ".join(sig_parts)
        lines.append(
            f"  MISS  [{pd.get('terminated_at', '?')}]  {r['query'][:70]}\n"
            f"        -> {r.get('predicted', '?')[:70]}\n"
            f"        GT: {gt[:70]}  |  {rank_str}  {warn}{diag_str}"
        )
    return "\n".join(lines)


def _section_l1c_successes(
    _cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    **_: object,
) -> str:
    if scoring_result is None:
        return ""
    successes = [r for r in scoring_result.winner_results if r.get("hit")]
    if not successes:
        return ""
    lines = [f"## SUCCESSES ({len(successes)} queries)"]
    for r in successes[:2]:
        pd = r.get("pipeline_data") or {}
        lines.append(
            f"  HIT  [{pd.get('terminated_at', '?')}]  "
            f"{r['query'][:70]} → {r.get('predicted', '?')[:70]}"
        )
    return "\n".join(lines)


def _section_l1c_historical_context(cycle: Cycle, **_: object) -> str:
    if cycle.axes is None:
        return ""
    digest = cycle.axes.digest_for_l1_critique()
    return (
        format_axis_digest_block(digest, _L1C_AXIS_LABELS, header="## HISTORICAL CONTEXT")
        if digest
        else ""
    )


def _section_l1c_this_round(
    cycle: Cycle,
    *,
    scoring_result: L1ScoringResult | None = None,
    **_: object,
) -> str:
    if scoring_result is None:
        return ""
    parts: list[str] = []
    diff = build_cross_candidate_diff(
        cast(list[dict], scoring_result.winner_results),
        cast("dict[str, list[dict]]", scoring_result.all_candidate_results),
        scoring_result.candidate_scores,
    )
    trajectory = build_trajectory_report(cycle.rounds)
    if trajectory and trajectory.classification != "healthy":
        parts.append(f"  [TRAJECTORY] {trajectory.classification}: {trajectory.description}")
    if diff:
        parts.append(f"  MISSED OPPORTUNITIES:\n{diff}")
    return "## THIS ROUND\n" + "\n".join(parts) if parts else ""


def _section_l1c_available_schema_mutations(
    _cycle: Cycle,
    *,
    pipeline_schema: PipelineSchema | None = None,
    **_: object,
) -> str:
    if not pipeline_schema:
        return ""
    cap_lines = [
        f"  {node.name} has mutable output_schema"
        f" (current fields: {', '.join(node.output_schema.fields)})"
        for node in pipeline_schema.nodes
        if node.output_schema and node.output_schema.fields and "output_schema" in node.param_keys
    ]
    if not cap_lines:
        return ""
    return (
        "## AVAILABLE SCHEMA MUTATIONS\n"
        + "\n".join(cap_lines)
        + "\n  Use output_schema param with +/-/~ mutation tuples"
        " to add/remove/replace fields."
    )


# ---------------------------------------------------------------------------
# Per-layer order — drives both registration filter and output sequence.
# ---------------------------------------------------------------------------


_SECTIONS: dict[str, Callable[..., str]] = {
    # Shared / L1_GENERATE / L2 / L3 sections
    "pipeline_schema_text": _section_pipeline_schema_text,
    "failure_analysis": _section_failure_analysis,
    "axes_l1": _section_axes_l1,
    "task_context": _section_task_context,
    "escalation_probe": _section_escalation_probe,
    "escalation_alert": _section_escalation_alert,
    "l2_directive": _section_l2_directive,
    "l1_critique_text": _section_l1_critique_text,
    "plan": _section_plan,
    "escalation_section": _section_escalation_section,
    "warning_inventory": _section_warning_inventory,
    "validation_failures": _section_validation_failures,
    "runtime_failures": _section_runtime_failures,
    "axes_l2": _section_axes_l2,
    "axes_l3": _section_axes_l3,
    # L1_CRITIQUE sections
    "l1c_scoring_summary": _section_l1c_scoring_summary,
    "l1c_anomaly_flags": _section_l1c_anomaly_flags,
    "l1c_pipeline_health": _section_l1c_pipeline_health,
    "l1c_runtime_failures": _section_l1c_runtime_failures,
    "l1c_rank_analysis": _section_l1c_rank_analysis,
    "l1c_round_evolution": _section_l1c_round_evolution,
    "l1c_query_categories": _section_l1c_query_categories,
    "l1c_failure_details": _section_l1c_failure_details,
    "l1c_successes": _section_l1c_successes,
    "l1c_historical_context": _section_l1c_historical_context,
    "l1c_this_round": _section_l1c_this_round,
    "l1c_available_schema_mutations": _section_l1c_available_schema_mutations,
}


LAYER_ORDER: dict[Layer, tuple[str, ...]] = {
    Layer.L1_GENERATE: (
        "pipeline_schema_text",
        "failure_analysis",
        "axes_l1",
        "task_context",
        "escalation_probe",
        "escalation_alert",
        "l2_directive",
        "l1_critique_text",
        "plan",
    ),
    Layer.L1_CRITIQUE: (
        "l1c_scoring_summary",
        "l1c_anomaly_flags",
        "l1c_pipeline_health",
        "l1c_runtime_failures",
        "l1c_rank_analysis",
        "l1c_round_evolution",
        "l1c_query_categories",
        "l1c_failure_details",
        "l1c_successes",
        "l1c_historical_context",
        "l1c_this_round",
        "l1c_available_schema_mutations",
    ),
    Layer.L2: (
        "escalation_section",
        "warning_inventory",
        "l1_critique_text",
        "l2_directive",
        "validation_failures",
        "runtime_failures",
        "axes_l2",
    ),
    Layer.L3: ("axes_l3",),
}


def assemble_dispatch_msg(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    scoring_result: L1ScoringResult | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Walk the registry for *layer*, render each section, drop empties, join.

    Reads persistent state from *cycle* (``cycle.opt_sp``, ``cycle.axes``,
    ``cycle.probe_next_round``, ``cycle.session.pipeline_schema``).
    Transient per-call inputs ride along as kwargs.

    For ``Layer.L1_CRITIQUE``, a pre-pass computes a ``_CritiqueContext``
    holding cross-cutting facts (near-miss query set, anomalies,
    pre-rendered rank/evolution text) — sections then stay pure.

    On L1_GENERATE the L2 directive supersedes the L1 critique whenever
    both are populated (the directive is L2's digested view of the
    critique — sliding window of 1). Returns ``""`` when no section
    produces content.
    """
    ctx: _CritiqueContext | None = None
    if layer is Layer.L1_CRITIQUE and scoring_result is not None:
        ctx = _compute_critique_context(cycle, scoring_result, pipeline_schema)

    kwargs: dict[str, object] = {
        "round_num": round_num,
        "pipeline_schema_text": pipeline_schema_text,
        "candidate_scores": candidate_scores,
        "escalation_check_result": escalation_check_result,
        "pipeline_params": pipeline_params,
        "scoring_result": scoring_result,
        "pipeline_schema": pipeline_schema,
        "ctx": ctx,
        "layer": layer,
    }
    sections: dict[str, str] = {}
    for name in LAYER_ORDER[layer]:
        text = _SECTIONS[name](cycle, **kwargs)
        if text:
            sections[name] = text

    # On L1_GENERATE the L2 directive replaces the critique whenever both are present.
    if layer is Layer.L1_GENERATE and "l2_directive" in sections:
        sections.pop("l1_critique_text", None)

    return "\n\n".join(sections[name] for name in LAYER_ORDER[layer] if name in sections)


__all__ = [
    "LAYER_ORDER",
    "Layer",
    "assemble_dispatch_msg",
]
