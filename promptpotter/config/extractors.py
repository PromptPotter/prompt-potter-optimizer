"""Backend extractors — per-backend query parsing and ground truth extraction.

Each extractor self-registers into the dictionaries below.
Core services dispatch through these registries — they never import
an extractor function directly.  Currently only TermNorm is registered.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# -- Registries ---------------------------------------------------------------

EXPERIMENT_EXTRACTORS: dict[str, Callable[[dict], tuple[list[dict], list[str]]]] = {}
"""Backend experiment data → ``(queries, index_terms)``.

Keyed by ``pipeline_schema.name.lower()``.
"""

TRACE_GT_RESOLVERS: dict[str, Callable[[dict, str], str | None]] = {}
"""Resolve ground truth for a single query string from experiment data.

Signature: ``(experiment_extract, query_str) -> ground_truth | None``.
Keyed by ``pipeline_schema.name.lower()``.
"""

# TermNorm extractor — query: ``bom_material / process``; GT: ``dataset_entry`` from mappings.


def split_query(query: str) -> tuple[str, str]:
    """Split ``"bom_material / process"`` → ``(bom_material, process)``.

    If no slash is present, process is an empty string.
    """
    if "/" in query:
        last_slash = query.rfind("/")
        primary = query[:last_slash].strip()
        secondary = query[last_slash + 1 :].strip()
    else:
        primary = query.strip()
        secondary = ""
    return primary, secondary


def build_query_item(query: str, ground_truth: str = "") -> dict[str, Any]:
    """Build a query dict with TermNorm bom_material/process fields."""
    primary, secondary = split_query(query)
    item: dict[str, Any] = {
        "query": query,
        "bom_material": primary,
        "process": secondary,
        "query_fields": {"bom_material": primary, "process": secondary},
    }
    if ground_truth:
        item["ground_truth"] = ground_truth
    return item


def extract_index_terms(experiment_data: dict) -> list[str]:
    """Extract unique non-empty ``dataset_entry`` values from mappings."""
    entries = set()
    for m in experiment_data.get("mappings", []):
        entry = m.get("dataset_entry", "").strip()
        if entry and entry != "--":
            entries.add(entry)
    return sorted(entries)


def extract_ground_truth_map(experiment_data: dict) -> dict[str, str]:
    """Build ``{bom_material: ground_truth}`` from experiment mappings."""
    gt_map: dict[str, str] = {}
    for m in experiment_data.get("mappings", []):
        bom = m.get("bom_material", "")
        entry = m.get("dataset_entry", "").strip()
        if bom and entry and entry != "--":
            gt_map[bom] = entry
    return gt_map


def extract_queries(experiment_data: dict) -> list[dict[str, Any]]:
    """Extract queries with valid ground truth from experiment data.

    Joins evaluation_result queries to mappings via bom_material.
    """
    gt_map = extract_ground_truth_map(experiment_data)

    runs = experiment_data.get("runs", [])
    if not runs:
        return []

    queries: list[dict[str, Any]] = []
    for er in runs[0].get("evaluation_results", []):
        query = er["query"]
        primary, _ = split_query(query)

        if primary not in gt_map:
            continue

        queries.append(
            {
                **build_query_item(query),
                "ground_truth": gt_map[primary],
                "original_predicted": er.get("predicted", ""),
                "original_latency_ms": er.get("latency_ms", 0),
                "original_confidence": er.get("confidence", 0),
            }
        )

    return queries


EXPERIMENT_EXTRACTORS["termnorm"] = lambda d: (extract_queries(d), extract_index_terms(d))
TRACE_GT_RESOLVERS["termnorm"] = lambda d, q: extract_ground_truth_map(d).get(split_query(q)[0])
