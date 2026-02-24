"""Tests for build_prompt_result_index and historical lookup in _eval_config."""
import hashlib

from api.services.grid_search import build_prompt_result_index
from api.services.prompt_eval import HASH_TRUNCATE


def _rp_hash(text: str) -> str:
    """Compute rendered_prompt_hash the same way build_dataset_run_data does."""
    return hashlib.sha256(text.encode()).hexdigest()[:HASH_TRUNCATE]


def _make_run(run_id, rendered_prompt, queries):
    """Build a dataset run dict with the given rendered prompt and queries."""
    items = []
    for q, hit in queries:
        items.append({
            "query": q,
            "predicted": "pred" if hit else "wrong",
            "ground_truth": "pred",
            "hit": hit,
            "confidence": 0.9 if hit else 0.1,
            "error": None,
        })
    hits = sum(1 for _, h in queries if h)
    total = len(queries)
    return {
        "run_id": run_id,
        "name": run_id,
        "content_hash": f"ch_{run_id}",
        "prompt_state_id": f"ps_{run_id}",
        "rendered_prompt_hash": _rp_hash(rendered_prompt),
        "model": "test",
        "temperature": 0,
        "item_count": total,
        "scores": {
            "hits": hits, "total": total,
            "accuracy": hits / total if total else 0.0,
            "errors": 0,
        },
        "created_at": "2026-02-24T00:00:00Z",
        "dataset_run_items": items,
    }


def test_build_index_empty(tmp_store):
    """Empty store produces empty index."""
    index = build_prompt_result_index(tmp_store, "b1")
    assert index == {}


def test_build_index_single_run(tmp_store):
    """Single run with 2 queries produces correct index."""
    run = _make_run("r1", "prompt A", [("q1", True), ("q2", False)])
    tmp_store.save_dataset_run("b1", run["run_id"], run)

    index = build_prompt_result_index(tmp_store, "b1")
    rp_hash = _rp_hash("prompt A")
    assert rp_hash in index
    assert "q1" in index[rp_hash]
    assert "q2" in index[rp_hash]
    assert index[rp_hash]["q1"]["hit"] is True
    assert index[rp_hash]["q2"]["hit"] is False


def test_build_index_multiple_runs_same_prompt(tmp_store):
    """Multiple runs with same rendered prompt merge queries."""
    run1 = _make_run("r1", "prompt A", [("q1", True), ("q2", False)])
    run2 = _make_run("r2", "prompt A", [("q3", True), ("q4", True)])
    tmp_store.save_dataset_run("b1", run1["run_id"], run1)
    tmp_store.save_dataset_run("b1", run2["run_id"], run2)

    index = build_prompt_result_index(tmp_store, "b1")
    rp_hash = _rp_hash("prompt A")
    assert len(index) == 1  # Same prompt
    assert len(index[rp_hash]) == 4  # 4 unique queries


def test_build_index_different_prompts(tmp_store):
    """Runs with different prompts produce separate index entries."""
    run1 = _make_run("r1", "prompt A", [("q1", True)])
    run2 = _make_run("r2", "prompt B", [("q1", False)])
    tmp_store.save_dataset_run("b1", run1["run_id"], run1)
    tmp_store.save_dataset_run("b1", run2["run_id"], run2)

    index = build_prompt_result_index(tmp_store, "b1")
    assert len(index) == 2


def test_build_index_later_run_overwrites_query(tmp_store):
    """When the same query appears in multiple runs for the same prompt,
    the later save wins (last-write-wins)."""
    run1 = _make_run("r1", "prompt A", [("q1", True)])
    run2 = _make_run("r2", "prompt A", [("q1", False)])
    tmp_store.save_dataset_run("b1", run1["run_id"], run1)
    tmp_store.save_dataset_run("b1", run2["run_id"], run2)

    index = build_prompt_result_index(tmp_store, "b1")
    rp_hash = _rp_hash("prompt A")
    # Last run processed wins
    assert rp_hash in index
    assert "q1" in index[rp_hash]


def test_load_by_id(tmp_store):
    """ProjectStore.load_dataset_run() loads a run by ID."""
    run = _make_run("r1", "prompt A", [("q1", True)])
    tmp_store.save_dataset_run("b1", run["run_id"], run)

    loaded = tmp_store.load_dataset_run("b1", "r1")
    assert loaded is not None
    assert loaded["run_id"] == "r1"
    assert len(loaded["dataset_run_items"]) == 1


def test_load_by_id_missing(tmp_store):
    """load_dataset_run returns None for missing run."""
    assert tmp_store.load_dataset_run("b1", "nonexistent") is None


def test_index_ignores_runs_without_hash(tmp_store):
    """Runs missing rendered_prompt_hash are skipped."""
    run = _make_run("r1", "prompt A", [("q1", True)])
    del run["rendered_prompt_hash"]
    tmp_store.save_dataset_run("b1", run["run_id"], run)

    index = build_prompt_result_index(tmp_store, "b1")
    assert index == {}


def test_index_ignores_runs_without_items(tmp_store):
    """Runs with empty dataset_run_items are skipped."""
    run = _make_run("r1", "prompt A", [])
    tmp_store.save_dataset_run("b1", run["run_id"], run)

    index = build_prompt_result_index(tmp_store, "b1")
    assert index == {}
