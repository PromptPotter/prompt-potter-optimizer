"""Tests for ProjectStore dataset_runs (eval result caching)."""
import json


def _make_run_data(run_id="baseline_aabbccdd", content_hash="aabbccdd11223344", name="Baseline"):
    return {
        "run_id": run_id,
        "name": name,
        "content_hash": content_hash,
        "prompt_state_id": "ps-123",
        "rendered_prompt_hash": "rp-456",
        "model": "test-model",
        "temperature": 0,
        "item_count": 2,
        "scores": {"hits": 1, "total": 2, "accuracy": 0.5, "errors": 0},
        "created_at": "2026-02-22T14:00:00Z",
        "dataset_run_items": [
            {
                "query": "q1", "predicted": "p1", "ground_truth": "p1",
                "hit": True, "confidence": 0.9, "error": None,
            },
            {
                "query": "q2", "predicted": "p2", "ground_truth": "gt2",
                "hit": False, "confidence": 0.3, "error": None,
            },
        ],
    }


def test_save_and_load_by_hash(tmp_store):
    data = _make_run_data()
    path = tmp_store.dataset_runs.save("b1", data["run_id"], data)
    assert path.exists()

    loaded = tmp_store.dataset_runs.load_by_hash("b1", data["content_hash"])
    assert loaded is not None
    assert loaded["run_id"] == data["run_id"]
    assert loaded["scores"]["accuracy"] == 0.5
    assert len(loaded["dataset_run_items"]) == 2


def test_load_miss_returns_none(tmp_store):
    assert tmp_store.dataset_runs.load_by_hash("b1", "nonexistent") is None


def test_load_miss_no_index(tmp_store):
    """No index file at all → None, no crash."""
    assert tmp_store.dataset_runs.load_by_hash("no-backend", "anything") is None


def test_list_dataset_runs(tmp_store):
    run1 = _make_run_data("run_a", "hash_a", "Run A")
    run2 = _make_run_data("run_b", "hash_b", "Run B")

    tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
    tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert len(entries) == 2
    assert entries[0]["run_id"] == "run_a"
    assert entries[1]["run_id"] == "run_b"


def test_list_empty(tmp_store):
    assert tmp_store.dataset_runs.list_all("b1") == []


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
    """Saving with the same content_hash replaces the index entry (no duplicates)."""
    data1 = _make_run_data("run_v1", "same_hash_1234", "V1")
    data2 = _make_run_data("run_v2", "same_hash_1234", "V2")
    data2["scores"]["accuracy"] = 0.75

    tmp_store.dataset_runs.save("b1", data1["run_id"], data1)
    tmp_store.dataset_runs.save("b1", data2["run_id"], data2)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert len(entries) == 1
    assert entries[0]["run_id"] == "run_v2"
    assert entries[0]["scores"]["accuracy"] == 0.75


def test_detail_file_written_correctly(tmp_store):
    data = _make_run_data()
    path = tmp_store.dataset_runs.save("b1", data["run_id"], data)

    detail = json.loads(path.read_text())
    assert detail["run_id"] == data["run_id"]
    assert detail["dataset_run_items"][0]["query"] == "q1"


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


def test_load_partial_eval_empty(tmp_store):
    """load_partial_eval returns empty list when no partial file exists."""
    assert tmp_store.dataset_runs.load_partial_eval("b1", "nonexistent") == []


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


def test_finalize_without_partial(tmp_store):
    """finalize_eval_run works even when no .partial.jsonl exists."""
    run_data = _make_run_data("run_nop", "hash_nop")
    detail_path = tmp_store.dataset_runs.finalize_eval_run("b1", "run_nop", run_data)
    assert detail_path.exists()


# ---------------------------------------------------------------------------
# list_partial_evals
# ---------------------------------------------------------------------------


def test_list_partial_evals_empty(tmp_store):
    """Returns empty list when no partial files exist."""
    assert tmp_store.dataset_runs.list_partial_evals("b1") == []


def test_list_partial_evals_single(tmp_store):
    """Returns metadata for a single partial file."""
    tmp_store.dataset_runs.append_eval_item("b1", "run_abc", {"query": "q1", "hit": True})
    tmp_store.dataset_runs.append_eval_item("b1", "run_abc", {"query": "q2", "hit": False})

    partials = tmp_store.dataset_runs.list_partial_evals("b1")
    assert len(partials) == 1
    assert partials[0]["run_id"] == "run_abc"
    assert partials[0]["items"] == 2
    assert "path" in partials[0]


def test_list_partial_evals_multiple(tmp_store):
    """Returns metadata for multiple partial files, sorted by name."""
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


def test_list_partial_evals_ignores_finalized(tmp_store):
    """Finalized runs no longer appear in list_partial_evals."""
    tmp_store.dataset_runs.append_eval_item("b1", "run_fin", {"query": "q1", "hit": True})
    assert len(tmp_store.dataset_runs.list_partial_evals("b1")) == 1

    run_data = _make_run_data("run_fin", "hash_fin")
    tmp_store.dataset_runs.finalize_eval_run("b1", "run_fin", run_data)

    assert tmp_store.dataset_runs.list_partial_evals("b1") == []


# ---------------------------------------------------------------------------
# Provenance — source field in dataset_runs
# ---------------------------------------------------------------------------


def test_build_dataset_run_data_includes_source():
    from api.services.prompt_eval import build_dataset_run_data
    data = build_dataset_run_data(
        "baseline_aabb", "Baseline", "aabb1122", "ps-1",
        "test prompt", "model", 0.0,
        {"hits": 1, "total": 1, "accuracy": 1.0, "errors": 0}, [],
        source="baseline",
    )
    assert data["source"] == "baseline"


def test_build_dataset_run_data_source_defaults_empty():
    from api.services.prompt_eval import build_dataset_run_data
    data = build_dataset_run_data(
        "run_x", "Run", "hash_x", "ps-1",
        "test prompt", "model", 0.0,
        {"hits": 0, "total": 0, "accuracy": 0.0, "errors": 0}, [],
    )
    assert data["source"] == ""


def test_source_persisted_in_index(tmp_store):
    data = _make_run_data()
    data["source"] = "grid_search"
    tmp_store.dataset_runs.save("b1", data["run_id"], data)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert entries[0]["source"] == "grid_search"


def test_source_missing_defaults_empty_in_index(tmp_store):
    """Legacy runs without source field get empty string in index."""
    data = _make_run_data()
    # No "source" key at all
    tmp_store.dataset_runs.save("b1", data["run_id"], data)

    entries = tmp_store.dataset_runs.list_all("b1")
    assert entries[0]["source"] == ""
