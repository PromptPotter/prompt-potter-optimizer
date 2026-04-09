"""Typed schema for per-query evaluation results.

Every evaluation — whether via backend HTTP call or local cache — produces
a ``QueryResult`` dict.  This module defines the structural type so
consumers (metrics, critique, stale-data protocol) can declare and
validate the fields they access.

Construction sites: ``eval_query._build_local_result()``,
``eval_query._build_error_result()``, ``eval_query.evaluate_query()``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class PipelineData(TypedDict, total=False):
    """Nested pipeline execution details within a QueryResult."""

    final_ranking: list[dict[str, Any]]
    total_time: float
    terminated_at: str
    step_timings: dict[str, Any]
    llm_provider: str
    pipeline_params: dict[str, Any]
    diagnostics: dict[str, Any]


class QueryResult(TypedDict):
    """Core per-query evaluation result — always present."""

    query: str
    ground_truth: str
    predicted: str
    hit: bool
    score: float
    error: str | None
    pipeline_data: PipelineData | None


class QueryResultFull(QueryResult, total=False):
    """Extended result with optional fields from eval pipeline and stale-data protocol."""

    # Eval pipeline
    n_candidates: int
    ground_truth_rank: int | None
    precomputed_through: list[str]
    cached: bool

    # Stale-data protocol fields (set by stale_data.py)
    retry_of_degraded: bool
    rerun_comparison: dict[str, Any]
    samplescan_probe: bool
    samplescan_config: dict[str, Any]
    degraded_observed: bool
    degraded_obs_count: int
    degraded_obs_threshold: int
    rerun_prior_outcome: dict[str, Any] | None
    switched_out: bool
    persistently_degraded: bool
