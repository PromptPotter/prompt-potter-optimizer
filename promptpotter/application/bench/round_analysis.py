from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.row_diagnostics import extract_sample_diagnostics
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.results_health import UNKNOWN_STEP, terminal_node
from promptpotter.domain.round_diagnostics import (
    EvolutionRow,
    NearMiss,
    RoundDiagnostics,
    SampleDiag,
    TrendClass,
)
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.scoring import GradedCell

shapes_optimizer_prompt(__name__)

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
    """*rounds_history* MUST hold *round_result* as its LAST element."""
    results = round_result.results.cells

    rank_buckets, top_k, near_misses, n_valid = _rank_analysis(results)
    error_rate, warning_rate = _pipeline_health(results)
    evolution_rows, anomalies = _evolution(rounds_history)
    trend, trend_desc = _trend(rounds_history)
    diff_lines = _cross_candidate_diff(round_result)
    samples = _sample_diagnostics(results, pipeline_schema)

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
        samples=samples,
    )


def _rank_analysis(
    results: Sequence[GradedCell],
) -> tuple[dict[str, int], dict[int, float], list[NearMiss], int]:
    answered = [r.facts for r in results if not r.facts.errored]
    n_valid = len(answered)
    # A rank is a position against a LABEL: with none, absence, never `not_found` and a 0.0 top-k.
    if all_verifier_graded(r.facts.ground_truth for r in results):
        return {}, {}, [], n_valid
    ranks = [facts.ground_truth_rank for facts in answered]
    buckets: dict[str, int] = dict.fromkeys(_RANK_BUCKET_KEYS, 0)
    near_misses: list[NearMiss] = []
    for facts, rank in zip(answered, ranks, strict=True):
        if rank == 1:
            buckets["1"] += 1
        elif rank is not None and rank <= 10:
            buckets["2-5" if rank <= 5 else "6-10"] += 1
            near_misses.append(
                NearMiss(
                    query=facts.query[:80],
                    ground_truth=facts.ground_truth[:60],
                    rank=rank,
                    predicted=(facts.predicted or "?")[:60],
                )
            )
        elif rank is not None and rank <= 20:
            buckets["11-20"] += 1
        else:
            buckets["not_found"] += 1
    top_k: dict[int, float] = {}
    if n_valid:
        for k in _TOP_K_LEVELS:
            in_top_k = sum(1 for rank in ranks if rank is not None and rank <= k)
            top_k[k] = in_top_k / n_valid

    return buckets, top_k, near_misses, n_valid


def _pipeline_health(results: Sequence[GradedCell]) -> tuple[float, float]:
    total = len(results)
    if not total:
        return 0.0, 0.0
    warning_count = 0
    error_count = 0
    for r in results:
        if r.facts.pipeline.diagnostics.warnings:
            warning_count += 1
        if r.facts.errored:
            error_count += 1
    return error_count / total, warning_count / total


def _evolution(rounds: list[RoundResult]) -> tuple[list[EvolutionRow], list[str]]:
    if not rounds:
        return [], []
    rows: list[EvolutionRow] = []
    plateau_run = 0
    max_plateau = 0
    prev_acc: float | None = None
    for r in rounds:
        # An unreadable round breaks the series: a 0.0 delta would count as a flat round below.
        delta = (r.accuracy - prev_acc) if prev_acc is not None and r.accuracy is not None else None
        rows.append(
            EvolutionRow(
                round=r.round,
                accuracy=r.accuracy,
                delta=delta,
                degraded=r.degraded_samples,
                n_candidates=len(r.candidate_scores),
                elected=bool(r.improved),
            )
        )
        plateau_run = plateau_run + 1 if delta is not None and abs(delta) < _PLATEAU_DELTA else 0
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
    """Elections, never the accuracy series: subsets are re-picked at the leader's θ, so accuracy drifts."""
    if len(rounds) < 3:
        return "healthy", "Too few rounds to classify"
    elected = [bool(r.improved) for r in rounds]
    total = sum(elected)
    recent = elected[-5:]
    if not total:
        return (
            "plateau",
            f"No round has elected — 0 of {len(rounds)} cleared the parent",
        )
    since = len(elected) - 1 - max(i for i, won in enumerate(elected) if won)
    if sum(recent) >= len(recent) * 0.5:
        return (
            "healthy",
            f"Electing — {sum(recent)}/{len(recent)} recent rounds cleared the parent",
        )
    if since >= 5:
        return "ceiling", f"{since} rounds since the last election ({total} in total)"
    return "plateau", f"{since} rounds since the last election ({total} in total)"


def _cross_candidate_diff(round_result: RoundResult) -> list[str]:
    winner_results = round_result.results
    all_results = round_result.all_candidate_results
    if not winner_results or len(all_results) < 2:
        return []

    winner_misses = {r.sample_id for r in winner_results if not r.hit}
    solvers: dict[int, int] = {}
    for results in all_results.values():
        for r in results:
            if r.sample_id in winner_misses and r.hit:
                solvers[r.sample_id] = solvers.get(r.sample_id, 0) + 1
    ranked = sorted(solvers.items(), key=lambda kv: -kv[1])
    return [f"  #{sid} — solved by {n} other candidate(s)" for sid, n in ranked[:5]]


def _sample_diagnostics(
    results: Sequence[GradedCell],
    pipeline_schema: PipelineSchema | None,
) -> list[SampleDiag]:
    out: list[SampleDiag] = []
    for r in results:
        facts, fitness = r.facts, r.grade.fitness
        if facts.errored or fitness is None:
            continue
        sd: dict[str, Any] | None = None
        if pipeline_schema is not None:
            sd = extract_sample_diagnostics(facts, pipeline_schema)
        out.append(
            SampleDiag(
                query=facts.query[:80],
                ground_truth=facts.ground_truth[:60],
                predicted=(facts.predicted or "?")[:60],
                rank=facts.ground_truth_rank,
                terminal_node=terminal_node(facts) or UNKNOWN_STEP,
                gt_in_source=(sd or {}).get("gt_in_source"),
                gt_in_ranked=(sd or {}).get("gt_in_ranked"),
                warnings=[
                    f"{w.step or 'unknown'}:{w.code or 'unknown'}"
                    for w in facts.pipeline.diagnostics.warnings
                ],
                # CORRECTNESS, never the composite: `panels.py::_r_diagnostics` thresholds it with `is_hit`.
                fitness=float(fitness),
            )
        )
    return out
