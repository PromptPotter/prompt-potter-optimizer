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

from api.config.optimizer_pipeline import get_node_config, llm_call
from api.config.settings import load_variant_library

if TYPE_CHECKING:
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CritiqueContext — all data available for critique analysis
# ---------------------------------------------------------------------------


@dataclass
class CritiqueContext:
    """Bundles current-round results, round history, and scan context."""

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

    # Scan context (optional)
    scan_context: dict | None = None

    # Current pipeline config
    pipeline_params: dict | None = None

    # Cross-round warning inventory (from OptSearchPoint.warning_inventory)
    warning_inventory: dict | None = None

    # L2 domain context (so critique understands L2's problem framing)
    task_context: dict | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_rank(candidates: list, ground_truth: str) -> int | None:
    """Find 1-based rank of ground_truth in candidates list."""
    if not candidates or not ground_truth:
        return None
    for i, c in enumerate(candidates):
        name = c.get("candidate", c) if isinstance(c, dict) else (
            c[0] if isinstance(c, (list, tuple)) else str(c)
        )
        if str(name) == ground_truth:
            return i + 1
    return None


def _get_candidates(r: dict) -> list:
    pd = r.get("pipeline_data") or {}
    return pd.get("ranked_candidates") or pd.get("token_matched_candidates") or []


# ---------------------------------------------------------------------------
# Per-query warning inventory (cross-round tracking)
# ---------------------------------------------------------------------------


def _extract_warning_types(result: dict) -> list[str]:
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
        entry = tracker.setdefault(query, {
            "rounds_seen": 0,
            "hits": 0,
            "misses": 0,
            "warnings": {},
            "last_terminated_at": "",
        })
        entry["rounds_seen"] += 1
        if r.get("hit"):
            entry["hits"] += 1
        else:
            entry["misses"] += 1
        pd = r.get("pipeline_data") or {}
        terminated = pd.get("terminated_at", "")
        if terminated:
            entry["last_terminated_at"] = terminated
        for wtype in _extract_warning_types(r):
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
        (e.get("rounds_seen", 0) for e in tracker.values()), default=0,
    )
    lines = [f"## RECURRING PIPELINE WARNINGS (across {max_rounds} rounds)"]
    for wtype, entries in sorted(
        by_warning.items(), key=lambda x: -len(x[1]),
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
            lines.append(
                f"    {query[:70]}  "
                f"({wcount}/{seen} rounds, {hits} hits)"
            )
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


CRITIQUE_TASK = """\
Analyze all data above and return JSON:
{
  "positive_critique": "<what is working well — patterns to extend>",
  "negative_critique": "<what is failing — root causes and blockers>",
  "priority_fix": "<what change(s) would have the most impact>",
  "suggested_axes": ["<pipeline_param or prompt_field to try next>"],
  "summary": "<2-3 sentence actionable critique for the next candidate generator>"
}
Rules:
- positive_critique: identify success patterns from the data (hits, near-misses, rank distribution)
- negative_critique: reference specific stats from the sections above
- suggested_axes: name parameters or fields visible in the data above
- All 5 fields are fed to the next generation round — be concise and actionable
"""


def assemble_critique_prompt(ctx: CritiqueContext) -> str:
    """Build the full critique prompt with inline stat computation."""
    results = ctx.results
    total = len(results)
    sections: list[str] = []
    anomalies: list[str] = []

    # --- Summary ---
    sections.append(
        f"## EVALUATION SUMMARY\n"
        f"Accuracy: {ctx.accuracy:.1%} | Composite: {ctx.composite:.4f} | "
        f"Degraded: {ctx.degraded_queries}/{total}\n"
        f"Round {ctx.current_round} | Stall count: {ctx.stall_count} | "
        f"Best so far: {ctx.best_accuracy:.1%} (round {ctx.best_round})"
    )

    # --- Pipeline health ---
    if total > 0:
        web_warning_count = 0
        termination: Counter[str] = Counter()
        times: list[float] = []
        error_count = 0

        for r in results:
            pd = r.get("pipeline_data") or {}
            diag = pd.get("diagnostics") or {}
            if diag.get("warnings"):
                web_warning_count += 1
            termination[pd.get("terminated_at", "unknown")] += 1
            t = pd.get("total_time")
            if t is not None:
                times.append(float(t))
            if r.get("error"):
                error_count += 1

        deg_rate = web_warning_count / total
        lines = ["## PIPELINE HEALTH"]
        if termination:
            lines.append("Termination distribution:")
            for step, count in termination.most_common():
                lines.append(f"  {step}: {count}/{total}")
        lines.append(f"Step degradation: {deg_rate:.0%} of queries")
        if times:
            ts = sorted(times)
            lines.append(
                f"Timing: p50={ts[len(ts)//2]:.0f}ms "
                f"p90={ts[int(len(ts)*0.9)]:.0f}ms "
                f"max={ts[-1]:.0f}ms"
            )
        lines.append(f"Error rate: {error_count / total:.0%}")
        sections.append("\n".join(lines))

        # --- Anomaly flags (inline) ---
        if deg_rate > 0.4:
            anomalies.append(
                f"[HIGH] high_degradation: {web_warning_count}/{total} "
                "queries had degraded steps."
            )

    # --- Rank analysis (precompute ranks once) ---
    rank_map: dict[int, int | None] = {}
    for i, r in enumerate(results):
        if not r.get("error"):
            rank_map[i] = _find_rank(_get_candidates(r), r.get("ground_truth", ""))

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
            near_misses.append({
                "query": r["query"][:80], "ground_truth": r.get("ground_truth", "")[:60],
                "rank": rank, "predicted": r.get("predicted", "?")[:60],
            })
        elif rank is not None and rank <= 10:
            rank_buckets["6-10"] += 1
            near_misses.append({
                "query": r["query"][:80], "ground_truth": r.get("ground_truth", "")[:60],
                "rank": rank, "predicted": r.get("predicted", "?")[:60],
            })
        elif rank is not None and rank <= 20:
            rank_buckets["11-20"] += 1
        else:
            rank_buckets["not_found"] += 1

    n_valid = sum(1 for r in results if not r.get("error"))
    if n_valid > 0:
        lines = ["## CANDIDATE RANK ANALYSIS"]
        lines.append("Where does ground truth appear in candidate list?")
        for bucket, count in rank_buckets.items():
            lines.append(f"  Rank {bucket}: {count}")

        # Top-k recall (using precomputed ranks)
        for k in [1, 3, 5, 10]:
            in_top_k = sum(
                1 for i, rank in rank_map.items()
                if rank is not None and rank <= k
            )
            lines.append(f"  top-{k}: {in_top_k / n_valid:.0%}")

        if near_misses:
            lines.append(f"\nNear misses ({len(near_misses)} — GT in candidates but not rank 1):")
            for nm in near_misses[:15]:
                lines.append(
                    f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                    f"(GT: {nm['ground_truth']})"
                )
        sections.append("\n".join(lines))

        # Near-miss anomaly
        misses = [r for r in results if not r.get("hit") and not r.get("error")]
        if misses and len(near_misses) / max(len(misses), 1) > 0.3:
            anomalies.append(
                f"[MEDIUM] near_miss_pattern: GT in candidates for "
                f"{len(near_misses)}/{len(misses)} misses but not ranked first."
            )

    # --- Round evolution ---
    if ctx.round_history:
        lines = ["## ROUND EVOLUTION"]
        lines.append("Round  Accuracy  Delta   Degraded  Candidates")
        prev_acc = None
        plateau_count = 0
        for rh in ctx.round_history:
            acc = rh.get("accuracy", 0.0)
            delta = acc - prev_acc if prev_acc is not None else 0.0
            lines.append(
                f"  {rh.get('round', '?'):>5}  {acc:>7.1%}  {delta:>+6.1%}  "
                f"{rh.get('degraded', 0):>8}  {rh.get('n_candidates', 0):>10}"
            )
            plateau_count = plateau_count + 1 if abs(delta) < 0.01 else 0
            prev_acc = acc

        # Param changes between rounds
        for i in range(1, len(ctx.round_history)):
            prev_pp = ctx.round_history[i - 1].get("pipeline_params") or {}
            curr_pp = ctx.round_history[i].get("pipeline_params") or {}
            changed = {
                k for k in set(prev_pp) | set(curr_pp)
                if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
            }
            if changed:
                lines.append(
                    f"  Round {ctx.round_history[i-1].get('round')}→"
                    f"{ctx.round_history[i].get('round')}: "
                    f"{', '.join(sorted(changed))}"
                )
        sections.append("\n".join(lines))

        if plateau_count >= 2:
            anomalies.append(
                f"[MEDIUM] plateau_signal: {plateau_count} consecutive rounds "
                "with <1% improvement."
            )

    # --- Emit anomalies ---
    if total > 0 and anomalies:
        sections.insert(1, "## ANOMALY FLAGS ({})\n{}".format(
            len(anomalies), "\n".join(f"  {a}" for a in anomalies),
        ))

    # --- Query categories ---
    failures_by_step: dict[str, list[str]] = {}
    for r in results:
        if r.get("hit") or r.get("error"):
            continue
        pd = r.get("pipeline_data") or {}
        step = pd.get("terminated_at", "unknown")
        failures_by_step.setdefault(step, []).append(r.get("query", "?")[:80])

    if failures_by_step:
        lines = ["## QUERY CATEGORY ANALYSIS"]
        lines.append("Failures by termination step:")
        for step, queries in failures_by_step.items():
            lines.append(f"  {step}: {len(queries)}")
            for q in queries[:5]:
                lines.append(f"    - {q}")
        sections.append("\n".join(lines))

    # --- Scan context ---
    sc = ctx.scan_context
    if sc:
        lines = ["## SCAN CONTEXT (from sensitivity analysis)"]
        if sc.get("leaderboard_text"):
            lines.append("Leaderboard (tested values ranked by accuracy):")
            lines.append(sc["leaderboard_text"])
        if sc.get("sensitivity_text"):
            lines.append("\nAxis sensitivity:")
            lines.append(sc["sensitivity_text"])
        if sc.get("difficulty_text"):
            lines.append("\nQuery difficulty:")
            lines.append(sc["difficulty_text"])
        if sc.get("improving_axes"):
            lines.append(f"\nImproving axes: {', '.join(sc['improving_axes'])}")
        if sc.get("tested_values"):
            lines.append("\nTested values per axis:")
            lines.append(sc["tested_values"])
        sections.append("\n".join(lines))

    # --- Task context (L2 domain framing) ---
    if ctx.task_context:
        tc_lines = "\n".join(
            f"  {k}: {v}" for k, v in ctx.task_context.items() if v
        )
        if tc_lines:
            sections.append(f"## TASK CONTEXT\n{tc_lines}")

    # --- Warning inventory (cross-round) ---
    if ctx.warning_inventory:
        inv_text = summarize_warning_inventory(ctx.warning_inventory)
        if inv_text:
            sections.append(inv_text)

    # --- Failure details ---
    failures = [r for r in results if not r.get("hit") and not r.get("error")]
    if failures:
        lines = [f"## FAILURE DETAILS ({len(failures)} queries)"]
        for r in failures[:15]:
            pd = r.get("pipeline_data") or {}
            gt = r.get("ground_truth", "?")
            rank = _find_rank(_get_candidates(r), gt)
            rank_str = f"rank {rank}" if rank else "not in candidates"
            diag = pd.get("diagnostics") or {}
            warn = "⚠ degraded" if diag.get("warnings") else ""
            lines.append(
                f"  MISS  [{pd.get('terminated_at', '?')}]  {r['query'][:70]}\n"
                f"        → {r.get('predicted', '?')[:70]}\n"
                f"        GT: {gt[:70]}  |  {rank_str}  {warn}"
            )
        sections.append("\n".join(lines))

    # --- Success details ---
    successes = [r for r in results if r.get("hit")]
    if successes:
        lines = [f"## SUCCESS DETAILS ({len(successes)} queries)"]
        for r in successes[:10]:
            pd = r.get("pipeline_data") or {}
            lines.append(
                f"  HIT  [{pd.get('terminated_at', '?')}]  "
                f"{r['query'][:70]} → {r.get('predicted', '?')[:70]}"
            )
        sections.append("\n".join(lines))

    # --- Task ---
    sections.append(CRITIQUE_TASK)

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# CritiqueAgent — LLM-based analysis
# ---------------------------------------------------------------------------


class CritiqueAgent:
    """Analyzes eval results via pipeline-aware critique stats.

    Returns a 5-field dict (positive_critique, negative_critique,
    priority_fix, suggested_axes, summary) fed to both L1 Generate
    and L2 Refine Context.
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
        priority_fix, suggested_axes, summary.
        """
        prompt = assemble_critique_prompt(ctx)
        logger.info(
            "Rich critique: %d chars prompt, round %d, acc=%.3f",
            len(prompt), ctx.current_round + 1, ctx.accuracy,
        )

        response = await llm_call(
            self.llm_client,
            messages=[{"role": "user", "content": prompt}],
            config=get_node_config("critique"),
            model=self.model,
        )

        return _parse_critique(response.content)


def _parse_critique(content: str) -> dict:
    """Parse LLM critique response into the 5-field dict."""
    try:
        result = json.loads(content)
        return {
            "positive_critique": result.get("positive_critique", ""),
            "negative_critique": result.get("negative_critique", ""),
            "priority_fix": result.get("priority_fix", ""),
            "suggested_axes": result.get("suggested_axes", []),
            "summary": result.get("summary", content),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "positive_critique": "",
            "negative_critique": "",
            "priority_fix": "",
            "suggested_axes": [],
            "summary": content,
        }


def format_critique_for_prompt(critique: dict) -> str:
    """Format critique dict into text for injection into L1/L2 prompts."""
    parts = []
    if critique.get("positive_critique"):
        parts.append(f"Strengths: {critique['positive_critique']}")
    if critique.get("negative_critique"):
        parts.append(f"Weaknesses: {critique['negative_critique']}")
    if critique.get("priority_fix"):
        parts.append(f"Priority fix: {critique['priority_fix']}")
    if critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(critique['suggested_axes'])}")
    if critique.get("summary"):
        parts.append(f"Summary: {critique['summary']}")
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
