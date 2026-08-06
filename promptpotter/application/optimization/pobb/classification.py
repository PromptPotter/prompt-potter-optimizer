"""Three-bucket verdict. Structural-vs-transient is the BACKEND's, read off the stamped ``WarningKind`` — a warning with no kind is
therefore NOT structural, since guessing eliminates candidates for free. ``infra`` deprecates the sample, so a transient is not one."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.rendering import classify_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


def ranked_item_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.node_type in ("ranker", "candidate_source"):
            keys.extend(node.output_keys)
    return keys


def get_ranked_items(r: Mapping[str, Any], ranked_item_keys: list[str] | None = None) -> list[Any]:
    pd = r.get("pipeline_data") or {}
    for key in ranked_item_keys or []:
        val: list[Any] | None = pd.get(key)
        if val:
            return val
    return []


def terminal_ranking(r: Mapping[str, Any], schema: PipelineSchema | None) -> list[Any]:
    """The ranked list from the LAST ranker that emitted its key — decided by key PRESENCE, so an empty terminal list is a legitimate
    NO_RESULT and never a fall-through to an earlier node's candidate pool."""
    pd = r.get("pipeline_data") or {}
    if not schema:
        return []
    for node in reversed(schema.nodes):
        if node.node_type in ("ranker", "candidate_source"):
            for key in node.output_keys:
                if key in pd:
                    val = pd[key]
                    return val if isinstance(val, list) else []
    return []


def extract_warning_types(result: Mapping[str, Any]) -> list[str]:
    """Every advisory + fatal code seen on this result, for display and tracking. Classification itself is
    :func:`classify_result`'s."""
    return classify_result(result).all_codes


def is_deprecated(result: Mapping[str, Any]) -> bool:
    """True iff the classifier flagged the sample fatal or infra-truncated. Both deprecate it for accounting, but only
    ``fatal_codes`` participate in one-sighting fast-path elimination."""
    return classify_result(result).is_fatal


__all__ = [
    "extract_warning_types",
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
    "terminal_ranking",
]
