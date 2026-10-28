from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.row_diagnostics import extract_sample_diagnostics
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import RoundResult
from promptpotter.domain.results_health import UNKNOWN_STEP, terminal_node
from promptpotter.domain.round_diagnostics import NearMiss, RoundDiagnostics, SampleDiag
from promptpotter.shared.answer_text import tail
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.domain.scoring import GradedCell

shapes_optimizer_prompt(__name__)

_RANK_BUCKET_KEYS: tuple[str, ...] = ("1", "2-5", "6-10", "11-20", "not_found")
_TOP_K_LEVELS: tuple[int, ...] = (1, 3, 5, 10)


def compute_round_diagnostics(
    round_result: RoundResult, pipeline_schema: PipelineSchema | None
) -> RoundDiagnostics:
    results = round_result.results.cells

    rank_buckets, top_k, near_misses, n_valid = _rank_analysis(results)
    error_rate, warning_rate = _pipeline_health(results)
    samples = _sample_diagnostics(results, pipeline_schema)

    return RoundDiagnostics(
        rank_buckets=rank_buckets,
        top_k_accuracy=top_k,
        near_misses=near_misses,
        n_valid=n_valid,
        error_rate=error_rate,
        warning_rate=warning_rate,
        samples=samples,
    )


def _rank_analysis(
    results: Sequence[GradedCell],
) -> tuple[dict[str, int], dict[int, float], list[NearMiss], int]:
    answered = [r for r in results if not r.facts.errored]
    n_valid = len(answered)
    # A formula reading no ranking leaves absence, never `not_found` and a 0.0 top-k.
    ranked = [r for r in answered if r.grade.n_candidates is not None]
    if not ranked:
        return {}, {}, [], n_valid
    ranks = [r.grade.ground_truth_rank for r in ranked]
    buckets: dict[str, int] = dict.fromkeys(_RANK_BUCKET_KEYS, 0)
    near_misses: list[NearMiss] = []
    for facts, rank in zip((r.facts for r in ranked), ranks, strict=True):
        if rank == 1:
            buckets["1"] += 1
        elif rank is not None and rank <= 10:
            buckets["2-5" if rank <= 5 else "6-10"] += 1
            near_misses.append(
                NearMiss(
                    query=facts.query[:80],
                    ground_truth=facts.ground_truth[:60],
                    rank=rank,
                    predicted=tail(facts.predicted or "?", 60),
                )
            )
        elif rank is not None and rank <= 20:
            buckets["11-20"] += 1
        else:
            buckets["not_found"] += 1
    top_k: dict[int, float] = {}
    for k in _TOP_K_LEVELS:
        in_top_k = sum(1 for rank in ranks if rank is not None and rank <= k)
        top_k[k] = in_top_k / len(ranks)

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
                predicted=tail(facts.predicted or "?", 60),
                rank=r.grade.ground_truth_rank,
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
