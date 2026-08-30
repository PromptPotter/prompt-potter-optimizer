"""Three-bucket verdict. Structural-vs-transient is the BACKEND's, read off the stamped ``WarningKind`` — a warning with no kind is
therefore NOT structural, since guessing eliminates candidates for free. ``infra`` deprecates the sample, so a transient is not one."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.rendering import classify_result
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement


def ranked_item_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.emits_ranking:
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
        if node.emits_ranking:
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


def scoreable_rows(results: list[QueryMeasurement]) -> list[QueryMeasurement]:
    """The EVIDENCE population — rows that carry a verdict. A deprecated row was measured and thrown
    out, an errored one never happened, so neither belongs in a denominator.

    **One definition, because every published rate needs its ``n`` and its mean drawn from the same
    filter** — spelled per call site, a third exclusion added to one leaves the count describing a
    different population than the value beside it, with nothing raised. Load-bearing at L4, where a
    cell is a whole inner campaign: a floored 0.0 there does not read as "scored nothing", it reads
    as "drove the inner loop maximally DOWN". Deliberately NOT applied inside
    ``selection.py::_mean_fitness_by_cell``, whose own docstring says why.
    """
    return [r for r in results if not is_deprecated(r) and not is_error_result(r)]


__all__ = [
    "extract_warning_types",
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
    "scoreable_rows",
    "terminal_ranking",
]
