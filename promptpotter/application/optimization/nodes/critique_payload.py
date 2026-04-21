"""Shared round-result helpers: :class:`RoundSnapshot` + result extractors.

``RoundSnapshot`` is the critique agent's input packet; the four free
functions (``extract_warning_types``, ``update_query_tracker``,
``candidate_keys_from_schema``, ``get_candidates``) are consumed by escalation,
round-execution, notebook display, and the critique agent itself.

The prompt-section builders and ``assemble_critique_sections`` used to live
here as private helpers for a single caller — they now live in ``critique.py``
where they belong.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.nodes.formatting import (
    build_cross_candidate_diff,
    build_trajectory_report,
)
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.optimization.loop_state import LoopState
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult

    from .score import L1ScoringResult

__all__ = [
    "RoundSnapshot",
    "candidate_keys_from_schema",
    "extract_warning_types",
    "get_candidates",
    "update_query_tracker",
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

    # Schema-driven candidate keys (from PipelineNode.output_keys for ranker/candidate_source nodes)
    candidate_keys: list[str] = field(default_factory=list)
    pipeline_schema: PipelineSchema | None = None

    degradation_threshold: float = 0.4
    near_miss_ratio: float = 0.3

    search_memory_digest: dict | None = None
    round_analysis: dict[str, str] = field(default_factory=dict)

    runtime_failures: list[dict] = field(default_factory=list)

    @classmethod
    def from_round_state(
        cls,
        state: LoopState,
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
        trajectory = build_trajectory_report(state.rounds)
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
                for r in state.rounds
            ],
            current_round=round_num,
            l1_stall_count=state.escalation.l1_stall_count,
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            pipeline_params=(state.current_sp.pipeline_params if state.current_sp else None),
            candidate_keys=candidate_keys_from_schema(schema),
            pipeline_schema=schema,
            degradation_threshold=config.optimization.critique_degradation_threshold,
            near_miss_ratio=config.optimization.critique_near_miss_ratio,
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
        )


def candidate_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    """Derive pipeline_data candidate keys from schema's ranker/candidate_source nodes."""
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.node_type in ("ranker", "candidate_source"):
            keys.extend(node.output_keys)
    return keys


def get_candidates(r: Mapping[str, Any], candidate_keys: list[str] | None = None) -> list:
    """Extract candidates from a result dict, checking keys in order."""
    pd = r.get("pipeline_data") or {}
    for key in candidate_keys or []:
        val = pd.get(key)
        if val:
            return val
    return []


def extract_warning_types(result: Mapping[str, Any]) -> list[str]:
    """Extract warning type strings from a single eval result."""
    pd = result.get("pipeline_data") or {}
    diag = pd.get("diagnostics") or {}
    types: list[str] = []
    for w in diag.get("warnings") or []:
        if isinstance(w, dict):
            types.append(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
        elif isinstance(w, str):
            types.append(w)
    if not types and is_error_result(result):
        terminated = pd.get("terminated_at", "unknown")
        types.append(f"{terminated}:error")
    return types


def update_query_tracker(
    tracker: dict[str, dict],
    results: list[QueryResult],
) -> None:
    """Merge results into the per-query warning inventory (mutates tracker)."""
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
