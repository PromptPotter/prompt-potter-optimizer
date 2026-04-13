"""Pure stat-section builders for the critique prompt.

No I/O, no LLM calls. Each section helper walks a round's results and
renders a markdown-style block; ``assemble_critique_sections()`` orchestrates
them and returns the full pipeline-aware stats bundle that feeds the
critique LLM call in ``critique.py``.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from promptpotter.application.scoring.metrics import (
    extract_sample_diagnostics,
    find_rank,
)
from promptpotter.shared.errors import is_error_result

from .critique import get_candidates

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult

    from .critique import RoundSnapshot

__all__ = ["assemble_critique_sections"]


def _summary_section(ctx: RoundSnapshot) -> str:
    total = len(ctx.results)
    return (
        f"## SCORING SUMMARY\n"
        f"Accuracy: {ctx.accuracy:.1%} | Composite: {ctx.composite:.4f} | "
        f"Degraded: {ctx.degraded_queries}/{total}\n"
        f"Round {ctx.current_round} | Stall count: {ctx.stall_count} | "
        f"Best so far: {ctx.best_accuracy:.1%} (round {ctx.best_round})"
    )


def _pipeline_health_section(
    results: list[QueryResult],
    anomalies: list[str],
    *,
    degradation_threshold: float = 0.4,
) -> str:
    """Termination distribution, degradation rate, timing, error rate."""
    total = len(results)
    if total == 0:
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

    deg_rate = web_warning_count / total
    lines = ["## PIPELINE HEALTH"]
    if termination:
        lines.append("Termination distribution:")
        for step, count in termination.most_common():
            lines.append(f"  {step}: {count}/{total}")
    lines.append(f"Step degradation: {deg_rate:.0%} of queries")
    lines.append(f"Error rate: {error_count / total:.0%}")

    if deg_rate > degradation_threshold:
        anomalies.append(
            f"[HIGH] high_degradation: {web_warning_count}/{total} queries had degraded steps."
        )

    return "\n".join(lines)


def _rank_analysis_section(
    results: list[QueryResult],
    anomalies: list[str],
    candidate_keys: list[str] | None = None,
    *,
    near_miss_ratio: float = 0.3,
) -> tuple[str, set[str]]:
    """Rank buckets, top-k recall, and near-miss patterns.

    Returns ``(section_text, near_miss_queries)`` so failure_details can
    skip queries already shown here.
    """
    rank_map: dict[int, int | None] = {}
    for i, r in enumerate(results):
        if not is_error_result(r):
            rank_map[i] = find_rank(get_candidates(r, candidate_keys), r.get("ground_truth", ""))

    rank_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "not_found": 0}
    near_misses: list[dict] = []
    for i, r in enumerate(results):
        if is_error_result(r):
            continue
        rank = rank_map.get(i)
        if rank == 1:
            rank_buckets["1"] += 1
        elif rank is not None and rank <= 5:
            rank_buckets["2-5"] += 1
            near_misses.append(
                {
                    "query": r["query"][:80],
                    "ground_truth": r.get("ground_truth", "")[:60],
                    "rank": rank,
                    "predicted": r.get("predicted", "?")[:60],
                }
            )
        elif rank is not None and rank <= 10:
            rank_buckets["6-10"] += 1
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
    if n_valid == 0:
        return "", nm_queries

    lines = ["## CANDIDATE RANK ANALYSIS"]
    lines.append("Where does ground truth appear in candidate list?")
    for bucket, count in rank_buckets.items():
        lines.append(f"  Rank {bucket}: {count}")

    for k in [1, 3, 5, 10]:
        in_top_k = sum(1 for i, rank in rank_map.items() if rank is not None and rank <= k)
        lines.append(f"  top-{k}: {in_top_k / n_valid:.0%}")

    if near_misses:
        lines.append(f"\nNear misses ({len(near_misses)} — GT in candidates but not rank 1):")
        for nm in near_misses[:15]:
            lines.append(
                f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                f"(GT: {nm['ground_truth']})"
            )

    misses = [r for r in results if not r.get("hit") and not is_error_result(r)]
    if misses and len(near_misses) / max(len(misses), 1) > near_miss_ratio:
        anomalies.append(
            f"[MEDIUM] near_miss_pattern: GT in candidates for "
            f"{len(near_misses)}/{len(misses)} misses but not ranked first."
        )

    return "\n".join(lines), nm_queries


def _round_evolution_section(
    round_history: list[dict],
    anomalies: list[str],
) -> str:
    """Accuracy trajectory and inter-round param changes."""
    if not round_history:
        return ""

    lines = ["## ROUND EVOLUTION"]
    lines.append("Round  Accuracy  Delta   Degraded  Candidates")
    prev_acc = None
    plateau_count = 0
    for rh in round_history:
        acc = rh.get("accuracy", 0.0)
        delta = acc - prev_acc if prev_acc is not None else 0.0
        lines.append(
            f"  {rh.get('round', '?'):>5}  {acc:>7.1%}  {delta:>+6.1%}  "
            f"{rh.get('degraded', 0):>8}  {rh.get('n_candidates', 0):>10}"
        )
        plateau_count = plateau_count + 1 if abs(delta) < 0.01 else 0
        prev_acc = acc

    for i in range(1, len(round_history)):
        prev_pp = round_history[i - 1].get("pipeline_params") or {}
        curr_pp = round_history[i].get("pipeline_params") or {}
        changed = {
            k
            for k in set(prev_pp) | set(curr_pp)
            if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
        }
        if changed:
            lines.append(
                f"  Round {round_history[i - 1].get('round')}→"
                f"{round_history[i].get('round')}: "
                f"{', '.join(sorted(changed))}"
            )

    if plateau_count >= 2:
        anomalies.append(
            f"[MEDIUM] plateau_signal: {plateau_count} consecutive rounds with <1% improvement."
        )

    return "\n".join(lines)


def _query_category_section(results: list[QueryResult]) -> str:
    """Failures grouped by termination step (counts only)."""
    step_counts: Counter[str] = Counter()
    for r in results:
        if r.get("hit") or is_error_result(r):
            continue
        pd = r.get("pipeline_data") or {}
        step_counts[pd.get("terminated_at", "unknown")] += 1

    if not step_counts:
        return ""

    lines = ["## QUERY CATEGORIES"]
    lines.append("Failures by termination step:")
    for step, count in step_counts.most_common():
        lines.append(f"  {step}: {count}")
    return "\n".join(lines)


def _failure_details_section(
    results: list[QueryResult],
    candidate_keys: list[str] | None = None,
    near_miss_queries: set[str] | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Per-query failure breakdown; skips queries already shown as near misses."""
    failures = [r for r in results if not r.get("hit") and not is_error_result(r)]
    if near_miss_queries:
        failures = [r for r in failures if r.get("query", "") not in near_miss_queries]
    if not failures:
        return ""

    lines = [f"## FAILURE DETAILS ({len(failures)} non-near-miss failures)"]
    for r in failures[:8]:
        pd = r.get("pipeline_data") or {}
        gt = r.get("ground_truth", "?")
        rank = find_rank(get_candidates(r, candidate_keys), gt)
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


def _success_details_section(results: list[QueryResult]) -> str:
    """Per-query success count + brief samples."""
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


def assemble_critique_sections(ctx: RoundSnapshot) -> str:
    """Build stat-rich sections for the critique template.

    Each section is computed by a dedicated helper; this function orchestrates
    the assembly order and inserts anomaly flags after the summary.
    The task/output instructions live in the ``critique.json`` template.
    """
    results = ctx.results
    anomalies: list[str] = []

    rank_text, nm_queries = _rank_analysis_section(
        results,
        anomalies,
        candidate_keys=ctx.candidate_keys or None,
        near_miss_ratio=ctx.near_miss_ratio,
    )

    sections = [
        _summary_section(ctx),
        _pipeline_health_section(
            results,
            anomalies,
            degradation_threshold=ctx.degradation_threshold,
        ),
        rank_text,
        _round_evolution_section(ctx.round_history, anomalies),
        _query_category_section(results),
        _failure_details_section(
            results,
            candidate_keys=ctx.candidate_keys or None,
            near_miss_queries=nm_queries,
            pipeline_schema=ctx.pipeline_schema,
        ),
        _success_details_section(results),
    ]

    if results and anomalies:
        anomaly_block = "## ANOMALY FLAGS ({})\n{}".format(
            len(anomalies),
            "\n".join(f"  {a}" for a in anomalies),
        )
        sections.insert(1, anomaly_block)

    sm_digest = ctx.search_memory_digest
    if sm_digest:
        sm_lines = ["## HISTORICAL INTELLIGENCE"]
        if sm_digest.get("discriminating_queries"):
            sm_lines.append(f"  Discriminating queries: {sm_digest['discriminating_queries']}")
        if sm_digest.get("failure_clusters"):
            sm_lines.append(f"  Failure clusters: {sm_digest['failure_clusters']}")
        if sm_digest.get("tractability"):
            sm_lines.append(f"  Query tractability: {sm_digest['tractability']}")
        if sm_digest.get("exhausted_axes"):
            sm_lines.append(
                f"  Exhausted axes (DO NOT suggest these): {sm_digest['exhausted_axes']}"
            )
        if sm_digest.get("value_trends"):
            sm_lines.append(f"  Value trends: {sm_digest['value_trends']}")
        if sm_digest.get("trajectory"):
            sm_lines.append(f"  [TRAJECTORY] {sm_digest['trajectory']}")
        if sm_digest.get("improvement_attribution"):
            sm_lines.append(f"  WHAT WORKED:\n{sm_digest['improvement_attribution']}")
        if sm_digest.get("cross_candidate_diff"):
            sm_lines.append(f"  MISSED OPPORTUNITIES:\n{sm_digest['cross_candidate_diff']}")
        sections.append("\n".join(sm_lines))

    if ctx.pipeline_schema:
        cap_lines: list[str] = []
        for node in ctx.pipeline_schema.nodes:
            if (
                node.output_schema
                and node.output_schema.fields
                and "output_schema" in node.param_keys
            ):
                cap_lines.append(
                    f"  {node.name} has mutable output_schema"
                    f" (current fields: {', '.join(node.output_schema.fields)})"
                )
        if cap_lines:
            sections.append(
                "## AVAILABLE SCHEMA MUTATIONS\n"
                + "\n".join(cap_lines)
                + "\n  Use output_schema param with +/-/~ mutation tuples"
                " to add/remove/replace fields."
            )

    return "\n\n".join(s for s in sections if s)
