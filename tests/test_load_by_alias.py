"""Tests for DatasetRunStore.load_by_alias()."""

import pytest

from api.services.stores.dataset_run_store import DatasetRunStore


@pytest.fixture()
def drs(tmp_path):
    return DatasetRunStore(tmp_path)


def _make_run(run_id, rp_hash, model="m1", temperature=0.5,
              pipeline_params=None, item_count=3):
    return {
        "run_id": run_id,
        "name": run_id,
        "content_hash": f"ch_{run_id}",
        "prompt_state_id": "ps1",
        "rendered_prompt_hash": rp_hash,
        "model": model,
        "temperature": temperature,
        "item_count": item_count,
        "scores": {"accuracy": 0.8, "hits": 2, "total": item_count},
        "source": "test",
        "created_at": "2026-01-01T00:00:00Z",
        "dataset_run_items": [{"query": f"q{i}"} for i in range(item_count)],
        **({"pipeline_params": pipeline_params} if pipeline_params else {}),
    }


def test_load_exact_alias_match(drs):
    """Save with rp_hash A, register alias(A, B), lookup via B → hit."""
    drs.save("b1", "r1", _make_run("r1", "hash_a"))
    drs.register_alias("b1", "hash_a", "hash_b")

    result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, None, 3)
    assert result is not None
    assert result["run_id"] == "r1"


def test_no_alias_returns_none(drs):
    """No alias registered → None."""
    drs.save("b1", "r1", _make_run("r1", "hash_a"))

    result = drs.load_by_alias("b1", "hash_x", "m1", 0.5, None, 3)
    assert result is None


def test_model_mismatch_returns_none(drs):
    """Alias matches but model differs → None."""
    drs.save("b1", "r1", _make_run("r1", "hash_a"))
    drs.register_alias("b1", "hash_a", "hash_b")

    result = drs.load_by_alias("b1", "hash_b", "wrong_model", 0.5, None, 3)
    assert result is None


def test_steps_only_difference_matches(drs):
    """Alias matches and only 'steps' differs → hit (steps-blind)."""
    drs.save("b1", "r1", _make_run("r1", "hash_a", pipeline_params={"steps": ["a"]}))
    drs.register_alias("b1", "hash_a", "hash_b")

    result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, {"steps": ["b"]}, 3)
    assert result is not None
    assert result["run_id"] == "r1"


def test_non_steps_pipeline_params_mismatch(drs):
    """Alias matches but non-steps pipeline_params differ → None."""
    drs.save("b1", "r1", _make_run(
        "r1", "hash_a", pipeline_params={"steps": ["a"], "ranking_temperature": 0.5},
    ))
    drs.register_alias("b1", "hash_a", "hash_b")

    result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, {"ranking_temperature": 0.9}, 3)
    assert result is None


def test_item_count_mismatch(drs):
    """Alias matches but item_count differs → None."""
    drs.save("b1", "r1", _make_run("r1", "hash_a"))
    drs.register_alias("b1", "hash_a", "hash_b")

    result = drs.load_by_alias("b1", "hash_b", "m1", 0.5, None, 99)
    assert result is None
