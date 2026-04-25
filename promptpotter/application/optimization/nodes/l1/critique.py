"""L1 critique phase — LLM analysis of a round's results.

Runs inside the L1 round after scoring+winner selection. Builds a
:class:`RoundSnapshot` from the round's state (defined here), assembles a
stat-rich prompt, calls the LLM, and returns the 6-field critique dict.
Output feeds the next L1 generate phase (via inbox) and L2 refine (on
escalation).
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from promptpotter.application.intelligence import load_variant_library
from promptpotter.application.optimization.nodes.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
    format_search_memory_block,
)
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.application.optimization.utils import (
    candidate_keys_from_schema,
    get_candidates,
)
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult
    from promptpotter.infrastructure.llm.client import LLMClientBase

    from .score import L1ScoringResult

logger = logging.getLogger(__name__)

__all__ = [
    "L1CritiqueAgent",
    "RoundSnapshot",
    "format_l1_critique_for_prompt",
    "sample_thinking_styles",
]


@dataclass
class RoundSnapshot:
    """Bundles current-round results + round history for critique diagnostics."""

    results: list[QueryResult]
    accuracy: float
    composite: float = 0.0
    degraded_queries: int = 0

    round_history: list[dict] = field(default_factory=list)
    current_round: int = 0
    l1_stall_count: int = 0
    best_accuracy: float = 0.0
    best_round: int = -1

    pipeline_params: dict | None = None

    candidate_keys: list[str] = field(default_factory=list)
    pipeline_schema: PipelineSchema | None = None

    degradation_threshold: float = 0.4
    near_miss_ratio: float = 0.3

    search_memory_digest: dict | None = None
    round_analysis: dict[str, str] = field(default_factory=dict)

    runtime_failures: list[dict] = field(default_factory=list)

    # Size (chars) of the current-best prompt — surfaced to the critique so
    # it can flag bloat drift and prompt L1 to compress on the next round.
    current_prompt_chars: int = 0

    @classmethod
    def from_round_state(
        cls,
        cycle: Cycle,
        scoring_result: L1ScoringResult,
        config: CampaignConfig,
        schema: PipelineSchema | None,
        *,
        round_num: int,
        search_memory_digest: dict | None = None,
    ) -> RoundSnapshot:
        """Build snapshot; round-local analysis (trajectory, cross-candidate diff) lives on its own field, not mutated onto the SearchMemory digest."""
        round_analysis: dict[str, str] = {}
        diff = build_cross_candidate_diff(
            cast(list[dict], scoring_result.winner_results),
            cast("dict[str, list[dict]]", scoring_result.all_candidate_results),
            scoring_result.candidate_scores,
        )
        if diff:
            round_analysis["cross_candidate_diff"] = diff
        trajectory = build_trajectory_report(cycle.rounds)
        if trajectory and trajectory.classification != "healthy":
            round_analysis["trajectory"] = f"{trajectory.classification}: {trajectory.description}"

        return cls(
            results=scoring_result.winner_results,
            accuracy=scoring_result.winner_accuracy,
            composite=scoring_result.winner_composite,
            degraded_queries=scoring_result.degraded_queries,
            round_history=[
                {
                    "round": r.round,
                    "accuracy": r.accuracy,
                    "composite": r.composite,
                    "pipeline_params": r.pipeline_params,
                    "degraded": getattr(r, "degraded_queries", 0),
                    "n_candidates": len(r.candidate_scores),
                }
                for r in cycle.rounds
            ],
            current_round=round_num,
            l1_stall_count=cycle.escalation.l1_stall_count,
            best_accuracy=cycle.best_accuracy,
            best_round=cycle.best_round,
            pipeline_params=(cycle.current_sp.pipeline_params if cycle.current_sp else None),
            candidate_keys=candidate_keys_from_schema(schema),
            pipeline_schema=schema,
            degradation_threshold=config.optimization.l1_critique_degradation_threshold,
            near_miss_ratio=config.optimization.l1_critique_near_miss_ratio,
            search_memory_digest=search_memory_digest,
            round_analysis=round_analysis,
            runtime_failures=[
                {
                    "candidate_desc": (cs.get("changes_description") or cs.get("candidate_id", ""))[
                        :60
                    ],
                    **rf,
                }
                for cs in scoring_result.candidate_scores
                for rf in (cs.get("runtime_failures") or [])
            ],
            current_prompt_chars=len(cycle.opt_sp.render()),
        )


_CRITIQUE_SM_LABELS = {
    "discriminating_queries": "Discriminating queries",
    "failure_clusters": "Failure clusters",
    "tractability": "Query tractability",
    "exhausted_axes": "Exhausted axes (DO NOT suggest these)",
    "value_trends": "Value trends",
    "improvement_attribution": "WHAT WORKED",
}

_RF_CFG_AXES = ("model", "temperature", "max_tokens", "reasoning_effort")


class L1CritiqueAgent:
    """Analyzes scoring results; returns 6-field critique dict consumed by L1/L2."""

    def __init__(
        self,
        llm_client: LLMClientBase,
        model: str | None = None,
    ):
        self.llm_client = llm_client
        self.model = model

    async def run(self, ctx: RoundSnapshot) -> dict:
        """Build critique from pipeline stats + LLM analysis."""
        sections = _assemble_l1_critique_sections(ctx)
        _compile_vars = {"inbox": sections}
        _template = load_optimizer_prompt("l1_critique")
        prompt = _template.compile_prompt(**_compile_vars)
        logger.info(
            "Rich L1 critique: %d chars prompt, round %d, acc=%.3f",
            len(prompt),
            ctx.current_round + 1,
            ctx.accuracy,
        )

        response = await llm_call(
            self.llm_client,
            messages=[{"role": "user", "content": prompt}],
            node="l1_critique",
            model=self.model,
            trace_meta={
                "template_name": "l1_critique",
                "template_fields": _template.prompt_field_dict(),
                "variables": _compile_vars,
            },
        )

        return _parse_l1_critique(response.content)


def _parse_l1_critique(content: str) -> dict:
    """Parse LLM L1 critique response into the 6-field dict."""
    try:
        result = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        result = {}
    return {
        "positive_critique": result.get("positive_critique", ""),
        "negative_critique": result.get("negative_critique", ""),
        "priority_fix": result.get("priority_fix", ""),
        "suggested_axes": result.get("suggested_axes", []),
        "failure_highlights": result.get("failure_highlights", []),
        "summary": result.get("summary", content),
    }


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


def sample_thinking_styles(n: int = 3, seed: int | None = None) -> list[str]:
    """Sample thinking styles from the variant library for meta-prompt injection."""
    styles = [
        s
        for s in load_variant_library().get("prompt_fields", {}).get("thinking_style", [])
        if s and s.strip()
    ]
    return random.Random(seed).sample(styles, min(n, len(styles))) if styles else []


# ---------------------------------------------------------------------------
# Stat-section builders — private helpers for _assemble_l1_critique_sections.
# ---------------------------------------------------------------------------


_PROMPT_BLOAT_CHARS = 3000


def _summary_section(ctx: RoundSnapshot) -> str:
    total = len(ctx.results)
    lines = [
        "## SCORING SUMMARY",
        f"Accuracy: {ctx.accuracy:.1%} | Composite: {ctx.composite:.4f} | "
        f"Degraded: {ctx.degraded_queries}/{total}",
        f"Round {ctx.current_round} | L1 stall count: {ctx.l1_stall_count} | "
        f"Best so far: {ctx.best_accuracy:.1%} (round {ctx.best_round})",
    ]
    if ctx.current_prompt_chars:
        bloat = (
            " — prompt is bloated; favour compression in priority_fix"
            if ctx.current_prompt_chars > _PROMPT_BLOAT_CHARS
            else ""
        )
        lines.append(f"Current prompt size: {ctx.current_prompt_chars} chars{bloat}")
    return "\n".join(lines)


def _pipeline_health_section(
    results: list[QueryResult],
    anomalies: list[str],
    *,
    degradation_threshold: float = 0.4,
) -> str:
    """Termination distribution + degradation rate + error rate."""
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
    """Rank buckets + top-k recall + near-miss patterns."""
    rank_map: dict[int, int | None] = {
        i: find_rank(get_candidates(r, candidate_keys), r.get("ground_truth", ""))
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
    """Accuracy trajectory + inter-round param changes."""
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
    """Failures grouped by termination step."""
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
    """Per-query failure breakdown (skips near-miss queries shown above)."""
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
    """Success count + brief samples."""
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


def _runtime_failures_section(entries: list[dict]) -> str:
    """Per-candidate RuntimeFailures as a hard-constraint section."""
    if not entries:
        return ""
    by_warning: dict[str, list[dict]] = {}
    for e in entries:
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


def _assemble_l1_critique_sections(ctx: RoundSnapshot) -> str:
    """Build stat-rich sections for the L1 critique template."""
    results = ctx.results
    anomalies: list[str] = []

    rank_text, nm_queries = _rank_analysis_section(
        results,
        anomalies,
        candidate_keys=ctx.candidate_keys or None,
        near_miss_ratio=ctx.near_miss_ratio,
    )

    rt_failures_text = _runtime_failures_section(ctx.runtime_failures)

    sections = [
        _summary_section(ctx),
        _pipeline_health_section(
            results,
            anomalies,
            degradation_threshold=ctx.degradation_threshold,
        ),
    ]
    if rt_failures_text:
        sections.append(rt_failures_text)
    sections += [
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

    hi = format_search_memory_block(
        ctx.search_memory_digest,
        _CRITIQUE_SM_LABELS,
        header="## HISTORICAL INTELLIGENCE",
    )
    if hi:
        sections.append(hi)

    if ctx.round_analysis:
        ra_parts: list[str] = []
        if traj := ctx.round_analysis.get("trajectory"):
            ra_parts.append(f"  [TRAJECTORY] {traj}")
        if diff := ctx.round_analysis.get("cross_candidate_diff"):
            ra_parts.append(f"  MISSED OPPORTUNITIES:\n{diff}")
        if ra_parts:
            sections.append("## THIS ROUND\n" + "\n".join(ra_parts))

    if ctx.pipeline_schema:
        cap_lines = [
            f"  {node.name} has mutable output_schema"
            f" (current fields: {', '.join(node.output_schema.fields)})"
            for node in ctx.pipeline_schema.nodes
            if node.output_schema
            and node.output_schema.fields
            and "output_schema" in node.param_keys
        ]
        if cap_lines:
            sections.append(
                "## AVAILABLE SCHEMA MUTATIONS\n"
                + "\n".join(cap_lines)
                + "\n  Use output_schema param with +/-/~ mutation tuples"
                " to add/remove/replace fields."
            )

    return "\n\n".join(s for s in sections if s)
