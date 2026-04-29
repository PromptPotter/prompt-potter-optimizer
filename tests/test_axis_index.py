"""AxisIndex — rebuild contract.

Guards the recompute-from-archive invariant: every ``refresh()`` rebuilds
``_axis_values`` from ``MeasurementArchive.list_all()``, so the axis side
is a pure derivation with no persistence. Idempotent under no-change
refresh.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from promptpotter.application.intelligence.indexes import AxisIndex
from promptpotter.infrastructure.store import build_stores
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive


def _seed_run(
    archive: MeasurementArchive,
    run_id: str,
    *,
    pipeline_params: dict[str, dict[str, Any]],
    accuracy: float,
) -> None:
    archive.save(
        "any-backend",
        run_id,
        {
            "run_id": run_id,
            "name": run_id,
            "content_hash": f"hash_{run_id}",
            "prompt_fields_id": "pf_x",
            "item_count": 1,
            "scores": {"accuracy": accuracy, "total": 1},
            "node_configs": list(pipeline_params.items()),
            "pipeline_params": pipeline_params,
            "created_at": "2026-01-01T00:00:00Z",
            "measurements": [
                {
                    "sample_id": 0,
                    "query": "q",
                    "ground_truth": "gt",
                    "predicted": "p",
                    "hit": accuracy >= 0.5,
                    "score": accuracy,
                    "pipeline_data": {"terminated_at": "llm_only"},
                }
            ],
        },
    )


@pytest.fixture
def stores(tmp_path: Path):
    s = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    _seed_run(
        s.archive,
        "run_a",
        pipeline_params={"llm_only": {"model": "X", "temperature": 0.0}},
        accuracy=0.8,
    )
    _seed_run(
        s.archive,
        "run_b",
        pipeline_params={"llm_only": {"model": "Y", "temperature": 0.0}},
        accuracy=0.4,
    )
    return s


def test_axis_index_refresh_rebuilds_axis_values(stores: Any) -> None:
    idx = AxisIndex()
    idx.refresh(stores, "any-backend")

    # Axis side: model has two values (X, Y); temperature has one (0.0).
    assert set(idx._axis_values["llm_only.model"].keys()) == {"X", "Y"}
    assert idx._axis_values["llm_only.model"]["X"] == [0.8]
    assert idx._axis_values["llm_only.model"]["Y"] == [0.4]
    assert set(idx._axis_values["llm_only.temperature"].keys()) == {"0.0"}
    assert sorted(idx._axis_values["llm_only.temperature"]["0.0"]) == [0.4, 0.8]

    # Idempotent under full rebuild.
    snapshot = {
        a: {v: list(accs) for v, accs in vals.items()} for a, vals in idx._axis_values.items()
    }
    idx.refresh(stores, "any-backend")
    rebuilt = {
        a: {v: list(accs) for v, accs in vals.items()} for a, vals in idx._axis_values.items()
    }
    assert rebuilt == snapshot


def test_axis_index_no_persistence(stores: Any, tmp_path: Path) -> None:
    """Neither digest side writes anything to ``library/``."""
    idx = AxisIndex()
    idx.refresh(stores, "any-backend")
    library = stores.base_dir / "library"
    assert not (library / "axes.json").exists()
    assert not (library / "search_memory.json").exists()
    assert not (library / "samples.json").exists()
