"""Result classification — three-bucket (advisory / infra / fatal) verdict.

Structural-vs-transient is the BACKEND's verdict, read from the source-stamped
``WarningKind`` — one source of truth, two consumers, and no PromptPotter-side code
taxonomy. A warning with no ``kind`` is therefore not structural: absence is not evidence,
and guessing here eliminates candidates for free. Transient codes are likewise not
blanket-routed to ``infra``, because ``infra`` means *deprecate the sample*, which a
transient measurement is not. ``infra_codes`` mark a sample deprecated for accounting but
stay out of the one-sighting fatal fast-path; rate-based degradation still counts them.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.rendering import classify_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


def ranked_item_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    """Derive pipeline_data keys carrying ranked items from schema's ranker/candidate_source nodes."""
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.node_type in ("ranker", "candidate_source"):
            keys.extend(node.output_keys)
    return keys


def get_ranked_items(r: Mapping[str, Any], ranked_item_keys: list[str] | None = None) -> list[Any]:
    """Extract ranked items from a result dict, checking keys in order."""
    pd = r.get("pipeline_data") or {}
    for key in ranked_item_keys or []:
        val: list[Any] | None = pd.get(key)
        if val:
            return val
    return []


def terminal_ranking(r: Mapping[str, Any], schema: PipelineSchema | None) -> list[Any]:
    """The ranked list from the LAST ranker/candidate_source node that emitted its
    key — the pipeline's result ranking, whichever node is terminal. So a pipeline
    ending at ``token_matching`` yields its ``candidate_ranking``; one that re-ranks
    with ``llm_ranking`` yields that node's ``final_ranking`` (later → wins).

    Distinct from :func:`get_ranked_items` (first non-empty across ALL ranker keys,
    for "did GT appear in ANY ranking" analysis): the prediction is the TERMINAL
    ranker's verdict, decided by key *presence* — an empty terminal list is a
    legitimate NO_RESULT, not a fall-through to an earlier node's candidate pool.
    """
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
    """Extract every advisory + fatal code seen on this result.

    Display and tracker callers want the full code list; classification is
    handled separately by :func:`classify_result`.
    """
    return classify_result(result).all_codes


def is_deprecated(result: Mapping[str, Any]) -> bool:
    """True iff the classifier flagged the sample as fatal or infra-truncated.

    Both buckets deprecate the sample for accounting purposes; only
    ``fatal_codes`` (not ``infra_codes``) participate in one-sighting
    fast-path elimination via ``ResultClassification.dominant_fatal``.
    """
    return classify_result(result).is_fatal


__all__ = [
    "extract_warning_types",
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
    "terminal_ranking",
]
