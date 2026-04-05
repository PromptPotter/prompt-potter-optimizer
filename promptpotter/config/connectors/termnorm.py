"""TermNorm connector config — LCA terminology normalization.

Query format: ``bom_material / process`` (slash-delimited)
Ground truth: ``dataset_entry`` from experiment mappings
Backend: TermNorm-excel (github.com/runfish5/TermNorm-excel)

Pipeline: cache_lookup → fuzzy_matching → web_search → entity_profiling
          → token_matching (llm_ranking exists but excluded due to bugs)
"""

from __future__ import annotations

from typing import Any

# -- Defaults ---------------------------------------------------------------

BACKEND_NAME = "TermNorm Local"
BACKEND_TYPE = "termnorm"
DATASET_DESCRIPTION = (
    "TermNorm production ground truth queries for prompt evaluation"
)
PIPELINE_TRACE_NAME = "termnorm_pipeline"


# -- Query parsing ----------------------------------------------------------


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


# -- Experiment data extraction ---------------------------------------------


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

        queries.append({
            **build_query_item(query),
            "ground_truth": gt_map[primary],
            "original_predicted": er.get("predicted", ""),
            "original_latency_ms": er.get("latency_ms", 0),
            "original_confidence": er.get("confidence", 0),
        })

    return queries
