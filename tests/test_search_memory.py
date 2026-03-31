"""Tests for SearchMemory — Wave 3a (core) + 3b (analysis pillars)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from api.services.search.search_memory import SearchMemory


def _make_run(
    run_id: str,
    *,
    accuracy: float = 0.5,
    pipeline_params: dict | None = None,
    items: list[dict] | None = None,
) -> dict:
    if items is None:
        items = [
            {"query": "q0", "hit": True, "error": None, "pipeline_data": {"terminated_at": "llm_ranking"}},
            {"query": "q1", "hit": False, "error": None, "pipeline_data": {"terminated_at": "token_matching"}},
        ]
    return {
        "run_id": run_id,
        "scores": {"accuracy": accuracy, "hits": 1, "total": 2},
        "pipeline_params": pipeline_params or {"llm_ranking": {"prompt": "test"}},
        "dataset_run_items": items,
        "content_hash": f"hash_{run_id}",
        "prompt_fields_id": "pf1",
        "item_count": len(items),
        "created_at": "2026-03-30T00:00:00",
    }


def _mock_store(*runs: dict) -> MagicMock:
    store = MagicMock()
    store.dataset_runs.list_all.return_value = [
        {"run_id": r["run_id"]} for r in runs
    ]
    store.dataset_runs.load_by_id.side_effect = lambda _bid, rid: next(
        (r for r in runs if r["run_id"] == rid), None,
    )
    return store


class TestRefreshAndWatermark:
    def test_fresh_refresh(self):
        run = _make_run("r1")
        store = _mock_store(run)
        mem = SearchMemory()
        assert mem.refresh(store, "backend1") is True
        assert "r1" in mem._watermark

    def test_no_new_runs(self):
        run = _make_run("r1")
        store = _mock_store(run)
        mem = SearchMemory()
        mem.refresh(store, "backend1")
        assert mem.refresh(store, "backend1") is False  # no new data

    def test_incremental_refresh(self):
        r1 = _make_run("r1")
        r2 = _make_run("r2", accuracy=0.8)
        store = _mock_store(r1)
        mem = SearchMemory()
        mem.refresh(store, "b1")

        # Add r2 to store
        store.dataset_runs.list_all.return_value = [
            {"run_id": "r1"}, {"run_id": "r2"},
        ]
        store.dataset_runs.load_by_id.side_effect = lambda _bid, rid: (
            r1 if rid == "r1" else r2
        )
        assert mem.refresh(store, "b1") is True
        assert len(mem._watermark) == 2


class TestParameterImpact:
    def test_axis_rankings(self):
        r1 = _make_run("r1", accuracy=0.3, pipeline_params={"llm": {"temp": 0.5}})
        r2 = _make_run("r2", accuracy=0.8, pipeline_params={"llm": {"temp": 0.9}})
        store = _mock_store(r1, r2)
        mem = SearchMemory()
        mem.refresh(store, "b1")

        rankings = mem.axis_rankings()
        assert len(rankings) > 0
        # llm.temp should have non-zero effect since 0.3 vs 0.8
        temp_impact = next((a for a in rankings if a.axis == "llm.temp"), None)
        assert temp_impact is not None
        assert temp_impact.effect_size > 0

    def test_top_k_values(self):
        r1 = _make_run("r1", accuracy=0.3, pipeline_params={"node": {"k": "10"}})
        r2 = _make_run("r2", accuracy=0.9, pipeline_params={"node": {"k": "20"}})
        store = _mock_store(r1, r2)
        mem = SearchMemory()
        mem.refresh(store, "b1")

        top = mem.top_k_values("node.k", k=2)
        assert len(top) == 2
        assert top[0].mean_accuracy > top[1].mean_accuracy  # sorted desc

    def test_axis_impact_single_value(self):
        r1 = _make_run("r1", pipeline_params={"node": {"k": "10"}})
        store = _mock_store(r1)
        mem = SearchMemory()
        mem.refresh(store, "b1")

        impact = mem.axis_impact("node.k")
        assert impact is not None
        assert impact.classification == "dead"  # only one value seen


class TestQueryPatterns:
    def test_query_tractability(self):
        items = [
            {"query": "easy", "hit": True, "error": None, "pipeline_data": {}},
            {"query": "hard", "hit": False, "error": None, "pipeline_data": {"terminated_at": "token_matching"}},
        ]
        r1 = _make_run("r1", items=items)
        r2 = _make_run("r2", items=items)  # same results
        store = _mock_store(r1, r2)
        mem = SearchMemory()
        mem.refresh(store, "b1")

        records = mem.query_tractability()
        by_q = {r.query: r for r in records}
        assert by_q["easy"].hit_rate == 1.0
        assert by_q["hard"].hit_rate == 0.0

    def test_discriminating_queries(self):
        items1 = [
            {"query": "disc", "hit": True, "error": None, "pipeline_data": {}},
            {"query": "dead", "hit": False, "error": None, "pipeline_data": {"terminated_at": "x"}},
        ]
        items2 = [
            {"query": "disc", "hit": False, "error": None, "pipeline_data": {"terminated_at": "x"}},
            {"query": "dead", "hit": False, "error": None, "pipeline_data": {"terminated_at": "x"}},
        ]
        store = _mock_store(_make_run("r1", items=items1), _make_run("r2", items=items2))
        mem = SearchMemory()
        mem.refresh(store, "b1")

        disc = mem.discriminating_queries(min_variance=0.1)
        assert any(q.query == "disc" for q in disc)
        assert not any(q.query == "dead" for q in disc)

    def test_dead_queries(self):
        items = [
            {"query": "always_miss", "hit": False, "error": None, "pipeline_data": {"terminated_at": "x"}},
        ]
        store = _mock_store(_make_run("r1", items=items), _make_run("r2", items=items))
        mem = SearchMemory()
        mem.refresh(store, "b1")

        dead = mem.dead_queries()
        assert any(q.query == "always_miss" for q in dead)


class TestFailureModes:
    def test_bottleneck_distribution(self):
        items = [
            {"query": "q0", "hit": False, "error": None, "pipeline_data": {"terminated_at": "token_matching"}},
            {"query": "q1", "hit": False, "error": None, "pipeline_data": {"terminated_at": "token_matching"}},
            {"query": "q2", "hit": False, "error": None, "pipeline_data": {"terminated_at": "llm_ranking"}},
        ]
        store = _mock_store(_make_run("r1", items=items))
        mem = SearchMemory()
        mem.refresh(store, "b1")

        dist = mem.bottleneck_distribution()
        assert dist["token_matching"] == pytest.approx(2 / 3)
        assert dist["llm_ranking"] == pytest.approx(1 / 3)

    def test_failure_clusters(self):
        items = [
            {"query": "q0", "hit": False, "error": None, "pipeline_data": {"terminated_at": "token_matching"}},
            {"query": "q1", "hit": False, "error": None, "pipeline_data": {"terminated_at": "token_matching"}},
            {"query": "q2", "hit": False, "error": None, "pipeline_data": {"terminated_at": "llm_ranking"}},
        ]
        store = _mock_store(_make_run("r1", items=items))
        mem = SearchMemory()
        mem.refresh(store, "b1")

        clusters = mem.failure_clusters()
        assert len(clusters) == 2
        assert clusters[0].failure_mode == "token_matching"
        assert clusters[0].query_count == 2

    def test_no_failures(self):
        items = [{"query": "q0", "hit": True, "error": None, "pipeline_data": {}}]
        store = _mock_store(_make_run("r1", items=items))
        mem = SearchMemory()
        mem.refresh(store, "b1")

        assert mem.bottleneck_distribution() == {}
        assert mem.failure_clusters() == []


class TestPersistence:
    def test_save_and_load(self, tmp_path: Path):
        items = [
            {"query": "q0", "hit": True, "error": None, "pipeline_data": {}},
            {"query": "q1", "hit": False, "error": None, "pipeline_data": {"terminated_at": "tm"}},
        ]
        r1 = _make_run("r1", accuracy=0.5, items=items, pipeline_params={"n": {"k": "10"}})
        store = _mock_store(r1)
        mem = SearchMemory()
        mem.refresh(store, "b1")

        path = tmp_path / "search_memory.json"
        mem.save(path)
        assert path.exists()

        loaded = SearchMemory.load(path)
        assert loaded._watermark == {"r1"}
        assert loaded.bottleneck_distribution() == mem.bottleneck_distribution()
        assert len(loaded.query_tractability()) == len(mem.query_tractability())

    def test_load_missing_file(self, tmp_path: Path):
        mem = SearchMemory.load(tmp_path / "nonexistent.json")
        assert len(mem._watermark) == 0

    def test_load_corrupted_file(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("not json!!!")
        mem = SearchMemory.load(path)
        assert len(mem._watermark) == 0
