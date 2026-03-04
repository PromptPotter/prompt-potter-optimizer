"""Tests for build_prompt_result_index and historical lookup."""

from api.services.search import build_prompt_result_index

from _helpers import rp_hash as _rp_hash, make_dataset_run


def _make_run(run_id, rendered_prompt, queries):
    items = [
        {"query": q, "predicted": "pred" if hit else "wrong",
         "ground_truth": "pred", "hit": hit,
         "confidence": 0.9 if hit else 0.1, "error": None}
        for q, hit in queries
    ]
    hits = sum(1 for _, h in queries if h)
    total = len(queries)
    return make_dataset_run(
        run_id, accuracy=hits / total if total else 0.0,
        items=items, content_hash=f"ch_{run_id}",
        rendered_prompt=rendered_prompt,
    )


def test_build_and_load_single_run(tmp_store):
    """Single run: correct index + load_by_id works."""
    run = _make_run("r1", "prompt A", [("q1", True), ("q2", False)])
    tmp_store.dataset_runs.save("b1", run["run_id"], run)

    index = build_prompt_result_index(tmp_store, "b1")
    rp_hash = _rp_hash("prompt A")
    assert rp_hash in index
    assert index[rp_hash]["q1"]["hit"] is True
    assert index[rp_hash]["q2"]["hit"] is False

    loaded = tmp_store.dataset_runs.load_by_id("b1", "r1")
    assert loaded is not None
    assert loaded["run_id"] == "r1"


def test_build_index_multiple_runs_same_prompt(tmp_store):
    """Multiple runs with same rendered prompt merge queries."""
    run1 = _make_run("r1", "prompt A", [("q1", True), ("q2", False)])
    run2 = _make_run("r2", "prompt A", [("q3", True), ("q4", True)])
    tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
    tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

    index = build_prompt_result_index(tmp_store, "b1")
    rp_hash = _rp_hash("prompt A")
    assert len(index) == 1
    assert len(index[rp_hash]) == 4


def test_build_index_different_prompts(tmp_store):
    """Runs with different prompts produce separate index entries."""
    run1 = _make_run("r1", "prompt A", [("q1", True)])
    run2 = _make_run("r2", "prompt B", [("q1", False)])
    tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
    tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

    index = build_prompt_result_index(tmp_store, "b1")
    assert len(index) == 2


def test_build_index_later_run_overwrites_query(tmp_store):
    """Same query in multiple runs for same prompt: last-write-wins."""
    run1 = _make_run("r1", "prompt A", [("q1", True)])
    run2 = _make_run("r2", "prompt A", [("q1", False)])
    tmp_store.dataset_runs.save("b1", run1["run_id"], run1)
    tmp_store.dataset_runs.save("b1", run2["run_id"], run2)

    index = build_prompt_result_index(tmp_store, "b1")
    rp_hash = _rp_hash("prompt A")
    assert rp_hash in index
    assert "q1" in index[rp_hash]


def test_index_ignores_runs_without_hash(tmp_store):
    """Runs missing rendered_prompt_hash are skipped."""
    run = _make_run("r1", "prompt A", [("q1", True)])
    del run["rendered_prompt_hash"]
    tmp_store.dataset_runs.save("b1", run["run_id"], run)

    index = build_prompt_result_index(tmp_store, "b1")
    assert index == {}
