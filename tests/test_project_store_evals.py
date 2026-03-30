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


def test_save_load_and_list(tmp_store):
    """Save, load by hash, list, and verify index integrity."""
    data = _make_run_data()
    path = tmp_store.dataset_runs.save("b1", data["run_id"], data)
    assert path.exists()

    loaded = tmp_store.dataset_runs.load_by_hash("b1", data["content_hash"])
    assert loaded is not None
    assert loaded["run_id"] == data["run_id"]
    assert loaded["scores"]["accuracy"] == 0.5
    assert len(loaded["dataset_run_items"]) == 2

    # Save more and verify list + index
    for i in range(2):
        d = _make_run_data(f"run_{i}", f"hash_{i:016d}", f"Run {i}")
        tmp_store.dataset_runs.save("b1", d["run_id"], d)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert len(entries) == 3
    index_path = tmp_store.base_dir / "b1" / "dataset_runs.json"
    index = json.loads(index_path.read_text())
    assert index["total"] == 3


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
    sp = osp.to_job_search_point()
    data = build_dataset_run_data(
        "baseline_aabb", "Baseline", "aabb1122", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
        source="baseline",
    )
    assert data["source"] == "baseline"
    assert data["prompt_fields_id"] == sp.sp_hash()


@pytest.mark.parametrize("pipeline_params,expect_key", [
    ({"llm_ranking": {"prompt": "test"}, "ranking_temperature": 0.5}, True),
    (None, False),
])
def test_build_dataset_run_data_pipeline_params(pipeline_params, expect_key):
    from api.models.search_point import JobSearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    sp = JobSearchPoint(pipeline_params=pipeline_params)
    data = build_dataset_run_data(
        "run_pp", "Test", "hash1234", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
    )
    if expect_key:
        assert data["pipeline_params"] == pipeline_params
    else:
        assert "pipeline_params" not in data


def _make_alias_run(run_id, rp_hash,
                    pipeline_params=None, item_count=3):
    items = [{"query": f"q{i}", "hit": True, "confidence": 0.9, "error": None}
             for i in range(item_count)]
    run = make_dataset_run(
        run_id, accuracy=0.8,
        items=items, content_hash=f"ch_{run_id}",
    )
    run["rendered_prompt_hash"] = rp_hash
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

        result = drs.load_by_alias("b1", "hash_b", None, 3)
        assert result is not None
        assert result["run_id"] == "r1"

    @pytest.mark.parametrize("setup,lookup_hash,pp,n_items", [
        # No alias registered
        ("no_alias", "hash_x", None, 3),
        # Pipeline params mismatch
        ("with_alias_pp", "hash_b", {"ranking_temperature": 0.9}, 3),
        # Item count mismatch
        ("with_alias", "hash_b", None, 99),
    ])
    def test_returns_none_on_mismatch(self, drs, setup, lookup_hash, pp, n_items):
        if setup == "no_alias":
            drs.save("b1", "r1", _make_alias_run("r1", "hash_a"))
        elif setup == "with_alias_pp":
            drs.save("b1", "r1", _make_alias_run(
                "r1", "hash_a", pipeline_params={"steps": ["a"], "ranking_temperature": 0.5},
            ))
            drs.register_alias("b1", "hash_a", "hash_b")
        else:
            drs.save("b1", "r1", _make_alias_run("r1", "hash_a"))
            drs.register_alias("b1", "hash_a", "hash_b")

        assert drs.load_by_alias("b1", lookup_hash, pp, n_items) is None


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

    def test_multiple_runs_same_and_different_prompts(self, tmp_store):
        """Multiple runs merge under same prompt; different prompts stay separate."""
        items1 = [
            {"query": "q1", "predicted": "pred", "ground_truth": "pred",
             "hit": True, "confidence": 0.9, "error": None},
            {"query": "q2", "predicted": "wrong", "ground_truth": "pred",
             "hit": False, "confidence": 0.1, "error": None},
        ]
        items2 = [
            {"query": "q3", "predicted": "pred", "ground_truth": "pred",
             "hit": True, "confidence": 0.9, "error": None},
        ]
        for rid, items, prompt in [("r1", items1, "prompt A"), ("r2", items2, "prompt A"),
                                    ("r3", items2, "prompt B")]:
            run = make_dataset_run(rid, accuracy=0.5, items=items,
                                   content_hash=f"ch_{rid}", rendered_prompt=prompt)
            tmp_store.dataset_runs.save("b1", run["run_id"], run)

        index = build_prompt_result_index(tmp_store, "b1")
        assert len(index) == 2  # prompt A + prompt B
        assert len(index[_rp_hash("prompt A")]) == 3  # q1 + q2 + q3

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
