"""L1 critique phase — LLM analysis of a round's results."""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.nodes.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
    format_axis_digest_block,
)
from promptpotter.application.optimization.pipeline import run_optimizer_node
from promptpotter.application.optimization.utils import (
    candidate_keys_from_schema,
    get_candidates,
)
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult
    from promptpotter.infrastructure.llm.client import LLMClientBase
    from promptpotter.infrastructure.persistence.round_recorder import RoundRecorder

    from .score import L1ScoringResult

logger = logging.getLogger(__name__)

__all__ = [
    "format_l1_critique_for_prompt",
    "run_l1_critique",
]

_PROMPT_BLOAT_CHARS = 3000
_RF_CFG_AXES = ("model", "temperature", "max_tokens", "reasoning_effort")

_CRITIQUE_AXIS_LABELS = {
    "discriminating_queries": "Discriminating queries",
    "failure_clusters": "Failure clusters",
    "tractability": "Query tractability",
    "exhausted_axes": "Exhausted axes (DO NOT suggest these)",
    "value_trends": "Value trends",
    "improvement_attribution": "WHAT WORKED",
}


async def run_l1_critique(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
    llm_client: LLMClientBase,
    *,
    round_num: int,
    axis_digest: dict | None = None,
    model: str | None = None,
    recorder: RoundRecorder | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict."""
    sections = _assemble_l1_critique_sections(
        cycle, scoring_result, schema, round_num=round_num, axis_digest=axis_digest
    )
    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        compile_vars={"inbox": sections},
        llm_client=llm_client,
        model=model,
        recorder=recorder,
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


def _section_scoring_summary(
    scoring_result: L1ScoringResult,
    cycle: Cycle,
    round_num: int,
    prompt_chars: int,
) -> str:
    n_results = len(scoring_result.winner_results)
    lines = [
        "## SCORING SUMMARY",
        f"Accuracy: {scoring_result.winner_accuracy:.1%} | "
        f"Composite: {scoring_result.winner_composite:.4f} | "
        f"Degraded: {scoring_result.degraded_queries}/{n_results}",
        f"Round {round_num} | L1 stall count: {cycle.escalation.l1_stall_count} | "
        f"Best so far: {cycle.best_accuracy:.1%} (round {cycle.best_round})",
    ]
    if prompt_chars:
        bloat = (
            " — prompt is bloated; favour compression in priority_fix"
            if prompt_chars > _PROMPT_BLOAT_CHARS
            else ""
        )
        lines.append(f"Current prompt size: {prompt_chars} chars{bloat}")
    return "\n".join(lines)


def _section_pipeline_health(results: list[QueryResult]) -> str:
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


def _section_runtime_failures(scoring_result: L1ScoringResult) -> str:
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
            tot = e.get("total_evaluated", 0)
            cfg = e.get("observed_config") or {}
            cfg_bits = ", ".join(f"{k}={cfg[k]}" for k in _RF_CFG_AXES if k in cfg)
            lines.append(
                f"    {e.get('candidate_desc', '?')}: {rate:.0f}% ({dc}/{tot}) @ {cfg_bits}"
            )
    lines.append(
        "  These configurations are broken — do NOT propose the same or similar values next round."
    )
    return "\n".join(lines)


def _section_rank_analysis(
    results: list[QueryResult], candidate_keys: list[str] | None
) -> tuple[str, set[str]]:
    """Return (section_text, near_miss_query_set). Failure-details consumes nm_queries."""
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


def _section_round_evolution(cycle: Cycle) -> tuple[str, list[str]]:
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


def _section_query_categories(results: list[QueryResult]) -> str:
    step_counts: Counter[str] = Counter()
    for r in results:
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


def _section_failure_details(
    results: list[QueryResult],
    candidate_keys: list[str] | None,
    schema: PipelineSchema | None,
    nm_queries: set[str],
) -> str:
    keys = candidate_keys or None
    failures = [
        r
        for r in results
        if not r.get("hit") and not is_error_result(r) and r.get("query", "") not in nm_queries
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
        if schema:
            sd = extract_sample_diagnostics(r, schema)
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


def _section_successes(results: list[QueryResult]) -> str:
    successes = [r for r in results if r.get("hit")]
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


def _section_anomaly_flags(anomalies: list[str]) -> str:
    return "## ANOMALY FLAGS ({})\n{}".format(
        len(anomalies), "\n".join(f"  {a}" for a in anomalies)
    )


def _section_this_round(scoring_result: L1ScoringResult, cycle: Cycle) -> str:
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


def _section_available_schema_mutations(schema: PipelineSchema | None) -> str:
    if not schema:
        return ""
    cap_lines = [
        f"  {node.name} has mutable output_schema"
        f" (current fields: {', '.join(node.output_schema.fields)})"
        for node in schema.nodes
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


def _assemble_l1_critique_sections(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
    *,
    round_num: int,
    axis_digest: dict | None,
) -> str:
    """Assemble the L1 critique meta-prompt sections in canonical order."""
    results = scoring_result.winner_results
    candidate_keys = candidate_keys_from_schema(schema)
    prompt_chars = len(cycle.opt_sp.render())

    rank_text, nm_queries = _section_rank_analysis(results, candidate_keys)
    evolution_text, anomalies = _section_round_evolution(cycle)

    sections: list[str] = [
        _section_scoring_summary(scoring_result, cycle, round_num, prompt_chars),
        _section_pipeline_health(results),
        _section_runtime_failures(scoring_result),
        rank_text,
        evolution_text,
        _section_query_categories(results),
        _section_failure_details(results, candidate_keys, schema, nm_queries),
        _section_successes(results),
    ]
    # Anomaly flags slot in just below the summary so the LLM sees them early.
    if results and anomalies:
        sections.insert(1, _section_anomaly_flags(anomalies))

    sections.append(
        format_axis_digest_block(
            axis_digest,
            _CRITIQUE_AXIS_LABELS,
            header="## HISTORICAL INTELLIGENCE",
        )
    )
    sections.append(_section_this_round(scoring_result, cycle))
    sections.append(_section_available_schema_mutations(schema))

    return "\n\n".join(s for s in sections if s)
