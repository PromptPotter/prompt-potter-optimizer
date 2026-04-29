"""L1_CRITIQUE section renderers + critique-context computation.

The L1-critique dispatch_msg has its own family of sections that the
other layers (L1_GENERATE, L2, L3) never touch. They share a per-call
:class:`CritiqueContext` populated up front by
:func:`compile_critique_context` so each section can stay a pure
``(ctx: LayerContext) -> str`` consumer.

The orchestrator in ``dispatch_msg_registry.py`` imports
:data:`L1C_SECTIONS` (name → renderer) and :data:`L1C_SECTION_ORDER`
(emit sequence) and merges them into the layer registry.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.elimination import (
    candidate_keys_from_schema,
    get_candidates,
)
from promptpotter.application.optimization.nodes.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
    format_axis_digest_block,
)
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.nodes.dispatch_msg_registry import LayerContext
    from promptpotter.application.optimization.nodes.l1_score import L1ScoringResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult


__all__ = [
    "L1C_SECTIONS",
    "L1C_SECTION_ORDER",
    "CritiqueContext",
    "compile_critique_context",
]


_PROMPT_BLOAT_CHARS = 3000
_RF_CFG_AXES = ("model", "temperature", "max_tokens", "reasoning_effort")

_L1C_AXIS_LABELS: dict[str, str] = {
    "discriminating_queries": "Discriminating queries",
    "failure_clusters": "Failure clusters",
    "tractability": "Query tractability",
    "exhausted_axes": "Exhausted axes (DO NOT suggest these)",
    "value_trends": "Value trends",
    "improvement_attribution": "WHAT WORKED",
}


@dataclass
class CritiqueContext:
    """L1_CRITIQUE pre-pass — cross-cutting facts computed once per call."""

    prompt_chars: int = 0
    candidate_keys: list[str] | None = None
    nm_queries: set[str] = field(default_factory=set)
    anomalies: list[str] = field(default_factory=list)
    rank_text: str = ""
    evolution_text: str = ""


def compile_critique_context(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
) -> CritiqueContext:
    candidate_keys = candidate_keys_from_schema(schema)
    prompt_chars = len(cycle.opt_sp.render())
    rank_text, nm_queries = _compute_rank_analysis(scoring_result.winner_results, candidate_keys)
    evolution_text, anomalies = _compute_round_evolution(cycle)
    return CritiqueContext(
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
# Cross-cutting facts (anomalies, near-miss queries) come via ``ctx.critique``.
# ---------------------------------------------------------------------------


def _section_l1c_scoring_summary(ctx: LayerContext) -> str:
    if ctx.scoring_result is None or ctx.critique is None:
        return ""
    sr = ctx.scoring_result
    cr = ctx.critique
    cycle = ctx.cycle
    n_results = len(sr.winner_results)
    lines = [
        "## SCORING SUMMARY",
        f"Accuracy: {sr.winner_accuracy:.1%} | "
        f"Composite: {sr.winner_composite:.4f} | "
        f"Degraded: {sr.degraded_queries}/{n_results}",
        f"Round {ctx.round_num} | L1 stall count: {cycle.escalation.l1_stall_count} | "
        f"Best so far: {cycle.best_accuracy:.1%} (round {cycle.best_round})",
    ]
    if cr.prompt_chars:
        bloat = (
            " — prompt is bloated; favour compression in priority_fix"
            if cr.prompt_chars > _PROMPT_BLOAT_CHARS
            else ""
        )
        lines.append(f"Current prompt size: {cr.prompt_chars} chars{bloat}")
    return "\n".join(lines)


def _section_l1c_anomaly_flags(ctx: LayerContext) -> str:
    if ctx.scoring_result is None or ctx.critique is None:
        return ""
    if not ctx.scoring_result.winner_results or not ctx.critique.anomalies:
        return ""
    return "## ANOMALY FLAGS ({})\n{}".format(
        len(ctx.critique.anomalies),
        "\n".join(f"  {a}" for a in ctx.critique.anomalies),
    )


def _section_l1c_pipeline_health(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    results = ctx.scoring_result.winner_results
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


def _section_l1c_runtime_failures(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    failures = [
        {
            "candidate_desc": (cs.changes_description or cs.candidate_id)[:60],
            **rf,
        }
        for cs in ctx.scoring_result.candidate_scores
        for rf in cs.runtime_failures
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


def _section_l1c_rank_analysis(ctx: LayerContext) -> str:
    return ctx.critique.rank_text if ctx.critique else ""


def _section_l1c_round_evolution(ctx: LayerContext) -> str:
    return ctx.critique.evolution_text if ctx.critique else ""


def _section_l1c_query_categories(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    step_counts: Counter[str] = Counter()
    for r in ctx.scoring_result.winner_results:
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


def _section_l1c_failure_details(ctx: LayerContext) -> str:
    if ctx.scoring_result is None or ctx.critique is None:
        return ""
    keys = ctx.critique.candidate_keys or None
    failures = [
        r
        for r in ctx.scoring_result.winner_results
        if not r.get("hit")
        and not is_error_result(r)
        and r.get("query", "") not in ctx.critique.nm_queries
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
        if ctx.pipeline_schema:
            sd = extract_sample_diagnostics(r, ctx.pipeline_schema)
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


def _section_l1c_successes(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    successes = [r for r in ctx.scoring_result.winner_results if r.get("hit")]
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


def _section_l1c_historical_context(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L1C_AXIS_LABELS, header="## HISTORICAL CONTEXT")
        if ctx.axis_digest
        else ""
    )


def _section_l1c_this_round(ctx: LayerContext) -> str:
    if ctx.scoring_result is None:
        return ""
    parts: list[str] = []
    sr = ctx.scoring_result
    diff = build_cross_candidate_diff(
        cast(list[dict], sr.winner_results),
        cast("dict[str, list[dict]]", sr.all_candidate_results),
        [cs.to_dict() for cs in sr.candidate_scores],
    )
    trajectory = build_trajectory_report(ctx.cycle.rounds)
    if trajectory and trajectory.classification != "healthy":
        parts.append(f"  [TRAJECTORY] {trajectory.classification}: {trajectory.description}")
    if diff:
        parts.append(f"  MISSED OPPORTUNITIES:\n{diff}")
    return "## THIS ROUND\n" + "\n".join(parts) if parts else ""


def _section_l1c_available_schema_mutations(ctx: LayerContext) -> str:
    if not ctx.pipeline_schema:
        return ""
    cap_lines = [
        f"  {node.name} has mutable output_schema"
        f" (current fields: {', '.join(node.output_schema.fields)})"
        for node in ctx.pipeline_schema.nodes
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


L1C_SECTIONS: dict[str, Callable[[LayerContext], str]] = {
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


L1C_SECTION_ORDER: tuple[str, ...] = tuple(L1C_SECTIONS.keys())
