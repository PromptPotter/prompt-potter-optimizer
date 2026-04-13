"""Critique agent — LLM analysis of a round's results.

``CritiqueAgent`` wraps the critique LLM call; the pure stat-section
helpers that build its prompt live in ``critique_formatting.py``.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence import load_variant_library
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import LoopConfig
    from promptpotter.application.optimization.loop_state import LoopState
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult
    from promptpotter.infrastructure.llm.client import LLMClientBase

    from .score import L1ScoringResult

logger = logging.getLogger(__name__)

__all__ = [
    "CritiqueAgent",
    "RoundSnapshot",
    "candidate_keys_from_schema",
    "extract_warning_types",
    "format_critique_for_prompt",
    "get_candidates",
    "sample_thinking_styles",
    "update_query_tracker",
]


@dataclass
class RoundSnapshot:
    """Bundles current-round results and round history for diagnostic analysis."""

    results: list[QueryResult]
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

    # SearchMemory context
    search_memory_digest: dict | None = None

    @classmethod
    def from_round_state(
        cls,
        state: LoopState,
        scoring_result: L1ScoringResult,
        config: LoopConfig,
        *,
        round_num: int,
        search_memory_digest: dict | None = None,
    ) -> RoundSnapshot:
        """Build from loop state, scoring result, and config.

        Computes cross-candidate diff and trajectory classification,
        enriching the search_memory_digest dict in-place.
        """
        from promptpotter.application.optimization.nodes.formatting import (
            build_cross_candidate_diff,
            build_trajectory_report,
        )

        # Enrich SM context with cross-candidate diff + trajectory
        sm_ctx = search_memory_digest
        diff = build_cross_candidate_diff(
            cast(list[dict], scoring_result.winner_results),
            cast("dict[str, list[dict]]", scoring_result.all_candidate_results),
            scoring_result.candidate_scores,
        )
        trajectory = build_trajectory_report(state.rounds)
        traj_str = (
            f"{trajectory.classification}: {trajectory.description}"
            if trajectory and trajectory.classification != "healthy"
            else None
        )
        if diff or traj_str:
            if sm_ctx is None:
                sm_ctx = {}
            if diff:
                sm_ctx["cross_candidate_diff"] = diff
            if traj_str:
                sm_ctx["trajectory"] = traj_str

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
            stall_count=state.stall_count,
            best_accuracy=state.best_accuracy,
            best_round=state.best_round,
            pipeline_params=(state.current_sp.pipeline_params if state.current_sp else None),
            candidate_keys=candidate_keys_from_schema(config.pipeline_schema),
            pipeline_schema=config.pipeline_schema,
            degradation_threshold=config.critique_degradation_threshold,
            near_miss_ratio=config.critique_near_miss_ratio,
            search_memory_digest=sm_ctx,
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
    # Fallback: synthesize warning from error field when diagnostics empty
    if not types and is_error_result(result):
        terminated = pd.get("terminated_at", "unknown")
        types.append(f"{terminated}:error")
    return types


def update_query_tracker(
    tracker: dict[str, dict],
    results: list[QueryResult],
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

    async def run(self, ctx: RoundSnapshot) -> dict:
        """Build critique from pipeline stats + LLM analysis.

        Returns dict with keys: positive_critique, negative_critique,
        priority_fix, suggested_axes, failure_highlights, summary.
        """
        from .critique_formatting import assemble_critique_sections

        sections = assemble_critique_sections(ctx)
        _compile_vars = {"stat_sections": sections}
        _template = load_optimizer_prompt("critique")
        prompt = _template.compile_prompt(**_compile_vars)
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
            trace_meta={
                "template_name": "critique",
                "template_fields": _template.prompt_field_dict(),
                "variables": _compile_vars,
            },
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
