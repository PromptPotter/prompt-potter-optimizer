"""Result-extraction helpers shared across the optimization layer.

Pure read-only helpers over a result dict / pipeline schema — no LLM calls,
no I/O. Consumed by elimination, L1 execute, L1 critique, and the notebook
display layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import is_error_result

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


def is_deprecated(result: Mapping[str, Any]) -> bool:
    """True iff result carries any FATAL_WARNINGS code — a deprecated data point.

    Closure over ``FATAL_WARNINGS``; ``shared/errors.is_deprecated_result``
    is the parameterized form. Local import avoids the
    ``elimination → utils → elimination`` module-load cycle.
    """
    from promptpotter.application.optimization.elimination import FATAL_WARNINGS
    from promptpotter.shared.errors import is_deprecated_result

    return is_deprecated_result(result, FATAL_WARNINGS)


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
