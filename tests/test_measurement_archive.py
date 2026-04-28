"""MeasurementArchive — retrieval contract.

Guards: ``measurements_for_sample``, ``measurements_for_config`` (subset
predicate via ``_matches_subset``), ``find_by_node_configs`` (positional
prefix-exact match via ``_match_prefix_exact``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive


def _seed_run(
    archive: MeasurementArchive,
    run_id: str,
    *,
    node_configs: list[tuple[str, dict[str, Any]]],
    items: list[dict[str, Any]],
) -> None:
    archive.save(
        "any-backend",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": len(items),
            "scores": {"accuracy": 0.5, "total": len(items)},
            "node_configs": node_configs,
            "pipeline_params": dict(node_configs),
            "created_at": "2026-01-01T00:00:00Z",
            "measurements": items,
        },
    )


def _item(sample_id: int, *, hit: bool, score: float = 1.0) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "query": f"q_{sample_id}",
        "ground_truth": f"gt_{sample_id}",
        "predicted": "pred",
        "hit": hit,
        "score": score,
        "pipeline_data": {"terminated_at": "llm_only"},
    }


@pytest.fixture
def archive(tmp_path: Path) -> MeasurementArchive:
    a = MeasurementArchive(tmp_path)
    _seed_run(
        a,
        "run_a",
        node_configs=[("llm_only", {"model": "X", "temperature": 0.0})],
        items=[_item(0, hit=True), _item(1, hit=False)],
    )
    _seed_run(
        a,
        "run_b",
        node_configs=[("llm_only", {"model": "Y", "temperature": 0.0})],
        items=[_item(0, hit=False), _item(1, hit=True)],
    )
    _seed_run(
        a,
        "run_c",
        node_configs=[("web_search", {"max_sites": 5}), ("llm_only", {"model": "X"})],
        items=[_item(0, hit=True)],
    )
    return a


def test_measurements_for_sample_returns_only_matching_rows(
    archive: MeasurementArchive,
) -> None:
    rows = archive.measurements_for_sample("any-backend", 0)
    assert len(rows) == 3  # sample 0 in all three runs
    assert {r.run_id for r in rows} == {"run_a", "run_b", "run_c"}
    assert all(r.sample_id == 0 for r in rows)
    a_row = next(r for r in rows if r.run_id == "run_a")
    assert a_row.hit is True
    assert a_row.node_configs == [("llm_only", {"model": "X", "temperature": 0.0})]
    assert a_row.score == 1.0


def test_measurements_for_config_subset_predicate(archive: MeasurementArchive) -> None:
    # Node-presence: every run has llm_only → all rows.
    presence = archive.measurements_for_config("any-backend", {"llm_only": {}})
    assert {r.run_id for r in presence} == {"run_a", "run_b", "run_c"}

    # Value match: only run_a + run_c carry model=X.
    by_model_x = archive.measurements_for_config("any-backend", {"llm_only": {"model": "X"}})
    assert {r.run_id for r in by_model_x} == {"run_a", "run_c"}

    # Multi-node predicate: only run_c has both nodes.
    multi = archive.measurements_for_config("any-backend", {"web_search": {}, "llm_only": {}})
    assert {r.run_id for r in multi} == {"run_c"}

    # No-match value: returns nothing.
    none_match = archive.measurements_for_config("any-backend", {"llm_only": {"model": "Z"}})
    assert none_match == []


def test_measurements_for_config_empty_predicate_returns_empty(
    archive: MeasurementArchive,
) -> None:
    assert archive.measurements_for_config("any-backend", {}) == []


def test_find_by_node_configs_prefix_exact(archive: MeasurementArchive) -> None:
    # Single-node prefix: matches run_a and run_b (both start with llm_only),
    # but each with a different config — only run_a matches model=X exactly.
    matches = archive.find_by_node_configs(
        "any-backend",
        [("llm_only", {"model": "X", "temperature": 0.0})],
    )
    assert len(matches) == 1
    entry, match_len = matches[0]
    assert entry["run_id"] == "run_a"
    assert match_len == 1

    # Two-node prefix matches run_c only.
    matches2 = archive.find_by_node_configs(
        "any-backend",
        [("web_search", {"max_sites": 5}), ("llm_only", {"model": "X"})],
    )
    assert len(matches2) == 1
    assert matches2[0][0]["run_id"] == "run_c"
    assert matches2[0][1] == 2

    # Empty spec returns empty list (cache-reuse no-op contract).
    assert archive.find_by_node_configs("any-backend", []) == []
