"""Deterministic stat computers and anomaly detectors for the critique agent.

Pre-computes pipeline health, rank analysis, round evolution, and query
categories from evaluation results.  Assembles a rich prompt for LLM critique.
All functions are pure (no I/O, no LLM calls) — registered as tools for
future ReAct-style upgrade.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


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


# ---------------------------------------------------------------------------
# Anomaly flag
# ---------------------------------------------------------------------------


@dataclass
class AnomalyFlag:
    name: str
    severity: str  # "high", "medium", "low"
    message: str


# ---------------------------------------------------------------------------
# Stat computers (pure functions)
# ---------------------------------------------------------------------------


def compute_pipeline_health(results: list[dict]) -> dict[str, Any]:
    """Compute pipeline execution health metrics from results."""
    total = len(results)
    if total == 0:
        return {}

    # Web search degradation
    web_warning_count = 0
    url_fetched_total = 0
    url_content_total = 0
    for r in results:
        pd = r.get("pipeline_data") or {}
        diag = pd.get("diagnostics") or {}
        warnings = diag.get("warnings") or []
        if warnings:
            web_warning_count += 1
        # Try to extract URL yield from warnings text
        for w in (warnings if isinstance(warnings, list) else []):
            w_str = str(w)
            if "fetched URLs" in w_str or "URLs scraped" in w_str:
                # Parse "N of M fetched URLs returned content"
                parts = w_str.split()
                for i, p in enumerate(parts):
                    if p == "of" and i > 0 and i + 1 < len(parts):
                        try:
                            content = int(parts[i - 1])
                            fetched = int(parts[i + 1])
                            url_content_total += content
                            url_fetched_total += fetched
                        except (ValueError, IndexError):
                            pass

    # Termination distribution
    termination: Counter[str] = Counter()
    for r in results:
        pd = r.get("pipeline_data") or {}
        step = pd.get("terminated_at", "unknown")
        termination[step] += 1

    # Timing stats
    times = []
    for r in results:
        pd = r.get("pipeline_data") or {}
        t = pd.get("total_time")
        if t is not None:
            times.append(float(t))

    timing_stats = {}
    if times:
        times_sorted = sorted(times)
        timing_stats = {
            "p50_ms": times_sorted[len(times_sorted) // 2],
            "p90_ms": times_sorted[int(len(times_sorted) * 0.9)],
            "max_ms": times_sorted[-1],
        }

    error_count = sum(1 for r in results if r.get("error"))

    return {
        "web_search_degradation_rate": web_warning_count / total,
        "web_search_url_yield": (
            url_content_total / url_fetched_total
            if url_fetched_total > 0 else None
        ),
        "termination_distribution": dict(termination.most_common()),
        "timing_stats": timing_stats,
        "error_rate": error_count / total,
        "degraded_count": web_warning_count,
        "total": total,
    }


def compute_rank_analysis(results: list[dict]) -> dict[str, Any]:
    """Analyze where ground truth appears in candidate rankings."""
    rank_buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "not_found": 0}
    near_misses: list[dict] = []
    candidate_counts: list[int] = []

    for r in results:
        if r.get("error"):
            continue
        pd = r.get("pipeline_data") or {}
        candidates = pd.get("ranked_candidates") or pd.get("token_matched_candidates") or []
        candidate_counts.append(len(candidates))

        gt = r.get("ground_truth", "")
        rank = _find_rank(candidates, gt)

        if rank == 1:
            rank_buckets["1"] += 1
        elif rank is not None and rank <= 5:
            rank_buckets["2-5"] += 1
            near_misses.append({
                "query": r["query"][:80],
                "ground_truth": gt[:60],
                "rank": rank,
                "predicted": r.get("predicted", "?")[:60],
            })
        elif rank is not None and rank <= 10:
            rank_buckets["6-10"] += 1
            near_misses.append({
                "query": r["query"][:80],
                "ground_truth": gt[:60],
                "rank": rank,
                "predicted": r.get("predicted", "?")[:60],
            })
        elif rank is not None and rank <= 20:
            rank_buckets["11-20"] += 1
        else:
            rank_buckets["not_found"] += 1

    n_valid = sum(1 for r in results if not r.get("error"))
    top_k_recall = {}
    if n_valid > 0:
        for k in [1, 3, 5, 10]:
            in_top_k = sum(
                1 for r in results
                if not r.get("error")
                and _find_rank(
                    (r.get("pipeline_data") or {}).get("ranked_candidates")
                    or (r.get("pipeline_data") or {}).get("token_matched_candidates")
                    or [],
                    r.get("ground_truth", ""),
                ) is not None
                and _find_rank(
                    (r.get("pipeline_data") or {}).get("ranked_candidates")
                    or (r.get("pipeline_data") or {}).get("token_matched_candidates")
                    or [],
                    r.get("ground_truth", ""),
                ) <= k
            )
            top_k_recall[k] = in_top_k / n_valid

    return {
        "rank_distribution": rank_buckets,
        "near_misses": near_misses[:15],
        "candidate_count_avg": (
            sum(candidate_counts) / len(candidate_counts)
            if candidate_counts else 0
        ),
        "top_k_recall": top_k_recall,
    }


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


def compute_round_evolution(round_history: list[dict]) -> dict[str, Any]:
    """Analyze how metrics evolve across rounds."""
    if not round_history:
        return {}

    trajectory = []
    prev_acc = None
    for rh in round_history:
        acc = rh.get("accuracy", 0.0)
        delta = acc - prev_acc if prev_acc is not None else 0.0
        trajectory.append({
            "round": rh.get("round", "?"),
            "accuracy": acc,
            "composite": rh.get("composite", acc),
            "delta": delta,
            "degraded": rh.get("degraded", 0),
            "n_candidates": rh.get("n_candidates", 0),
        })
        prev_acc = acc

    # Plateau detection
    plateau_count = 0
    for t in trajectory:
        if abs(t["delta"]) < 0.01:
            plateau_count += 1
        else:
            plateau_count = 0

    # Param changes between rounds
    param_changes = []
    for i in range(1, len(round_history)):
        prev_pp = round_history[i - 1].get("pipeline_params") or {}
        curr_pp = round_history[i].get("pipeline_params") or {}
        changed = {
            k for k in set(prev_pp) | set(curr_pp)
            if prev_pp.get(k) != curr_pp.get(k) and k != "steps"
        }
        if changed:
            param_changes.append({
                "from_round": round_history[i - 1].get("round"),
                "to_round": round_history[i].get("round"),
                "changed_params": sorted(changed),
            })

    return {
        "trajectory": trajectory,
        "plateau_rounds": plateau_count,
        "param_changes": param_changes,
    }


def compute_query_categories(results: list[dict]) -> dict[str, Any]:
    """Group failures by pipeline step and query patterns."""
    failures_by_step: dict[str, list[str]] = {}
    failures_with_warnings: list[str] = []
    hits: list[str] = []

    for r in results:
        q = r.get("query", "?")[:80]
        if r.get("hit"):
            hits.append(q)
            continue
        if r.get("error"):
            continue

        pd = r.get("pipeline_data") or {}
        step = pd.get("terminated_at", "unknown")
        failures_by_step.setdefault(step, []).append(q)

        diag = pd.get("diagnostics") or {}
        if diag.get("warnings"):
            failures_with_warnings.append(q)

    # Find repeated terms in failures (potential category blindspots)
    all_failure_queries = [
        r.get("query", "") for r in results
        if not r.get("hit") and not r.get("error")
    ]
    term_counts: Counter[str] = Counter()
    for q in all_failure_queries:
        # Extract significant terms (>3 chars, not common words)
        for term in q.split():
            if len(term) > 3 and term.lower() not in {"the", "and", "for", "with"}:
                term_counts[term] += 1

    blindspot_terms = [
        (term, count) for term, count in term_counts.most_common(10)
        if count >= 3
    ]

    return {
        "failures_by_step": {k: len(v) for k, v in failures_by_step.items()},
        "failures_by_step_detail": {
            k: v[:5] for k, v in failures_by_step.items()
        },
        "failures_with_web_warnings": len(failures_with_warnings),
        "hit_queries": hits[:10],
        "blindspot_terms": blindspot_terms,
    }


# ---------------------------------------------------------------------------
# Anomaly detectors
# ---------------------------------------------------------------------------


def detect_anomalies(
    ctx: CritiqueContext,
    health: dict,
    ranks: dict,
    evolution: dict,
) -> list[AnomalyFlag]:
    """Run all anomaly detectors, return list of flags."""
    flags: list[AnomalyFlag] = []

    # High degradation
    deg_rate = health.get("web_search_degradation_rate", 0)
    if deg_rate > 0.4:
        n = health.get("degraded_count", 0)
        total = health.get("total", 0)
        url_yield = health.get("web_search_url_yield")
        yield_str = f" URL yield: {url_yield:.0%}." if url_yield is not None else ""
        flags.append(AnomalyFlag(
            "high_degradation", "high",
            f"Pipeline stress: {n}/{total} queries had degraded steps.{yield_str}",
        ))

    # Web search mostly failing
    url_yield = health.get("web_search_url_yield")
    if url_yield is not None and url_yield < 0.3:
        flags.append(AnomalyFlag(
            "web_search_failure", "high",
            f"Web search content retrieval rate: {url_yield:.0%}. "
            "Most queries lack web context.",
        ))

    # Near miss pattern
    misses = [r for r in ctx.results if not r.get("hit") and not r.get("error")]
    near_miss_count = len(ranks.get("near_misses", []))
    if misses and near_miss_count / max(len(misses), 1) > 0.3:
        flags.append(AnomalyFlag(
            "near_miss_pattern", "medium",
            f"GT in candidates for {near_miss_count}/{len(misses)} misses "
            "but not ranked first. Ranking quality issue.",
        ))

    # Plateau signal
    plateau = evolution.get("plateau_rounds", 0)
    if plateau >= 2:
        flags.append(AnomalyFlag(
            "plateau_signal", "medium",
            f"Optimization plateau: {plateau} consecutive rounds with <1% improvement.",
        ))

    return flags


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


CRITIQUE_TASK = """\
You are a pipeline optimization analyst. Analyze all data above and return JSON:
{
  "failure_categories": [{"category": "<name>", "count": <n>, "description": "<why>"}],
  "root_cause": "<single most important cause of failures>",
  "pipeline_insights": "<what the pipeline telemetry reveals>",
  "priority_fix": "<single most impactful change to make>",
  "suggested_axes": ["<pipeline_param or prompt_field to try next>"],
  "summary": "<2-3 sentence actionable critique for the next candidate generator>"
}
Rules:
- failure_categories may be empty if accuracy is high
- pipeline_insights should reference specific stats (degradation rate, rank distribution, etc.)
- suggested_axes should be concrete parameter names from the scan context or pipeline config
- summary is the key output — it will be injected into the next generation prompt
"""


def assemble_critique_prompt(ctx: CritiqueContext) -> str:
    """Build the full critique prompt from pre-computed stats."""
    health = compute_pipeline_health(ctx.results)
    ranks = compute_rank_analysis(ctx.results)
    evolution = compute_round_evolution(ctx.round_history)
    categories = compute_query_categories(ctx.results)
    anomalies = detect_anomalies(ctx, health, ranks, evolution)

    sections: list[str] = []

    # --- Summary ---
    total = len(ctx.results)
    sections.append(
        f"## EVALUATION SUMMARY\n"
        f"Accuracy: {ctx.accuracy:.1%} | Composite: {ctx.composite:.4f} | "
        f"Degraded: {ctx.degraded_queries}/{total}\n"
        f"Round {ctx.current_round} | Stall count: {ctx.stall_count} | "
        f"Best so far: {ctx.best_accuracy:.1%} (round {ctx.best_round})"
    )

    # --- Anomaly flags ---
    if anomalies:
        lines = [f"## ANOMALY FLAGS ({len(anomalies)})"]
        for a in anomalies:
            lines.append(f"  [{a.severity.upper()}] {a.name}: {a.message}")
        sections.append("\n".join(lines))

    # --- Pipeline health ---
    if health:
        lines = ["## PIPELINE HEALTH"]
        term_dist = health.get("termination_distribution", {})
        if term_dist:
            lines.append("Termination distribution:")
            for step, count in term_dist.items():
                lines.append(f"  {step}: {count}/{total}")
        deg = health.get("web_search_degradation_rate", 0)
        lines.append(f"Web search degradation: {deg:.0%} of queries")
        url_yield = health.get("web_search_url_yield")
        if url_yield is not None:
            lines.append(f"Web search URL yield: {url_yield:.0%}")
        ts = health.get("timing_stats", {})
        if ts:
            lines.append(
                f"Timing: p50={ts.get('p50_ms', 0):.0f}ms "
                f"p90={ts.get('p90_ms', 0):.0f}ms "
                f"max={ts.get('max_ms', 0):.0f}ms"
            )
        lines.append(f"Error rate: {health.get('error_rate', 0):.0%}")
        sections.append("\n".join(lines))

    # --- Rank analysis ---
    if ranks and ranks.get("rank_distribution"):
        lines = ["## CANDIDATE RANK ANALYSIS"]
        lines.append("Where does ground truth appear in candidate list?")
        for bucket, count in ranks["rank_distribution"].items():
            lines.append(f"  Rank {bucket}: {count}")
        top_k = ranks.get("top_k_recall", {})
        if top_k:
            lines.append("Top-k recall:")
            for k, recall in sorted(top_k.items()):
                lines.append(f"  top-{k}: {recall:.0%}")
        avg_cand = ranks.get("candidate_count_avg", 0)
        lines.append(f"Avg candidates per query: {avg_cand:.1f}")

        near = ranks.get("near_misses", [])
        if near:
            lines.append(f"\nNear misses ({len(near)} queries — GT in candidates but not rank 1):")
            for nm in near:
                lines.append(
                    f"  [{nm['rank']}] {nm['query']} → predicted: {nm['predicted']} "
                    f"(GT: {nm['ground_truth']})"
                )
        sections.append("\n".join(lines))

    # --- Round evolution ---
    if evolution and evolution.get("trajectory"):
        lines = ["## ROUND EVOLUTION"]
        lines.append("Round  Accuracy  Delta   Degraded  Candidates")
        for t in evolution["trajectory"]:
            lines.append(
                f"  {t['round']:>5}  {t['accuracy']:>7.1%}  {t['delta']:>+6.1%}  "
                f"{t['degraded']:>8}  {t['n_candidates']:>10}"
            )
        pc = evolution.get("param_changes", [])
        if pc:
            lines.append("\nPipeline param changes between rounds:")
            for change in pc:
                lines.append(
                    f"  Round {change['from_round']}→{change['to_round']}: "
                    f"{', '.join(change['changed_params'])}"
                )
        sections.append("\n".join(lines))

    # --- Query categories ---
    if categories:
        lines = ["## QUERY CATEGORY ANALYSIS"]
        fbs = categories.get("failures_by_step", {})
        if fbs:
            lines.append("Failures by termination step:")
            for step, count in fbs.items():
                lines.append(f"  {step}: {count}")
                detail = categories.get("failures_by_step_detail", {}).get(step, [])
                for q in detail:
                    lines.append(f"    - {q}")
        ww = categories.get("failures_with_web_warnings", 0)
        if ww:
            lines.append(f"Failures with web search warnings: {ww}")
        blindspots = categories.get("blindspot_terms", [])
        if blindspots:
            lines.append("Potential category blindspots (recurring terms in failures):")
            for term, count in blindspots:
                lines.append(f"  '{term}' appears in {count} failed queries")
        hits = categories.get("hit_queries", [])
        if hits:
            lines.append(f"\nSuccessful queries ({len(hits)}):")
            for q in hits:
                lines.append(f"  ✓ {q}")
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

    # --- Failure details ---
    failures = [r for r in ctx.results if not r.get("hit") and not r.get("error")]
    if failures:
        lines = [f"## FAILURE DETAILS ({len(failures)} queries)"]
        for r in failures[:15]:
            pd = r.get("pipeline_data") or {}
            step = pd.get("terminated_at", "?")
            gt = r.get("ground_truth", "?")
            rank = _find_rank(
                pd.get("ranked_candidates") or pd.get("token_matched_candidates") or [],
                gt,
            )
            rank_str = f"rank {rank}" if rank else "not in candidates"
            diag = pd.get("diagnostics") or {}
            warn = "⚠ degraded" if diag.get("warnings") else ""
            lines.append(
                f"  MISS  [{step}]  {r['query'][:70]}\n"
                f"        → {r.get('predicted', '?')[:70]}\n"
                f"        GT: {gt[:70]}  |  {rank_str}  {warn}"
            )
        sections.append("\n".join(lines))

    # --- Success details ---
    successes = [r for r in ctx.results if r.get("hit")]
    if successes:
        lines = [f"## SUCCESS DETAILS ({len(successes)} queries)"]
        for r in successes[:10]:
            pd = r.get("pipeline_data") or {}
            step = pd.get("terminated_at", "?")
            lines.append(f"  HIT  [{step}]  {r['query'][:70]} → {r.get('predicted', '?')[:70]}")
        sections.append("\n".join(lines))

    # --- Task ---
    sections.append(CRITIQUE_TASK)

    return "\n\n".join(sections)
