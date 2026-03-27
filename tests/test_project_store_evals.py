"""Tests for ProjectStore dataset_runs (eval result caching)."""
import json

import pytest
from _helpers import make_dataset_run
from _helpers import rp_hash as _rp_hash

from api.services.search import build_prompt_result_index
from api.services.stores.dataset_run_store import DatasetRunStore


def _make_run_data(run_id="baseline_aabbccdd", content_hash="aabbccdd11223344", name="Baseline"):
    return make_dataset_run(
        run_id, accuracy=0.5, content_hash=content_hash, name=name,
        items=[
            {"query": "q1", "predicted": "p1", "ground_truth": "p1",
             "hit": True, "confidence": 0.9, "error": None},
            {"query": "q2", "predicted": "p2", "ground_truth": "gt2",
             "hit": False, "confidence": 0.3, "error": None},
        ],
    )


def test_save_and_load(tmp_store):
    data = _make_run_data()
    path = tmp_store.dataset_runs.save("b1", data["run_id"], data)
    assert path.exists()

    # Load by hash
    loaded = tmp_store.dataset_runs.load_by_hash("b1", data["content_hash"])
    assert loaded is not None
    assert loaded["run_id"] == data["run_id"]
    assert loaded["scores"]["accuracy"] == 0.5
    assert len(loaded["dataset_run_items"]) == 2

    # Detail file content
    detail = json.loads(path.read_text())
    assert detail["run_id"] == data["run_id"]
    assert detail["dataset_run_items"][0]["query"] == "q1"


def test_list_dataset_runs(tmp_store):
    run1 = _make_run_data("run_a", "hash_a", "Run A")
    run2 = _make_run_data("run_b", "hash_b", "Run B")

    tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
    tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert len(entries) == 2
    assert entries[0]["run_id"] == "run_a"
    assert entries[1]["run_id"] == "run_b"


def test_index_integrity_after_multiple_saves(tmp_store):
    for i in range(3):
        data = _make_run_data(f"run_{i}", f"hash_{i:016d}", f"Run {i}")
        tmp_store.dataset_runs.save("b1", data["run_id"], data)

    index_path = tmp_store.base_dir / "b1" / "dataset_runs.json"
    index = json.loads(index_path.read_text())
    assert index["total"] == 3
    assert len(index["dataset_runs"]) == 3


def test_upsert_replaces_same_hash(tmp_store):
    data1 = _make_run_data("run_v1", "same_hash_1234", "V1")
    data2 = _make_run_data("run_v2", "same_hash_1234", "V2")
    data2["scores"]["accuracy"] = 0.75

    tmp_store.dataset_runs.save("b1", data1["run_id"], data1)
    tmp_store.dataset_runs.save("b1", data2["run_id"], data2)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert len(entries) == 1
    assert entries[0]["run_id"] == "run_v2"
    assert entries[0]["scores"]["accuracy"] == 0.75


def test_build_dataset_run_data_includes_source():
    from api.models.opt_search_point import OptSearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    osp = OptSearchPoint(instruction="test prompt")
    sp = osp.to_job_search_point(model="model", temperature=0.0)
    data = build_dataset_run_data(
        "baseline_aabb", "Baseline", "aabb1122", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
        source="baseline",
    )
    assert data["source"] == "baseline"
    assert data["prompt_fields_id"] == sp.sp_hash()
    assert data["model"] == "model"
    assert data["temperature"] == 0.0


def test_build_dataset_run_data_includes_pipeline_params():
    from api.models.search_point import JobSearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    pp = {"llm_ranking": {"prompt": "test prompt"}, "ranking_temperature": 0.5}
    sp = JobSearchPoint(pipeline_params=pp)
    data = build_dataset_run_data(
        "run_pp", "PP Test", "hash1234", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
    )
    assert data["pipeline_params"] == pp


def test_build_dataset_run_data_omits_empty_pipeline_params():
    from api.models.search_point import JobSearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    sp = JobSearchPoint()
    data = build_dataset_run_data(
        "run_npp", "No PP", "hash5678", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
    )
    assert "pipeline_params" not in data


def test_source_persisted_in_index(tmp_store):
    data = _make_run_data()
    data["source"] = "sensitivity_scan"
    tmp_store.dataset_runs.save("b1", data["run_id"], data)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert entries[0]["source"] == "sensitivity_scan"


def _make_alias_run(run_id, rp_hash, model="m1", temperature=0.5,
                    pipeline_params=None, item_count=3):
    items = [{"query": f"q{i}", "hit": True, "confidence": 0.9, "error": None}
             for i in range(item_count)]
    run = make_dataset_run(
        run_id, accuracy=0.8,
        items=items, content_hash=f"ch_{run_id}",
    )
    run["rendered_prompt_hash"] = rp_hash
    run["model"] = model
    run["temperature"] = temperature
    run["source"] = "test"
    run["scores"] = {"accuracy": 0.8, "hits": 2, "total": item_count}
    if pipeline_params:
        run["pipeline_params"] = pipeline_params
    return run


class TestLoadByAlias:
    @pytest.fixture()
    def drs(self, tmp_path):
        return DatasetRunStore(tmp_path)

    def test_load_exact_alias_match(self, drs):
        drs.save("b1", "r1", _make_alias_run("r1", "hash_a"))
        drs.register_alias("b1", "hash_a", "hash_b")

        result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, None, 3)
        assert result is not None
        assert result["run_id"] == "r1"

    def test_no_alias_returns_none(self, drs):
        drs.save("b1", "r1", _make_alias_run("r1", "hash_a"))

        result = drs.load_by_alias("b1", "hash_x", "m1", 0.5, None, 3)
        assert result is None

    def test_model_mismatch_returns_none(self, drs):
        drs.save("b1", "r1", _make_alias_run("r1", "hash_a"))
        drs.register_alias("b1", "hash_a", "hash_b")

        result = drs.load_by_alias("b1", "hash_b", "wrong_model", 0.5, None, 3)
        assert result is None

    def test_pipeline_params_must_match_exactly(self, drs):
        drs.save("b1", "r1", _make_alias_run(
            "r1", "hash_a", pipeline_params={"steps": ["a"], "ranking_temperature": 0.5},
        ))
        drs.register_alias("b1", "hash_a", "hash_b")

        result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, {"ranking_temperature": 0.9}, 3)
        assert result is None

    def test_item_count_mismatch(self, drs):
        drs.save("b1", "r1", _make_alias_run("r1", "hash_a"))
        drs.register_alias("b1", "hash_a", "hash_b")

        result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, None, 99)
        assert result is None


class TestPromptResultIndex:
    def test_build_and_load_single_run(self, tmp_store):
        items = [
            {"query": "q1", "predicted": "pred", "ground_truth": "pred",
             "hit": True, "confidence": 0.9, "error": None},
            {"query": "q2", "predicted": "wrong", "ground_truth": "pred",
             "hit": False, "confidence": 0.1, "error": None},
        ]
        run = make_dataset_run(
            "r1", accuracy=0.5, items=items,
            content_hash="ch_r1", rendered_prompt="prompt A",
        )
        tmp_store.dataset_runs.save("b1", run["run_id"], run)

        index = build_prompt_result_index(tmp_store, "b1")
        rp_hash = _rp_hash("prompt A")
        assert rp_hash in index
        assert index[rp_hash]["q1"]["hit"] is True
        assert index[rp_hash]["q2"]["hit"] is False

        loaded = tmp_store.dataset_runs.load_by_id("b1", "r1")
        assert loaded is not None
        assert loaded["run_id"] == "r1"

    def test_build_index_multiple_runs_same_prompt(self, tmp_store):
        items1 = [
            {"query": "q1", "predicted": "pred", "ground_truth": "pred",
             "hit": True, "confidence": 0.9, "error": None},
            {"query": "q2", "predicted": "wrong", "ground_truth": "pred",
             "hit": False, "confidence": 0.1, "error": None},
        ]
        items2 = [
            {"query": "q3", "predicted": "pred", "ground_truth": "pred",
             "hit": True, "confidence": 0.9, "error": None},
            {"query": "q4", "predicted": "pred", "ground_truth": "pred",
             "hit": True, "confidence": 0.9, "error": None},
        ]
        run1 = make_dataset_run(
            "r1", accuracy=0.5, items=items1,
            content_hash="ch_r1", rendered_prompt="prompt A",
        )
        run2 = make_dataset_run(
            "r2", accuracy=1.0, items=items2,
            content_hash="ch_r2", rendered_prompt="prompt A",
        )
        tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
        tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

        index = build_prompt_result_index(tmp_store, "b1")
        rp_hash = _rp_hash("prompt A")
        assert len(index) == 1
        assert len(index[rp_hash]) == 4

    def test_build_index_different_prompts(self, tmp_store):
        run1 = make_dataset_run(
            "r1", accuracy=1.0,
            items=[{"query": "q1", "predicted": "pred", "ground_truth": "pred",
                    "hit": True, "confidence": 0.9, "error": None}],
            content_hash="ch_r1", rendered_prompt="prompt A",
        )
        run2 = make_dataset_run(
            "r2", accuracy=0.0,
            items=[{"query": "q1", "predicted": "wrong", "ground_truth": "pred",
                    "hit": False, "confidence": 0.1, "error": None}],
            content_hash="ch_r2", rendered_prompt="prompt B",
        )
        tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
        tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

        index = build_prompt_result_index(tmp_store, "b1")
        assert len(index) == 2

    def test_build_index_later_run_overwrites_query(self, tmp_store):
        run1 = make_dataset_run(
            "r1", accuracy=1.0,
            items=[{"query": "q1", "predicted": "pred", "ground_truth": "pred",
                    "hit": True, "confidence": 0.9, "error": None}],
            content_hash="ch_r1", rendered_prompt="prompt A",
        )
        run2 = make_dataset_run(
            "r2", accuracy=0.0,
            items=[{"query": "q1", "predicted": "wrong", "ground_truth": "pred",
                    "hit": False, "confidence": 0.1, "error": None}],
            content_hash="ch_r2", rendered_prompt="prompt A",
        )
        tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
        tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

        index = build_prompt_result_index(tmp_store, "b1")
        rp_hash = _rp_hash("prompt A")
        assert rp_hash in index
        assert "q1" in index[rp_hash]

    def test_index_ignores_runs_without_hash(self, tmp_store):
        run = make_dataset_run(
            "r1", accuracy=1.0,
            items=[{"query": "q1", "predicted": "pred", "ground_truth": "pred",
                    "hit": True, "confidence": 0.9, "error": None}],
            content_hash="ch_r1", rendered_prompt="prompt A",
        )
        del run["rendered_prompt_hash"]
        tmp_store.dataset_runs.save("b1", run["run_id"], run)

        index = build_prompt_result_index(tmp_store, "b1")
        assert index == {}
