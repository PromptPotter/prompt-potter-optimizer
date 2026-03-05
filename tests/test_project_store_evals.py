"""Tests for ProjectStore dataset_runs (eval result caching)."""
import json

from _helpers import make_dataset_run


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
    """Save a run, load by hash, verify detail file."""
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
    """Index total matches actual entry count."""
    for i in range(3):
        data = _make_run_data(f"run_{i}", f"hash_{i:016d}", f"Run {i}")
        tmp_store.dataset_runs.save("b1", data["run_id"], data)

    index_path = tmp_store.base_dir / "b1" / "dataset_runs.json"
    index = json.loads(index_path.read_text())
    assert index["total"] == 3
    assert len(index["dataset_runs"]) == 3


def test_upsert_replaces_same_hash(tmp_store):
    """Saving with the same content_hash replaces the index entry."""
    data1 = _make_run_data("run_v1", "same_hash_1234", "V1")
    data2 = _make_run_data("run_v2", "same_hash_1234", "V2")
    data2["scores"]["accuracy"] = 0.75

    tmp_store.dataset_runs.save("b1", data1["run_id"], data1)
    tmp_store.dataset_runs.save("b1", data2["run_id"], data2)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert len(entries) == 1
    assert entries[0]["run_id"] == "run_v2"
    assert entries[0]["scores"]["accuracy"] == 0.75


# ---------------------------------------------------------------------------
# Incremental eval writes
# ---------------------------------------------------------------------------


def test_append_and_load_partial_eval(tmp_store):
    """append_eval_item writes items that load_partial_eval reads back."""
    items = [
        {"query": "q1", "hit": True, "error": None},
        {"query": "q2", "hit": False, "error": None},
        {"query": "q3", "hit": True, "error": None},
    ]
    for item in items:
        tmp_store.dataset_runs.append_eval_item("b1", "run_abc", item)

    loaded = tmp_store.dataset_runs.load_partial_eval("b1", "run_abc")
    assert len(loaded) == 3
    assert loaded[0]["query"] == "q1"
    assert loaded[2]["hit"] is True


def test_finalize_eval_run_removes_partial(tmp_store):
    """finalize_eval_run saves detail file and deletes .partial.jsonl."""
    tmp_store.dataset_runs.append_eval_item(
        "b1", "run_xyz", {"query": "q1", "hit": True, "error": None},
    )
    tmp_store.dataset_runs.append_eval_item(
        "b1", "run_xyz", {"query": "q2", "hit": False, "error": None},
    )

    partial_path = tmp_store.base_dir / "b1" / "dataset_runs" / "run_xyz.partial.jsonl"
    assert partial_path.exists()

    run_data = _make_run_data("run_xyz", "hash_xyz")
    detail_path = tmp_store.dataset_runs.finalize_eval_run("b1", "run_xyz", run_data)

    assert not partial_path.exists()
    assert detail_path.exists()
    entries = tmp_store.dataset_runs.list_all("b1")
    assert any(e["run_id"] == "run_xyz" for e in entries)


def test_list_partial_evals(tmp_store):
    """list_partial_evals returns metadata, finalized runs disappear."""
    tmp_store.dataset_runs.append_eval_item("b1", "alpha_run", {"query": "q1", "hit": True})
    tmp_store.dataset_runs.append_eval_item("b1", "beta_run", {"query": "q1", "hit": True})
    tmp_store.dataset_runs.append_eval_item("b1", "beta_run", {"query": "q2", "hit": False})
    tmp_store.dataset_runs.append_eval_item("b1", "beta_run", {"query": "q3", "hit": True})

    partials = tmp_store.dataset_runs.list_partial_evals("b1")
    assert len(partials) == 2
    assert partials[0]["run_id"] == "alpha_run"
    assert partials[0]["items"] == 1
    assert partials[1]["run_id"] == "beta_run"
    assert partials[1]["items"] == 3

    # Finalized runs disappear
    run_data = _make_run_data("beta_run", "hash_beta")
    tmp_store.dataset_runs.finalize_eval_run("b1", "beta_run", run_data)
    assert len(tmp_store.dataset_runs.list_partial_evals("b1")) == 1


# ---------------------------------------------------------------------------
# Provenance — source field in dataset_runs
# ---------------------------------------------------------------------------


def test_build_dataset_run_data_includes_source():
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    ps = PromptState(instruction="test prompt")
    sp = SearchPoint(prompt_state=ps, model="model", temperature=0.0)
    data = build_dataset_run_data(
        "baseline_aabb", "Baseline", "aabb1122", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
        source="baseline",
    )
    assert data["source"] == "baseline"
    assert data["prompt_state_id"] == ps.id
    assert data["model"] == "model"
    assert data["temperature"] == 0.0


def test_build_dataset_run_data_includes_pipeline_params():
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    ps = PromptState(instruction="test prompt")
    pp = {"steps": ["llm_ranking"], "ranking_temperature": 0.5}
    sp = SearchPoint(prompt_state=ps, pipeline_params=pp)
    data = build_dataset_run_data(
        "run_pp", "PP Test", "hash1234", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
    )
    assert data["pipeline_params"] == pp


def test_build_dataset_run_data_omits_empty_pipeline_params():
    from api.models.prompt_state import PromptState
    from api.models.search_point import SearchPoint
    from api.services.prompt_eval import build_dataset_run_data

    ps = PromptState(instruction="test prompt")
    sp = SearchPoint(prompt_state=ps)
    data = build_dataset_run_data(
        "run_npp", "No PP", "hash5678", sp,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
    )
    assert "pipeline_params" not in data


def test_source_persisted_in_index(tmp_store):
    data = _make_run_data()
    data["source"] = "grid_search"
    tmp_store.dataset_runs.save("b1", data["run_id"], data)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert entries[0]["source"] == "grid_search"
