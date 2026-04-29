"""Tests for zero-signal sample filtering.

Covers the three moving parts:
- ``SampleIndex.dead()`` with the ``min_observations`` gate.
- ``BackendStore.exclude_dataset_items()`` / ``restore_dataset_items()``.
- ``apply_zero_signal_exclusions()`` end-to-end sweep.
"""

from __future__ import annotations

from pathlib import Path

from promptpotter.application.intelligence.indexes import AxisIndex, SampleIndex
from promptpotter.application.scoring.formula import (
    apply_zero_signal_exclusions,
)
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.store import build_stores


def _seed_axes(hits: dict[str, list[bool]]) -> AxisIndex:
    """Seed an AxisIndex (via SampleIndex) with synthetic hit histories."""
    idx = SampleIndex()
    for i, (query, seq) in enumerate(hits.items()):
        sample = Sample(id=i, query=query, ground_truth=f"gt_{i}")
        idx.register(sample)
        idx._hits[i] = list(seq)
    return AxisIndex(sample_index=idx)


def test_dead_queries_respects_min_observations() -> None:
    axes = _seed_axes(
        {
            "fresh_miss": [False] * 3,  # always miss, only 3 obs → filtered out
            "proven_miss": [False] * 6,  # always miss, 6 obs → dead
            "proven_hit": [True] * 6,  # always hit, 6 obs → dead
            "variable": [True, False, True, False, True, True],  # 5/6 hit → alive
        }
    )

    dead = axes.sample_index.dead(min_observations=5)
    dead_qs = {r.query for r in dead}
    assert dead_qs == {"proven_miss", "proven_hit"}

    miss_only = axes.sample_index.dead(min_observations=5, include_always_hit=False)
    assert {r.query for r in miss_only} == {"proven_miss"}


def test_exclude_and_restore_dataset_items(tmp_path: Path) -> None:
    store = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    items = [
        {"query": "a", "ground_truth": "A"},
        {"query": "b", "ground_truth": "B"},
        {"query": "c", "ground_truth": "C"},
    ]
    store.backends.save_dataset("train", items)

    moved = store.backends.exclude_dataset_items(
        "train",
        [{"query": "b", "reason": "zero_signal", "hit_rate": 0.0, "observations": 7}],
    )
    assert moved == 1

    data = store.backends.load_dataset("train")
    assert data is not None
    assert [d["query"] for d in data["items"]] == ["a", "c"]
    assert len(data["excluded"]) == 1
    assert data["excluded"][0]["item"]["query"] == "b"
    assert data["excluded"][0]["reason"] == "zero_signal"

    restored = store.backends.restore_dataset_items("train")
    assert restored == 1
    data = store.backends.load_dataset("train")
    assert data is not None
    assert {d["query"] for d in data["items"]} == {"a", "b", "c"}
    assert data["excluded"] == []


def test_apply_zero_signal_exclusions_mutates_active_and_disk(tmp_path: Path) -> None:
    store = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    samples = [
        Sample(id=0, query="always_miss", ground_truth="X"),
        Sample(id=1, query="always_hit", ground_truth="Y"),
        Sample(id=2, query="varies", ground_truth="Z"),
    ]
    store.backends.save_dataset("train", samples)

    axes = _seed_axes(
        {
            "always_miss": [False] * 6,
            "always_hit": [True] * 6,
            "varies": [True, False, True, False, True],
        }
    )
    active = list(samples)

    excluded = apply_zero_signal_exclusions(
        store=store,
        dataset_name="train",
        axes=axes,
        active_dataset=active,
        min_observations=5,
        campaign_id="cyc_test",
    )

    assert {e["query"] for e in excluded} == {"always_miss", "always_hit"}
    # In-memory list was mutated in place.
    assert [s.query for s in active] == ["varies"]
    # Disk mirrors the same decision.
    data = store.backends.load_dataset("train")
    assert data is not None
    assert [d["query"] for d in data["items"]] == ["varies"]
    assert {e["item"]["query"] for e in data["excluded"]} == {"always_miss", "always_hit"}
