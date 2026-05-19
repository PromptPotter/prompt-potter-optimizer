"""Dataset-scoped archive — contract tests for the per-dataset slice.

Six contracts: writes stamp dataset_name into index + detail; reads filter
by it; default excludes unknown (pre-schema) entries; cache reuse and
SampleIndex isolation respect the scope. Bug being guarded: when one
backend serves multiple datasets, integer sample_id collides and a single
unscoped read pools AIME hits with JustLogic hits, corrupting Rasch + PoBB
+ L1 panels.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_observations,
)
from promptpotter.application.intelligence.indexes import SampleIndex
from promptpotter.infrastructure.store.measurement_archive import MeasurementArchive


def _seed(
    archive: MeasurementArchive,
    *,
    run_id: str,
    dataset_name: str | None,
    sample_id: int = 0,
    hit: bool = True,
    content_hash: str | None = None,
) -> None:
    ch = content_hash or f"hash_{run_id}"
    data: dict[str, Any] = {
        "run_id": run_id,
        "name": run_id,
        "content_hash": ch,
        "prompt_fields_id": "pf_x",
        "item_count": 1,
        "scores": {"accuracy": 1.0 if hit else 0.0, "total": 1},
        "node_configs": [["llm_only", {"model": "X"}]],
        "pipeline_params": {"llm_only": {"model": "X"}},
        "created_at": "2026-05-19T00:00:00Z",
        "measurements": [
            {
                "sample_id": sample_id,
                "query": f"q_{dataset_name or 'unknown'}_{sample_id}",
                "ground_truth": "gt",
                "predicted": "p",
                "hit": hit,
                "fitness": 1.0 if hit else 0.0,
                "pipeline_data": {"terminated_at": "llm_only"},
            }
        ],
    }
    if dataset_name is not None:
        data["dataset_name"] = dataset_name
    archive.save("bk", run_id, data)


def test_save_writes_dataset_name(tmp_path: Path) -> None:
    """Phase 1 contract: the field lands in both the index summary and the per-run detail."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="r1", dataset_name="aime")

    index = json.loads((tmp_path / "archive" / "measurements_index.json").read_text())
    assert index["measurements"][0]["dataset_name"] == "aime"
    detail = json.loads((tmp_path / "archive" / "measurements" / "r1.json").read_text())
    assert detail["dataset_name"] == "aime"


def test_list_all_filters_by_dataset(tmp_path: Path) -> None:
    """Phase 2 contract: explicit dataset_name returns only matching entries."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="aime_1", dataset_name="aime")
    _seed(archive, run_id="just_1", dataset_name="justlogic")

    aime_only = archive.list_all("bk", dataset_name="aime")
    assert [e["run_id"] for e in aime_only] == ["aime_1"]
    just_only = archive.list_all("bk", dataset_name="justlogic")
    assert [e["run_id"] for e in just_only] == ["just_1"]
    no_filter = archive.list_all("bk")
    assert {e["run_id"] for e in no_filter} == {"aime_1", "just_1"}


def test_unknown_entries_excluded_by_default(tmp_path: Path) -> None:
    """Pre-schema entries (v1, no dataset_name) drop out of cross-cycle views by default."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="old", dataset_name=None)  # v1-shape
    _seed(archive, run_id="new", dataset_name="aime")

    only_aime = archive.list_all("bk", dataset_name="aime")
    assert [e["run_id"] for e in only_aime] == ["new"]
    with_unknown = archive.list_all("bk", dataset_name="aime", include_unknown=True)
    assert {e["run_id"] for e in with_unknown} == {"new", "old"}


def test_archive_observations_dataset_scoped(tmp_path: Path) -> None:
    """build_archive_observations filters by dataset — colliding sample_ids stay isolated."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="aime_5", dataset_name="aime", sample_id=14, hit=False)
    _seed(archive, run_id="just_5", dataset_name="justlogic", sample_id=14, hit=True)
    stores = SimpleNamespace(archive=archive)

    aime_obs = build_archive_observations(stores, "bk", dataset_name="aime")
    assert {(o.sample_id, o.hit) for o in aime_obs} == {(14, False)}
    just_obs = build_archive_observations(stores, "bk", dataset_name="justlogic")
    assert {(o.sample_id, o.hit) for o in just_obs} == {(14, True)}


def test_hit_cache_respects_dataset(tmp_path: Path) -> None:
    """load_reusable_results scopes by dataset — identical node-configs don't bleed across datasets."""
    archive = MeasurementArchive(tmp_path)
    _seed(archive, run_id="aime_cached", dataset_name="aime", sample_id=14, hit=True)
    _seed(archive, run_id="just_fresh", dataset_name="justlogic", sample_id=14, hit=False)

    node_configs = [("llm_only", {"model": "X"})]
    aime_cache = archive.load_reusable_results("bk", node_configs, dataset_name="aime")
    just_cache = archive.load_reusable_results("bk", node_configs, dataset_name="justlogic")

    # Same sample_id, different dataset → different query texts → cached
    # results for one dataset are unreachable under the other's dataset_name.
    aime_queries = set(aime_cache.keys())
    just_queries = set(just_cache.keys())
    assert aime_queries.isdisjoint(just_queries)
    assert any("aime" in q for q in aime_queries)
    assert any("justlogic" in q for q in just_queries)


def test_sample_index_per_dataset_isolation() -> None:
    """SampleIndex ingest filtered at the source — two indexes built from
    dataset-scoped streams retain dataset-specific query text for the
    same integer sample_id. The cycle bootstrap is responsible for
    feeding each index only its own dataset's runs; this test pins that
    feeding two cycle-scoped indexes from disjoint per-dataset slices
    produces no cross-talk."""
    aime_idx = SampleIndex()
    just_idx = SampleIndex()

    aime_idx.ingest_run(
        {
            "run_id": "aime_r1",
            "measurements": [
                {
                    "sample_id": 14,
                    "query": "The twelve letters $A$,$B$,$C$,…",
                    "ground_truth": "0",
                    "predicted": "0",
                    "hit": False,
                    "pipeline_data": {"terminated_at": "llm_only"},
                }
            ],
        }
    )
    just_idx.ingest_run(
        {
            "run_id": "just_r1",
            "measurements": [
                {
                    "sample_id": 14,
                    "query": "Premises: Whenever it is true that ice…",
                    "ground_truth": "Uncertain",
                    "predicted": "Uncertain",
                    "hit": True,
                    "pipeline_data": {"terminated_at": "llm_only"},
                }
            ],
        }
    )

    aime_sample = aime_idx.sample(14)
    just_sample = just_idx.sample(14)
    assert aime_sample is not None and just_sample is not None
    assert "twelve letters" in aime_sample.query
    assert "Premises" in just_sample.query
    # Hit histories isolated too — the bug surfaced when a single global
    # SampleIndex aggregated both, breaking per-sample dead/hard counts.
    assert aime_idx._hits[14] == [False]
    assert just_idx._hits[14] == [True]
