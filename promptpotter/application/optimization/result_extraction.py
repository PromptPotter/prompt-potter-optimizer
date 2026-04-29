"""Result-extraction helpers shared across the optimization layer.

Pure read-only helpers over a result dict / pipeline schema — no LLM calls,
no I/O. Consumed by elimination, L1 execute, L1 critique, and the notebook
display layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult

__all__ = [
    "candidate_keys_from_schema",
    "extract_warning_types",
    "get_candidates",
    "is_deprecated",
    "update_query_tracker",
]


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
    """Extract every advisory + fatal code seen on this result.

    Display and tracker callers want the full code list; classification is
    handled separately by :func:`classify_result`.
    """
    from promptpotter.application.optimization.diagnostics import classify_result

    return classify_result(result).all_codes


def is_deprecated(result: Mapping[str, Any]) -> bool:
    """True iff the classifier marked any fatal code — a deprecated data point."""
    from promptpotter.application.optimization.diagnostics import classify_result

    return classify_result(result).is_fatal


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
