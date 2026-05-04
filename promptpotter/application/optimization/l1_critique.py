"""L1-critique blob + LLM call. Single ``{{dispatch_msg}}`` hole — no overrides."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.dispatch import (
    DispatchState,
    Layer,
    build_dispatch_state,
    compile_prompt_vars,
)
from promptpotter.application.optimization.elimination import get_candidates
from promptpotter.application.optimization.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
    format_axis_digest_block,
)
from promptpotter.application.optimization.llm_call import run_optimizer_node
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm import LLMClientBase
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.l1 import L1ScoringResult
    from promptpotter.infrastructure.projections import AuditTrailProjection

logger = logging.getLogger(__name__)

__all__ = [
    "compile_l1_critique_blob",
    "format_l1_critique_for_prompt",
    "run_l1_critique",
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


def _section_l1c_round_report(ctx: DispatchState) -> str:
    """Round-level stats: scoring, anomalies, pipeline health, ranks, evolution, this-round diff."""
    if ctx.scoring_result is None:
        return ""
    sr = ctx.scoring_result
    cr = ctx.critique
    parts: list[str] = []

    # Scoring summary.
    if cr is not None:
        n_results = len(sr.winner_results)
        lines = [
            "## SCORING SUMMARY",
            f"Accuracy: {sr.winner_accuracy:.1%} | "
            f"Composite: {sr.winner_composite:.4f} | "
            f"Degraded: {sr.degraded_queries}/{n_results}",
            f"Round {ctx.round_num} | L1 stall count: {ctx.l1_stall_count} | "
            f"Best so far: {ctx.best_accuracy:.1%} (round {ctx.best_round})",
        ]
        if cr.prompt_chars:
            bloat = (
                " — prompt is bloated; favour compression in priority_fix"
                if cr.prompt_chars > _PROMPT_BLOAT_CHARS
                else ""
            )
            lines.append(f"Current prompt size: {cr.prompt_chars} chars{bloat}")
        parts.append("\n".join(lines))

    # Anomaly flags.
    if cr is not None and sr.winner_results and cr.anomalies:
        parts.append(
            "## ANOMALY FLAGS ({})\n{}".format(
                len(cr.anomalies), "\n".join(f"  {a}" for a in cr.anomalies)
            )
        )

    # Pipeline health.
    results = sr.winner_results
    total = len(results)
    if total:
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
        parts.append("\n".join(lines))

    # Rank analysis + round evolution (precomputed in CritiqueContext).
    if cr is not None and cr.rank_text:
        parts.append(cr.rank_text)
    if cr is not None and cr.evolution_text:
        parts.append(cr.evolution_text)

    # This round (trajectory + cross-candidate diff).
    diff = build_cross_candidate_diff(
        cast(list[dict], sr.winner_results),
        cast("dict[str, list[dict]]", sr.all_candidate_results),
        [cs.to_dict() for cs in sr.candidate_scores],
    )
    trajectory = build_trajectory_report(ctx.rounds)
    this_round_parts: list[str] = []
    if trajectory and trajectory.classification != "healthy":
        this_round_parts.append(
            f"  [TRAJECTORY] {trajectory.classification}: {trajectory.description}"
        )
    if diff:
        this_round_parts.append(f"  MISSED OPPORTUNITIES:\n{diff}")
    if this_round_parts:
        parts.append("## THIS ROUND\n" + "\n".join(this_round_parts))

    return "\n\n".join(parts)


def _section_l1c_per_query_report(ctx: DispatchState) -> str:
    """Per-query views: runtime failures, query categories, failure details, successes."""
    if ctx.scoring_result is None:
        return ""
    sr = ctx.scoring_result
    cr = ctx.critique
    parts: list[str] = []

    # Runtime failures (steer-away regions).
    rt_failures = [
        {"candidate_desc": (cs.changes_description or cs.candidate_id)[:60], **rf}
        for cs in sr.candidate_scores
        for rf in cs.runtime_failures
    ]
    if rt_failures:
        by_warning: dict[str, list[dict]] = {}
        for e in rt_failures:
            by_warning.setdefault(str(e.get("dominant_warning", "unknown")), []).append(e)
        lines = ["## RUNTIME FAILURES THIS ROUND (steer away from these regions)"]
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
            "  These configurations degraded — shift away from the same or similar regions."
        )
        parts.append("\n".join(lines))

    # Query categories — failures grouped by termination step.
    step_counts: Counter[str] = Counter()
    for r in sr.winner_results:
        if r.get("hit") or is_error_result(r):
            continue
        pd = r.get("pipeline_data") or {}
        step_counts[pd.get("terminated_at", "unknown")] += 1
    if step_counts:
        lines = ["## QUERY CATEGORIES", "Failures by termination step:"]
        for step, count in step_counts.most_common():
            lines.append(f"  {step}: {count}")
        parts.append("\n".join(lines))

    # Failure details — non-near-miss misses.
    if cr is not None:
        keys = cr.candidate_keys or None
        failures = [
            r
            for r in sr.winner_results
            if not r.get("hit")
            and not is_error_result(r)
            and r.get("query", "") not in cr.nm_queries
        ]
        if failures:
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
                        f"{k}={sd[k]}"
                        for k in ("gt_in_source", "gt_in_ranked", "terminated_at")
                        if k in sd
                    ]
                    if sig_parts:
                        diag_str = " | " + ", ".join(sig_parts)
                lines.append(
                    f"  MISS  [{pd.get('terminated_at', '?')}]  {r['query'][:70]}\n"
                    f"        -> {r.get('predicted', '?')[:70]}\n"
                    f"        GT: {gt[:70]}  |  {rank_str}  {warn}{diag_str}"
                )
            parts.append("\n".join(lines))

    # Successes — top 2 hits.
    successes = [r for r in sr.winner_results if r.get("hit")]
    if successes:
        lines = [f"## SUCCESSES ({len(successes)} queries)"]
        for r in successes[:2]:
            pd = r.get("pipeline_data") or {}
            lines.append(
                f"  HIT  [{pd.get('terminated_at', '?')}]  "
                f"{r['query'][:70]} → {r.get('predicted', '?')[:70]}"
            )
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


def _section_l1c_historical_context(ctx: DispatchState) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L1C_AXIS_LABELS, header="## HISTORICAL CONTEXT")
        if ctx.axis_digest
        else ""
    )


def _section_l1c_available_schema_mutations(ctx: DispatchState) -> str:
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


_L1C_SECTIONS: tuple[Callable[[DispatchState], str], ...] = (
    _section_l1c_round_report,
    _section_l1c_per_query_report,
    _section_l1c_historical_context,
    _section_l1c_available_schema_mutations,
)


def compile_l1_critique_blob(state: DispatchState) -> str:
    """Walk L1-critique sections, drop empties, join with blank lines.

    L1-critique has no per-section override channel (nothing external
    mutates its surface), so its prompt template carries a single
    ``{{dispatch_msg}}`` hole that this blob fills. The four section
    renderers below are walked in registration order; empties drop out.
    """
    return "\n\n".join(text for fn in _L1C_SECTIONS if (text := fn(state)))


async def run_l1_critique(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
    llm_client: LLMClientBase,
    *,
    round_num: int,
    model: str | None = None,
    recorder: AuditTrailProjection | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict."""
    state = build_dispatch_state(
        Layer.L1_CRITIQUE,
        cycle,
        round_num=round_num,
        pipeline_schema=schema,
        scoring_result=scoring_result,
    )
    compile_vars = compile_prompt_vars(
        Layer.L1_CRITIQUE,
        state,
        cycle.opt_sp,
        extras={"dispatch_msg": compile_l1_critique_blob(state)},
    )
    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        compile_vars=compile_vars,
        llm_client=llm_client,
        model=model,
        recorder=recorder,
        cache=cycle.session.store.optimizer_calls,
    )
    logger.info(
        "Rich L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        round_num + 1,
        scoring_result.winner_accuracy,
    )
    return result


def format_l1_critique_for_prompt(critique: dict) -> str:
    """L1 critique dict → compact text for L1/L2 (summary + priority_fix + axes + highlights)."""
    parts = []
    if critique.get("summary"):
        parts.append(critique["summary"])
    if critique.get("priority_fix"):
        parts.append(f"Priority fix: {critique['priority_fix']}")
    if critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(critique['suggested_axes'])}")
    highlights = critique.get("failure_highlights", [])
    if highlights:
        parts.append("Key failures:")
        for h in highlights[:5]:
            parts.append(f"  {h}")
    return "\n".join(parts)
