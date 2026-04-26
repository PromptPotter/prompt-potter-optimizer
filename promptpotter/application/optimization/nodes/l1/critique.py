"""L1 critique phase — LLM analysis of a round's results."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.nodes.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
    format_search_memory_block,
)
from promptpotter.application.optimization.pipeline import run_optimizer_node
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
    "RoundSnapshot",
    "format_l1_critique_for_prompt",
    "run_l1_critique",
]

_PROMPT_BLOAT_CHARS = 3000
_RF_CFG_AXES = ("model", "temperature", "max_tokens", "reasoning_effort")

_CRITIQUE_SM_LABELS = {
    "discriminating_queries": "Discriminating queries",
    "failure_clusters": "Failure clusters",
    "tractability": "Query tractability",
    "exhausted_axes": "Exhausted axes (DO NOT suggest these)",
    "value_trends": "Value trends",
    "improvement_attribution": "WHAT WORKED",
}


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


async def run_l1_critique(
    ctx: RoundSnapshot,
    llm_client: LLMClientBase,
    model: str | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict."""
    sections = _assemble_l1_critique_sections(ctx)
    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        compile_vars={"inbox": sections},
        llm_client=llm_client,
        model=model,
    )
    logger.info(
        "Rich L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        ctx.current_round + 1,
        ctx.accuracy,
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


def _assemble_l1_critique_sections(ctx: RoundSnapshot) -> str:
    """Build the L1 critique sections from RoundSnapshot."""
    results = ctx.results
    total = len(results)
    candidate_keys = ctx.candidate_keys or None
    anomalies: list[str] = []
    sections: list[str] = []

    # --- Scoring summary -----------------------------------------------------
    summary_lines = [
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
        summary_lines.append(f"Current prompt size: {ctx.current_prompt_chars} chars{bloat}")
    sections.append("\n".join(summary_lines))

    # --- Pipeline health -----------------------------------------------------
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
        deg_rate = web_warning_count / total
        ph_lines = ["## PIPELINE HEALTH"]
        if termination:
            ph_lines.append("Termination distribution:")
            for step, count in termination.most_common():
                ph_lines.append(f"  {step}: {count}/{total}")
        ph_lines.append(f"Step degradation: {deg_rate:.0%} of queries")
        ph_lines.append(f"Error rate: {error_count / total:.0%}")
        sections.append("\n".join(ph_lines))

    # --- Runtime failures (rail 2 hard constraints) --------------------------
    if ctx.runtime_failures:
        by_warning: dict[str, list[dict]] = {}
        for e in ctx.runtime_failures:
            by_warning.setdefault(str(e.get("dominant_warning", "unknown")), []).append(e)
        rf_lines = ["## RUNTIME FAILURES THIS ROUND (Rail 2 — treat as hard constraints)"]
        for dom in sorted(by_warning):
            rf_lines.append(f"  {dom}:")
            for e in by_warning[dom]:
                rate = float(e.get("degraded_rate", 0.0)) * 100
                dc = e.get("degraded_count", 0)
                tot = e.get("total_evaluated", 0)
                cfg = e.get("observed_config") or {}
                cfg_bits = ", ".join(f"{k}={cfg[k]}" for k in _RF_CFG_AXES if k in cfg)
                rf_lines.append(
                    f"    {e.get('candidate_desc', '?')}: {rate:.0f}% ({dc}/{tot}) @ {cfg_bits}"
                )
        rf_lines.append(
            "  These configurations are broken — do NOT propose the same or similar values"
            " next round."
        )
        sections.append("\n".join(rf_lines))

    # --- Rank analysis (computes nm_queries used by failure details) ---------
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
    if n_valid:
        ra_lines = [
            "## CANDIDATE RANK ANALYSIS",
            "Where does ground truth appear in candidate list?",
        ]
        for bucket, count in rank_buckets.items():
            ra_lines.append(f"  Rank {bucket}: {count}")
        for k in (1, 3, 5, 10):
            in_top_k = sum(1 for rank in rank_map.values() if rank is not None and rank <= k)
            ra_lines.append(f"  top-{k}: {in_top_k / n_valid:.0%}")
        if near_misses:
            ra_lines.append(
                f"\nNear misses ({len(near_misses)} — GT in candidates but not rank 1):"
            )
            for nm in near_misses[:15]:
                ra_lines.append(
                    f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                    f"(GT: {nm['ground_truth']})"
                )
        sections.append("\n".join(ra_lines))

    # --- Round evolution -----------------------------------------------------
    if ctx.round_history:
        re_lines = [
            "## ROUND EVOLUTION",
            "Round  Accuracy  Delta   Degraded  Candidates",
        ]
        prev_acc: float | None = None
        plateau_count = 0
        for rh in ctx.round_history:
            acc = rh.get("accuracy", 0.0)
            delta = acc - prev_acc if prev_acc is not None else 0.0
            re_lines.append(
                f"  {rh.get('round', '?'):>5}  {acc:>7.1%}  {delta:>+6.1%}  "
                f"{rh.get('degraded', 0):>8}  {rh.get('n_candidates', 0):>10}"
            )
            plateau_count = plateau_count + 1 if abs(delta) < 0.01 else 0
            prev_acc = acc
        for i in range(1, len(ctx.round_history)):
            prev_pp = ctx.round_history[i - 1].get("pipeline_params") or {}
            curr_pp = ctx.round_history[i].get("pipeline_params") or {}
            changed = {
                k
                for k in set(prev_pp) | set(curr_pp)
                if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
            }
            if changed:
                re_lines.append(
                    f"  Round {ctx.round_history[i - 1].get('round')}→"
                    f"{ctx.round_history[i].get('round')}: "
                    f"{', '.join(sorted(changed))}"
                )
        if plateau_count >= 2:
            anomalies.append(
                f"[MEDIUM] plateau_signal: {plateau_count} consecutive rounds with <1% improvement."
            )
        sections.append("\n".join(re_lines))

    # --- Query categories ----------------------------------------------------
    step_counts: Counter[str] = Counter()
    for r in results:
        if r.get("hit") or is_error_result(r):
            continue
        pd = r.get("pipeline_data") or {}
        step_counts[pd.get("terminated_at", "unknown")] += 1
    if step_counts:
        qc_lines = ["## QUERY CATEGORIES", "Failures by termination step:"]
        for step, count in step_counts.most_common():
            qc_lines.append(f"  {step}: {count}")
        sections.append("\n".join(qc_lines))

    # --- Failure details (skips queries already shown in near-misses) --------
    failures = [
        r
        for r in results
        if not r.get("hit") and not is_error_result(r) and r.get("query", "") not in nm_queries
    ]
    if failures:
        fd_lines = [f"## FAILURE DETAILS ({len(failures)} non-near-miss failures)"]
        for r in failures[:8]:
            pd = r.get("pipeline_data") or {}
            gt = r.get("ground_truth", "?")
            rank = find_rank(get_candidates(r, candidate_keys), gt)
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
            fd_lines.append(
                f"  MISS  [{pd.get('terminated_at', '?')}]  {r['query'][:70]}\n"
                f"        -> {r.get('predicted', '?')[:70]}\n"
                f"        GT: {gt[:70]}  |  {rank_str}  {warn}{diag_str}"
            )
        sections.append("\n".join(fd_lines))

    # --- Successes -----------------------------------------------------------
    successes = [r for r in results if r.get("hit")]
    if successes:
        sc_lines = [f"## SUCCESSES ({len(successes)} queries)"]
        for r in successes[:2]:
            pd = r.get("pipeline_data") or {}
            sc_lines.append(
                f"  HIT  [{pd.get('terminated_at', '?')}]  "
                f"{r['query'][:70]} → {r.get('predicted', '?')[:70]}"
            )
        sections.append("\n".join(sc_lines))

    # --- Anomaly flags (post-process: insert at index 1, after summary) ------
    if results and anomalies:
        anomaly_block = "## ANOMALY FLAGS ({})\n{}".format(
            len(anomalies),
            "\n".join(f"  {a}" for a in anomalies),
        )
        sections.insert(1, anomaly_block)

    # --- Historical intelligence (search memory digest) ----------------------
    hi = format_search_memory_block(
        ctx.search_memory_digest,
        _CRITIQUE_SM_LABELS,
        header="## HISTORICAL INTELLIGENCE",
    )
    if hi:
        sections.append(hi)

    # --- This-round trajectory + cross-candidate diff ------------------------
    if ctx.round_analysis:
        ra_parts: list[str] = []
        if traj := ctx.round_analysis.get("trajectory"):
            ra_parts.append(f"  [TRAJECTORY] {traj}")
        if diff := ctx.round_analysis.get("cross_candidate_diff"):
            ra_parts.append(f"  MISSED OPPORTUNITIES:\n{diff}")
        if ra_parts:
            sections.append("## THIS ROUND\n" + "\n".join(ra_parts))

    # --- Available schema mutations ------------------------------------------
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
