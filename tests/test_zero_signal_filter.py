"""Tests for zero-signal sample filtering.

Covers the three moving parts:
- ``SearchMemory.dead_queries()`` with the ``min_observations`` gate.
- ``BackendStore.exclude_dataset_items()`` / ``restore_dataset_items()``.
- ``apply_zero_signal_exclusions()`` end-to-end sweep.
"""

from __future__ import annotations

from pathlib import Path

from promptpotter.application.intelligence.search_memory import SearchMemory
from promptpotter.application.scoring.zero_signal_filter import (
    apply_zero_signal_exclusions,
)
from promptpotter.infrastructure.store.project_store import ProjectStore


def _seed_memory(hits: dict[str, list[bool]]) -> SearchMemory:
    mem = SearchMemory()
    for q, seq in hits.items():
        mem._query_hits[q] = list(seq)
    return mem


def test_dead_queries_respects_min_observations() -> None:
    mem = _seed_memory(
        {
            "fresh_miss": [False] * 3,  # always miss, only 3 obs → filtered out
            "proven_miss": [False] * 6,  # always miss, 6 obs → dead
            "proven_hit": [True] * 6,  # always hit, 6 obs → dead
            "variable": [True, False, True, False, True, True],  # 5/6 hit → alive
        }
    )

    dead = mem.dead_queries(min_observations=5)
    dead_qs = {r.query for r in dead}
    assert dead_qs == {"proven_miss", "proven_hit"}

    miss_only = mem.dead_queries(min_observations=5, include_always_hit=False)
    assert {r.query for r in miss_only} == {"proven_miss"}


def test_exclude_and_restore_dataset_items(tmp_path: Path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    backend = "bx"
    items = [
        {"query": "a", "ground_truth": "A"},
        {"query": "b", "ground_truth": "B"},
        {"query": "c", "ground_truth": "C"},
    ]
    store.backends.save_dataset(backend, "train", items)

    moved = store.backends.exclude_dataset_items(
        backend,
        "train",
        [{"query": "b", "reason": "zero_signal", "hit_rate": 0.0, "observations": 7}],
    )
    assert moved == 1

    data = store.backends.load_dataset(backend, "train")
    assert data is not None
    assert [d["query"] for d in data["items"]] == ["a", "c"]
    assert len(data["excluded"]) == 1
    assert data["excluded"][0]["item"]["query"] == "b"
    assert data["excluded"][0]["reason"] == "zero_signal"

    restored = store.backends.restore_dataset_items(backend, "train")
    assert restored == 1
    data = store.backends.load_dataset(backend, "train")
    assert data is not None
    assert {d["query"] for d in data["items"]} == {"a", "b", "c"}
    assert data["excluded"] == []


def test_apply_zero_signal_exclusions_mutates_active_and_disk(tmp_path: Path) -> None:
    store = ProjectStore(base_dir=tmp_path)
    backend = "bx"
    items = [
        {"query": "always_miss", "ground_truth": "X"},
        {"query": "always_hit", "ground_truth": "Y"},
        {"query": "varies", "ground_truth": "Z"},
    ]
    store.backends.save_dataset(backend, "train", items)

    memory = _seed_memory(
        {
            "always_miss": [False] * 6,
            "always_hit": [True] * 6,
            "varies": [True, False, True, False, True],
        }
    )
    active = list(items)

    excluded = apply_zero_signal_exclusions(
        store=store,
        backend_id=backend,
        dataset_name="train",
        memory=memory,
        active_dataset=active,
        min_observations=5,
        campaign_id="cyc_test",
    )

    assert {e["query"] for e in excluded} == {"always_miss", "always_hit"}
    # In-memory list was mutated in place.
    assert [d["query"] for d in active] == ["varies"]
    # Disk mirrors the same decision.
    data = store.backends.load_dataset(backend, "train")
    assert data is not None
    assert [d["query"] for d in data["items"]] == ["varies"]
    assert {e["item"]["query"] for e in data["excluded"]} == {"always_miss", "always_hit"}
