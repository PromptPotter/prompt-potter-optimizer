"""Compute ``RoundDiagnostics`` from a freshly-completed round — a pure function over already-computed scoring data."""

from __future__ import annotations

import logging
from typing import Any

from promptpotter.application.optimization.pobb.classification import (
    get_ranked_items,
    ranked_item_keys_from_schema,
)
from promptpotter.application.scoring.diagnostics import (
    extract_sample_diagnostics,
    rank_ground_truth,
)
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_diagnostics import (
    EvolutionRow,
    NearMiss,
    RoundDiagnostics,
    SampleDiag,
    TrendClass,
)
from promptpotter.domain.scoring import all_verifier_graded, is_hit
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
) -> RoundDiagnostics:
    """Compute typed deterministics over a completed round. *rounds_history* MUST contain *round_result* as its LAST
    element — callers fold the round into ``cycle.rounds`` before computing diagnostics."""
    ranked_item_keys = ranked_item_keys_from_schema(pipeline_schema)
    results = round_result.results

    rank_buckets, top_k, near_misses, n_valid = _rank_analysis(results, ranked_item_keys)
    error_rate, warning_rate = _pipeline_health(results)
    evolution_rows, anomalies = _evolution(rounds_history)
    trend, trend_desc = _trend(rounds_history)
    diff_lines = _cross_candidate_diff(round_result)
    samples = _sample_diagnostics(results, ranked_item_keys, pipeline_schema)

    return RoundDiagnostics(
        rank_buckets=rank_buckets,
        top_k_accuracy=top_k,
        near_misses=near_misses,
        n_valid=n_valid,
        error_rate=error_rate,
        warning_rate=warning_rate,
        evolution_rows=evolution_rows,
        trend=trend,
        trend_description=trend_desc,
        anomalies=anomalies,
        cross_candidate_diff=diff_lines,
        l1_diversity=float(round_result.l1_yield),
        samples=samples,
    )


# ---------------------------------------------------------------------------
# Section helpers — each returns the typed slice it owns.
# ---------------------------------------------------------------------------


def _rank_analysis(
    results: list[dict[str, Any]], ranked_item_keys: list[str] | None
) -> tuple[dict[str, int], dict[int, float], list[NearMiss], int]:
    # A rank is a POSITION AGAINST A LABEL. With none, every walk below returns `None`, so every
    # row lands in `not_found` and every top-k reads 0.0 — measured on a Harbor origin that solved
    # eight of ten, this banked `not_found: 10` and `top-1: 0.0` into `RoundResult.diagnostics`.
    # Absence, not zero: the empty dicts are what `RoundDiagnostics` defaults to, and the panel
    # that reads them already self-suppresses on an undiscriminating distribution.
    # `n_valid` still answers — it counts rows that were measured, which is not a rank claim.
    if all_verifier_graded(r.get("ground_truth") for r in results):
        return {}, {}, [], sum(1 for r in results if not is_error_result(r))
    keys = ranked_item_keys or None
    rank_map: dict[int, int | None] = {
        i: rank_ground_truth(
            get_ranked_items(r, keys), r.get("predicted") or "", r.get("ground_truth", "")
        )[0]
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
    n_valid = sum(1 for r in results if not is_error_result(r))
    top_k: dict[int, float] = {}
    if n_valid:
        for k in _TOP_K_LEVELS:
            in_top_k = sum(1 for rank in rank_map.values() if rank is not None and rank <= k)
            top_k[k] = in_top_k / n_valid

    return buckets, top_k, near_misses, n_valid


def _pipeline_health(results: list[dict[str, Any]]) -> tuple[float, float]:
    """Rates only — ``terminal_node`` is not a health signal and must not be tallied here
    (``domain/round_diagnostics.py::RoundDiagnostics``)."""
    total = len(results)
    if not total:
        return 0.0, 0.0
    warning_count = 0
    error_count = 0
    for r in results:
        diag = (r.get("pipeline_data") or {}).get("diagnostics") or {}
        if diag.get("warnings"):
            warning_count += 1
        if is_error_result(r):
            error_count += 1
    return error_count / total, warning_count / total


def _warning_str(w: object) -> str:
    """Normalize a pipeline warning to ``step:code``. Pipelines emit either dicts or bare strings; both shapes must reach
    the same downstream surface."""
    if isinstance(w, dict):
        return f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}"
    return str(w)


def _evolution(rounds: list[RoundResult]) -> tuple[list[EvolutionRow], list[str]]:
    """Per-round evolution rows + plateau detection, which fires when enough consecutive deltas fall under the threshold."""
    if not rounds:
        return [], []
    rows: list[EvolutionRow] = []
    plateau_run = 0
    max_plateau = 0
    prev_acc: float | None = None
    for r in rounds:
        delta = (r.accuracy - prev_acc) if prev_acc is not None else 0.0
        rows.append(
            EvolutionRow(
                round=r.round,
                accuracy=r.accuracy,
                delta=delta,
                degraded=r.degraded_samples,
                n_candidates=len(r.candidate_scores),
            )
        )
        plateau_run = plateau_run + 1 if abs(delta) < _PLATEAU_DELTA else 0
        max_plateau = max(max_plateau, plateau_run)
        prev_acc = r.accuracy

    anomalies: list[str] = []
    if max_plateau >= _PLATEAU_FLAG_THRESHOLD:
        anomalies.append(
            f"[MEDIUM] plateau_signal: {max_plateau} consecutive rounds with "
            f"<{_PLATEAU_DELTA:.0%} improvement."
        )
    return rows, anomalies


def _trend(rounds: list[RoundResult]) -> tuple[TrendClass, str]:
    """Classify the cycle's accuracy series — the SOLE owner of that decision. Returns the typed class
    plus a one-line description suitable for direct prompt inclusion."""
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
    """Queries other candidates hit but the winner missed. Empty when fewer than two candidates ran, or the winner solved
    everything."""
    winner_results = round_result.results
    all_results = round_result.all_candidate_results
    candidate_scores = round_result.candidate_scores
    if not winner_results or len(all_results) < 2:
        return []

    winner_misses: set[str] = {
        r.get("query", "")
        for r in winner_results
        if not is_hit(r.get("fitness")) and r.get("query")
    }
    if not winner_misses:
        return []

    missed_by: dict[str, list[str]] = {}
    for cand_id, results in all_results.items():
        desc = cand_id
        for cs in candidate_scores:
            if cs.candidate_id == cand_id:
                desc = (cs.changes_description or cand_id)[:60]
                break
        for r in results:
            q = r.get("query", "")
            if q in winner_misses and is_hit(r.get("fitness")):
                missed_by.setdefault(q, []).append(desc)

    if not missed_by:
        return []
    sorted_missed = sorted(missed_by.items(), key=lambda x: -len(x[1]))
    return [
        f"  {q[:60]} — solved by {len(candidates)} other candidate(s)"
        for q, candidates in sorted_missed[:5]
    ]


def _sample_diagnostics(
    results: list[dict[str, Any]],
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
        rank, _ = rank_ground_truth(
            get_ranked_items(r, ranked_item_keys),
            r.get("predicted") or "",
            r.get("ground_truth", ""),
        )
        sd: dict[str, Any] | None = None
        if pipeline_schema is not None:
            sd = extract_sample_diagnostics(r, pipeline_schema)
        out.append(
            SampleDiag(
                query=r.get("query", "")[:80],
                ground_truth=(r.get("ground_truth") or "")[:60],
                predicted=(r.get("predicted") or "?")[:60],
                rank=rank,
                terminal_node=pd.get("terminal_node", "unknown"),
                gt_in_source=(sd or {}).get("gt_in_source"),
                gt_in_ranked=(sd or {}).get("gt_in_ranked"),
                warnings=[_warning_str(w) for w in (diag.get("warnings") or ())],
                # CORRECTNESS. Its one reader thresholds it with `is_hit`
                # (`panels.py::_r_diagnostics`), which `CellScorer` declares a predicate on
                # `fitness`; `graded_response` here fed it the composite instead, so under any
                # `per_cell` penalty a correct-but-costly cell rendered to `l1_critique` as a MISS.
                fitness=float(r["fitness"]),
            )
        )
    return out
