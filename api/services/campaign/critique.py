"""Critique agent — stat computation, prompt assembly, and LLM analysis.

Pure stat functions (no I/O, no LLM calls) compute pipeline health,
rank analysis, round evolution, and query categories. CritiqueAgent
assembles a stat-rich prompt and calls the LLM for structured feedback.
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from api.config.optimizer_pipeline import llm_call
from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.config.settings import load_variant_library

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CritiqueContext — all data available for critique analysis
# ---------------------------------------------------------------------------


@dataclass
class CritiqueContext:
    """Bundles current-round results and round history for diagnostic analysis."""

    results: list[dict]
    accuracy: float
    composite: float = 0.0
    degraded_queries: int = 0

    # Round history
    round_history: list[dict] = field(default_factory=list)
    current_round: int = 0
    stall_count: int = 0
    best_accuracy: float = 0.0
    best_round: int = -1

    # Current pipeline config
    pipeline_params: dict | None = None

    # Schema-driven candidate keys (from PipelineNode.output_keys for ranker/candidate_source nodes)
    candidate_keys: list[str] = field(default_factory=list)

    # Pipeline schema for diagnostic enrichment (Wave 1c)
    pipeline_schema: PipelineSchema | None = None

    # Configurable thresholds
    degradation_threshold: float = 0.4
    near_miss_ratio: float = 0.3

    # SearchMemory context (M8 Wave 3c)
    search_memory_context: dict | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_rank(candidates: list, ground_truth: str) -> int | None:
    """Find 1-based rank of ground_truth in candidates list."""
    if not candidates or not ground_truth:
        return None
    for i, c in enumerate(candidates):
        name = (
            c.get("candidate", c)
            if isinstance(c, dict)
            else (c[0] if isinstance(c, (list, tuple)) else str(c))
        )
        if str(name) == ground_truth:
            return i + 1
    return None


def get_candidates(r: dict, candidate_keys: list[str] | None = None) -> list:
    """Extract candidates from a result dict, checking keys in order."""
    pd = r.get("pipeline_data") or {}
    for key in candidate_keys or []:
        val = pd.get(key)
        if val:
            return val
    return []


# ---------------------------------------------------------------------------
# Per-query warning inventory (cross-round tracking)
# ---------------------------------------------------------------------------


def extract_warning_types(result: dict) -> list[str]:
    """Extract warning type strings from a single eval result."""
    pd = result.get("pipeline_data") or {}
    diag = pd.get("diagnostics") or {}
    types: list[str] = []
    for w in diag.get("warnings") or []:
        if isinstance(w, dict):
            types.append(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
        elif isinstance(w, str):
            types.append(w)
    return types


def update_query_tracker(
    tracker: dict[str, dict],
    results: list[dict],
) -> None:
    """Merge current round's results into the per-query warning inventory.

    Mutates *tracker* in-place. For each query, increments ``rounds_seen``,
    ``hits``/``misses``, warning type counts, and updates ``last_terminated_at``.
    """
    for r in results:
        query = r.get("query", "")
        if not query:
            continue
        entry = tracker.setdefault(
            query,
            {
                "rounds_seen": 0,
                "hits": 0,
                "misses": 0,
                "warnings": {},
                "last_terminated_at": "",
            },
        )
        entry["rounds_seen"] += 1
        if r.get("hit"):
            entry["hits"] += 1
        else:
            entry["misses"] += 1
        pd = r.get("pipeline_data") or {}
        terminated = pd.get("terminated_at", "")
        if terminated:
            entry["last_terminated_at"] = terminated
        for wtype in extract_warning_types(r):
            entry["warnings"][wtype] = entry["warnings"].get(wtype, 0) + 1


def summarize_warning_inventory(tracker: dict[str, dict]) -> str:
    """Build a text summary of recurring warnings across rounds.

    Groups queries by warning type and shows per-query hit/miss stats.
    Returns empty string when no warnings are tracked.
    """
    # Collect queries that have any warnings
    by_warning: dict[str, list[tuple[str, dict]]] = {}
    for query, entry in tracker.items():
        for wtype, _count in entry.get("warnings", {}).items():
            by_warning.setdefault(wtype, []).append((query, entry))

    if not by_warning:
        return ""

    max_rounds = max(
        (e.get("rounds_seen", 0) for e in tracker.values()),
        default=0,
    )
    lines = [f"## RECURRING PIPELINE WARNINGS (across {max_rounds} rounds)"]
    for wtype, entries in sorted(
        by_warning.items(),
        key=lambda x: -len(x[1]),
    ):
        lines.append(f"  {wtype} — {len(entries)} queries affected:")
        # Sort by warning frequency descending
        for query, entry in sorted(
            entries,
            key=lambda x: -x[1]["warnings"].get(wtype, 0),
        )[:10]:
            wcount = entry["warnings"].get(wtype, 0)
            seen = entry["rounds_seen"]
            hits = entry["hits"]
            lines.append(f"    {query[:70]}  ({wcount}/{seen} rounds, {hits} hits)")
    return "\n".join(lines)


def warning_summary(tracker: dict[str, dict]) -> tuple[int, str]:
    """Return ``(warned_count, top_warning_type)`` from the warning inventory.

    ``warned_count`` is the number of queries with at least one warning.
    ``top_warning_type`` is the most frequent warning type, or ``""`` if none.
    """
    if not tracker:
        return 0, ""
    warned_count = sum(1 for e in tracker.values() if e.get("warnings"))
    all_wtypes: dict[str, int] = {}
    for e in tracker.values():
        for wt, c in e.get("warnings", {}).items():
            all_wtypes[wt] = all_wtypes.get(wt, 0) + c
    top_warning = max(all_wtypes, key=all_wtypes.get) if all_wtypes else ""  # type: ignore[arg-type]
    return warned_count, top_warning


# ---------------------------------------------------------------------------
# Prompt assembly — inline stat computation
# ---------------------------------------------------------------------------




def _summary_section(ctx: CritiqueContext) -> str:
    total = len(ctx.results)
    return (
        f"## EVALUATION SUMMARY\n"
        f"Accuracy: {ctx.accuracy:.1%} | Composite: {ctx.composite:.4f} | "
        f"Degraded: {ctx.degraded_queries}/{total}\n"
        f"Round {ctx.current_round} | Stall count: {ctx.stall_count} | "
        f"Best so far: {ctx.best_accuracy:.1%} (round {ctx.best_round})"
    )


def _pipeline_health_section(
    results: list[dict],
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
        if r.get("error"):
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
    results: list[dict],
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
        if not r.get("error"):
            rank_map[i] = find_rank(get_candidates(r, candidate_keys), r.get("ground_truth", ""))

    rank_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "not_found": 0}
    near_misses: list[dict] = []
    for i, r in enumerate(results):
        if r.get("error"):
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

    n_valid = sum(1 for r in results if not r.get("error"))
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

    # >30% of misses are near-misses → ranking problem, not retrieval
    misses = [r for r in results if not r.get("hit") and not r.get("error")]
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


def _query_category_section(results: list[dict]) -> str:
    """Failures grouped by termination step (counts only)."""
    step_counts: Counter[str] = Counter()
    for r in results:
        if r.get("hit") or r.get("error"):
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
    results: list[dict],
    candidate_keys: list[str] | None = None,
    near_miss_queries: set[str] | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Per-query failure breakdown with rank, degradation, and diagnostic info.

    When ``pipeline_schema`` is provided, appends sample diagnostic signals
    (Wave 1c) to each failure line.

    Skips queries already shown in rank_analysis near-miss section to
    avoid duplication.
    """
    failures = [r for r in results if not r.get("hit") and not r.get("error")]
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

        # Diagnostic annotation (Wave 1c)
        diag_str = ""
        if pipeline_schema:
            from api.services.metrics import extract_sample_diagnostics

            sd = extract_sample_diagnostics(r, pipeline_schema)
            sig_parts = []
            for k in ("gt_in_source", "gt_in_ranked", "terminated_at"):
                if k in sd:
                    sig_parts.append(f"{k}={sd[k]}")
            if sig_parts:
                diag_str = " | " + ", ".join(sig_parts)

        lines.append(
            f"  MISS  [{pd.get('terminated_at', '?')}]  {r['query'][:70]}\n"
            f"        -> {r.get('predicted', '?')[:70]}\n"
            f"        GT: {gt[:70]}  |  {rank_str}  {warn}{diag_str}"
        )
    return "\n".join(lines)


def _success_details_section(results: list[dict]) -> str:
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


def assemble_critique_sections(ctx: CritiqueContext) -> str:
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
    ]
    sections += [
        _failure_details_section(
            results,
            candidate_keys=ctx.candidate_keys or None,
            near_miss_queries=nm_queries,
            pipeline_schema=ctx.pipeline_schema,
        ),
        _success_details_section(results),
    ]

    # Insert anomaly flags right after the summary
    if results and anomalies:
        anomaly_block = "## ANOMALY FLAGS ({})\n{}".format(
            len(anomalies),
            "\n".join(f"  {a}" for a in anomalies),
        )
        sections.insert(1, anomaly_block)

    # SearchMemory historical context (Wave 3c)
    smc = ctx.search_memory_context
    if smc:
        sm_lines = ["## HISTORICAL INTELLIGENCE"]
        if smc.get("discriminating_queries"):
            sm_lines.append(f"  Discriminating queries: {smc['discriminating_queries']}")
        if smc.get("failure_clusters"):
            sm_lines.append(f"  Failure clusters: {smc['failure_clusters']}")
        sections.append("\n".join(sm_lines))

    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# CritiqueAgent — LLM-based analysis
# ---------------------------------------------------------------------------


class CritiqueAgent:
    """Analyzes eval results via pipeline-aware critique stats.

    Returns a 6-field dict (positive_critique, negative_critique,
    priority_fix, suggested_axes, failure_highlights, summary) fed
    to both L1 Generate and L2 Refine Context.
    """

    def __init__(
        self,
        llm_client: LLMClientBase,
        model: str | None = None,
    ):
        self.llm_client = llm_client
        self.model = model

    async def run(self, ctx: CritiqueContext) -> dict:
        """Build critique from pipeline stats + LLM analysis.

        Returns dict with keys: positive_critique, negative_critique,
        priority_fix, suggested_axes, failure_highlights, summary.
        """
        sections = assemble_critique_sections(ctx)
        prompt = load_optimizer_prompt("critique").compile_prompt(
            stat_sections=sections,
        )
        logger.info(
            "Rich critique: %d chars prompt, round %d, acc=%.3f",
            len(prompt),
            ctx.current_round + 1,
            ctx.accuracy,
        )

        response = await llm_call(
            self.llm_client,
            messages=[{"role": "user", "content": prompt}],
            node="critique",
            model=self.model,
        )

        return _parse_critique(response.content)


def _parse_critique(content: str) -> dict:
    """Parse LLM critique response into the 6-field dict."""
    try:
        result = json.loads(content)
        return {
            "positive_critique": result.get("positive_critique", ""),
            "negative_critique": result.get("negative_critique", ""),
            "priority_fix": result.get("priority_fix", ""),
            "suggested_axes": result.get("suggested_axes", []),
            "failure_highlights": result.get("failure_highlights", []),
            "summary": result.get("summary", content),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "positive_critique": "",
            "negative_critique": "",
            "priority_fix": "",
            "suggested_axes": [],
            "failure_highlights": [],
            "summary": content,
        }


def format_critique_for_prompt(critique: dict) -> str:
    """Format critique dict into compact text for injection into L1/L2 prompts.

    Emits only the actionable fields: summary, priority_fix, suggested_axes,
    and failure_highlights. Diagnostic detail (positive/negative_critique) stays
    internal to critique — ``summary`` already distills it.
    """
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
    lib = load_variant_library()
    styles = lib.get("prompt_fields", {}).get("thinking_style", [])
    styles = [s for s in styles if s and s.strip()]
    if not styles:
        return []
    rng = random.Random(seed)
    return rng.sample(styles, min(n, len(styles)))
