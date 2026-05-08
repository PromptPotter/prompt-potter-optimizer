"""Compute :class:`RoundDiagnostics` from a freshly-completed round.

Pure function over already-computed scoring data — rank analysis,
pipeline-health distributions, failure categorization, cycle evolution,
trajectory classification, cross-candidate diff, per-sample diagnostics.
Replaces the inline ``compile_critique_context`` + ``_section_l1c_*``
compute paths that today live across ``dispatch.py`` and ``l1_critique.py``.
"""

from __future__ import annotations

import logging
from collections import Counter

from promptpotter.application.optimization.elimination import (
    get_ranked_items,
    ranked_item_keys_from_schema,
)
from promptpotter.application.scoring.metrics import extract_sample_diagnostics, find_rank
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_diagnostics import (
    EvolutionRow,
    NearMiss,
    ProbeOutcome,
    RoundDiagnostics,
    SampleDiag,
    TrajectoryClass,
)
from promptpotter.shared.errors import is_error_result

__all__ = ["compute_round_diagnostics"]

logger = logging.getLogger(__name__)

_RANK_BUCKET_KEYS: tuple[str, ...] = ("1", "2-5", "6-10", "11-20", "not_found")
_TOP_K_LEVELS: tuple[int, ...] = (1, 3, 5, 10)
_PLATEAU_DELTA: float = 0.01
_PLATEAU_FLAG_THRESHOLD: int = 2


def compute_round_diagnostics(
    round_result: RoundResult,
    rounds_history: list[RoundResult],
    pipeline_schema: PipelineSchema | None,
    *,
    prompt_chars: int = 0,
    probe_just_completed: bool = False,
    axis_tested: str = "",
    prior_full_accuracy: float = 0.0,
) -> RoundDiagnostics:
    """Compute typed deterministics over a completed round.

    *rounds_history* MUST contain *round_result* as its last element —
    callers fold the round into ``cycle.rounds`` before computing
    diagnostics. ``probe_just_completed`` opts the round into a
    ``ProbeOutcome`` summary computed from the warned-query subset
    accuracy vs. the pre-probe full-set best.
    """
    ranked_item_keys = ranked_item_keys_from_schema(pipeline_schema)
    results = round_result.results or []

    rank_buckets, top_k, near_misses, nm_queries, n_valid = _rank_analysis(
        results, ranked_item_keys
    )
    termination_dist, error_rate, warning_rate = _pipeline_health(results)
    evolution_rows, plateau_count, anomalies = _evolution(rounds_history)
    trajectory, trajectory_desc = _trajectory(rounds_history)
    diff_lines = _cross_candidate_diff(round_result)
    samples = _sample_diagnostics(results, ranked_item_keys, pipeline_schema)
    probe_outcome: ProbeOutcome | None = None
    if probe_just_completed:
        subset_size = len(results)
        probe_outcome = ProbeOutcome(
            axis_tested=axis_tested or "unspecified",
            target_subset_size=subset_size,
            hit_rate=round_result.accuracy,
            delta_vs_full=round_result.accuracy - prior_full_accuracy,
        )

    return RoundDiagnostics(
        rank_buckets=rank_buckets,
        top_k_accuracy=top_k,
        near_misses=near_misses,
        near_miss_queries=frozenset(nm_queries),
        n_valid=n_valid,
        termination_dist=termination_dist,
        error_rate=error_rate,
        warning_rate=warning_rate,
        evolution_rows=evolution_rows,
        trajectory=trajectory,
        trajectory_description=trajectory_desc,
        plateau_count=plateau_count,
        anomalies=anomalies,
        cross_candidate_diff=diff_lines,
        l1_diversity=float(round_result.l1_yield or 1.0),
        cache_share=0.0,
        prompt_chars=prompt_chars,
        samples=samples,
        probe_outcome=probe_outcome,
    )


# ---------------------------------------------------------------------------
# Section helpers — each returns the typed slice it owns.
# ---------------------------------------------------------------------------


def _rank_analysis(
    results: list[dict], ranked_item_keys: list[str] | None
) -> tuple[dict[str, int], dict[int, float], list[NearMiss], set[str], int]:
    """Where did ground truth land in each sample's ranked-item list?"""
    keys = ranked_item_keys or None
    rank_map: dict[int, int | None] = {
        i: find_rank(get_ranked_items(r, keys), r.get("ground_truth", ""))
        for i, r in enumerate(results)
        if not is_error_result(r)
    }
    buckets: dict[str, int] = dict.fromkeys(_RANK_BUCKET_KEYS, 0)
    near_misses: list[NearMiss] = []
    for i, r in enumerate(results):
        if is_error_result(r):
            continue
        rank = rank_map.get(i)
        if rank == 1:
            buckets["1"] += 1
        elif rank is not None and rank <= 10:
            buckets["2-5" if rank <= 5 else "6-10"] += 1
            near_misses.append(
                NearMiss(
                    query=r["query"][:80],
                    ground_truth=(r.get("ground_truth") or "")[:60],
                    rank=rank,
                    predicted=(r.get("predicted") or "?")[:60],
                )
            )
        elif rank is not None and rank <= 20:
            buckets["11-20"] += 1
        else:
            buckets["not_found"] += 1
    nm_queries = {nm.query for nm in near_misses}

    n_valid = sum(1 for r in results if not is_error_result(r))
    top_k: dict[int, float] = {}
    if n_valid:
        for k in _TOP_K_LEVELS:
            in_top_k = sum(1 for rank in rank_map.values() if rank is not None and rank <= k)
            top_k[k] = in_top_k / n_valid

    return buckets, top_k, near_misses, nm_queries, n_valid


def _pipeline_health(results: list[dict]) -> tuple[dict[str, int], float, float]:
    """Termination distribution + error/warning rates."""
    total = len(results)
    if not total:
        return {}, 0.0, 0.0
    termination: Counter[str] = Counter()
    warning_count = 0
    error_count = 0
    for r in results:
        pd = r.get("pipeline_data") or {}
        diag = pd.get("diagnostics") or {}
        if diag.get("warnings"):
            warning_count += 1
        termination[pd.get("terminated_at", "unknown")] += 1
        if is_error_result(r):
            error_count += 1
    return dict(termination), error_count / total, warning_count / total


def _warning_str(w: object) -> str:
    """Normalize a pipeline warning entry to ``step:code``.

    Pipelines emit warnings as either dicts (``{"step", "code", ...}``) or
    bare strings; keep both shapes flowing to the same downstream surface.
    Mirrors ``elimination._collect_advisories``.
    """
    if isinstance(w, dict):
        return f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}"
    return str(w)


def _evolution(rounds: list[RoundResult]) -> tuple[list[EvolutionRow], int, list[str]]:
    """Per-round evolution rows + plateau detection.

    Plateau anomaly fires when ≥ ``_PLATEAU_FLAG_THRESHOLD`` consecutive
    deltas fall under :data:`_PLATEAU_DELTA`.
    """
    if not rounds:
        return [], 0, []
    rows: list[EvolutionRow] = []
    plateau_run = 0
    max_plateau = 0
    prev_acc: float | None = None
    prev_pp: dict | None = None
    for r in rounds:
        delta = (r.accuracy - prev_acc) if prev_acc is not None else 0.0
        changed_axes: list[str] = []
        cur_pp = r.pipeline_params or {}
        if prev_pp is not None:
            changed_axes = sorted(
                k
                for k in set(prev_pp) | set(cur_pp)
                if prev_pp.get(k) != cur_pp.get(k) and k != "steps"
            )
        rows.append(
            EvolutionRow(
                round=r.round,
                accuracy=r.accuracy,
                delta=delta,
                degraded=r.degraded_samples,
                n_candidates=len(r.candidate_scores or ()),
                changed_axes=changed_axes,
            )
        )
        plateau_run = plateau_run + 1 if abs(delta) < _PLATEAU_DELTA else 0
        max_plateau = max(max_plateau, plateau_run)
        prev_acc = r.accuracy
        prev_pp = cur_pp

    anomalies: list[str] = []
    if max_plateau >= _PLATEAU_FLAG_THRESHOLD:
        anomalies.append(
            f"[MEDIUM] plateau_signal: {max_plateau} consecutive rounds with "
            f"<{_PLATEAU_DELTA:.0%} improvement."
        )
    return rows, max_plateau, anomalies


def _trajectory(rounds: list[RoundResult]) -> tuple[TrajectoryClass, str]:
    """Classify cycle trajectory from the accuracy series.

    Mirrors :func:`build_trajectory_report` decisions (stall, oscillation,
    ceiling) but returns the typed enum + a one-line description suitable
    for direct prompt inclusion.
    """
    if len(rounds) < 2:
        return "healthy", "Too few rounds to classify"
    accuracies = [r.accuracy for r in rounds]
    best_acc = max(accuracies)
    best_round = accuracies.index(best_acc)
    rounds_since_best = len(accuracies) - 1 - best_round

    deltas = [accuracies[i] - accuracies[i - 1] for i in range(1, len(accuracies))]
    recent = deltas[-5:]
    improvements = sum(1 for d in recent if d > 0.005)
    regressions = sum(1 for d in recent if d < -0.005)
    flat = len(recent) - improvements - regressions

    stall = 0
    for d in reversed(deltas):
        if abs(d) < _PLATEAU_DELTA:
            stall += 1
        else:
            break

    if len(rounds) < 3:
        return "healthy", "Too few rounds to classify"

    if improvements >= len(recent) * 0.5 and regressions <= 1:
        return "healthy", f"Improving — {improvements}/{len(recent)} recent rounds improved"
    if rounds_since_best >= 5 and stall >= 3:
        return (
            "ceiling",
            f"Hard ceiling at {best_acc:.1%} (round {best_round}) — "
            f"{rounds_since_best} rounds without new best",
        )
    if improvements > 0 and regressions > 0 and abs(improvements - regressions) <= 1:
        return (
            "oscillating",
            f"Oscillating — {improvements} improvements, {regressions} regressions "
            f"in last {len(recent)} rounds",
        )
    if flat >= len(recent) * 0.6 or stall >= 3:
        return (
            "plateau",
            f"Plateau — {stall} consecutive rounds with <{_PLATEAU_DELTA:.0%} change, "
            f"gap to best: {best_acc - accuracies[-1]:.1%}",
        )
    return "healthy", "Mixed progress — no clear pattern"


def _cross_candidate_diff(round_result: RoundResult) -> list[str]:
    """Surface queries other candidates hit but the winner missed.

    Mirrors :func:`build_cross_candidate_diff` but returns line list
    rather than a single concatenated string. Empty when fewer than two
    candidates ran or the winner solved everything.
    """
    winner_results = round_result.results or []
    all_results = round_result.all_candidate_results or {}
    candidate_scores = round_result.candidate_scores or []
    if not winner_results or len(all_results) < 2:
        return []

    winner_misses: set[str] = {
        r.get("query", "") for r in winner_results if not r.get("hit") and r.get("query")
    }
    if not winner_misses:
        return []

    missed_by: dict[str, list[str]] = {}
    for cand_id, results in all_results.items():
        desc = cand_id
        for cs in candidate_scores:
            if cs.get("label") == cand_id or str(cs.get("idx")) == cand_id:
                desc = cs.get("changes_description", cand_id)[:60]
                break
        for r in results:
            q = r.get("query", "")
            if q in winner_misses and r.get("hit"):
                missed_by.setdefault(q, []).append(desc)

    if not missed_by:
        return []
    sorted_missed = sorted(missed_by.items(), key=lambda x: -len(x[1]))
    return [
        f"  {q[:60]} — solved by {len(candidates)} other candidate(s)"
        for q, candidates in sorted_missed[:5]
    ]


def _sample_diagnostics(
    results: list[dict],
    ranked_item_keys: list[str] | None,
    pipeline_schema: PipelineSchema | None,
) -> list[SampleDiag]:
    """Per-sample tactical view (≤8 actionable misses, capped for token budget)."""
    out: list[SampleDiag] = []
    for r in results:
        if is_error_result(r):
            continue
        pd = r.get("pipeline_data") or {}
        diag = pd.get("diagnostics") or {}
        rank = find_rank(get_ranked_items(r, ranked_item_keys), r.get("ground_truth", ""))
        sd: dict | None = None
        if pipeline_schema is not None:
            sd = extract_sample_diagnostics(r, pipeline_schema)
        out.append(
            SampleDiag(
                query=r.get("query", "")[:80],
                ground_truth=(r.get("ground_truth") or "")[:60],
                predicted=(r.get("predicted") or "?")[:60],
                rank=rank,
                terminated_at=pd.get("terminated_at", "unknown"),
                gt_in_source=(sd or {}).get("gt_in_source"),
                gt_in_ranked=(sd or {}).get("gt_in_ranked"),
                warnings=[_warning_str(w) for w in (diag.get("warnings") or ())],
                hit=bool(r.get("hit")),
            )
        )
    return out
