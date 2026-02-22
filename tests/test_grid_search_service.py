"""Tests for api/services/grid_search.py."""
import json
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from api.models.backend import BackendConnection
from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.services.grid_search import (
    DEFAULT_GRID_AXES,
    SAMPLING_ALPHA,
    GRID_SEARCHABLE_FIELDS,
    validate_grid_config,
    build_grid_combinations,
    restructure_context,
    run_grid_search,
    analyze_grid_results,
    select_grid_winner,
    load_eval_dataset,
    _combo_distance,
    _allocate_budget,
)


@pytest.fixture
def baseline():
    return PromptState(
        instruction=(
            "Rank candidates for {{core_concept}} "
            "given {{entity_profile_json}} and {{matches}}."
        ),
        changes_description="test baseline",
    )


@pytest.fixture
def eval_data():
    return [{
        "query": "aspirin",
        "ground_truth": "Acetylsalicylic acid",
    }]


def _make_backend_client(predicted="Acetylsalicylic acid"):
    """Create an AsyncMock backend_client that returns a hit for the given predicted value."""
    client = AsyncMock()
    client.run_match.return_value = {
        "data": {
            "ranked_candidates": [
                {"candidate": predicted, "core_concept_score": 0.95}
            ]
        }
    }
    return client


def test_constants_wellformed():
    """DEFAULT_GRID_AXES keys are valid and SAMPLING_ALPHA is positive."""
    for key in DEFAULT_GRID_AXES:
        assert key in GRID_SEARCHABLE_FIELDS
        assert "" in DEFAULT_GRID_AXES[key]
        assert len(DEFAULT_GRID_AXES[key]) >= 2

    assert SAMPLING_ALPHA > 0


def test_combo_distance():
    """_combo_distance counts non-empty values."""
    assert _combo_distance(("", "", "")) == 0
    assert _combo_distance(("a", "", "b")) == 2
    assert _combo_distance(("a", "b", "c")) == 3


def test_validate_and_build(baseline):
    config = {"persona": ["", "Expert"], "thinking_style": ["", "Step by step"]}
    meta = validate_grid_config(config, baseline)
    assert meta["total"] == 4

    combos, lookup, sampling_meta = build_grid_combinations(config, baseline)
    assert len(combos) == 4
    assert sampling_meta["total_space"] == 4
    assert sampling_meta["n_selected"] == 4
    for _, ps_id in combos:
        assert isinstance(lookup[ps_id], PromptState)
        assert lookup[ps_id].parent_id == baseline.id

    # Invalid field rejected
    with pytest.raises(ValueError):
        validate_grid_config({"bogus": ["a", "b"]}, baseline)

    # Subsampling with n_combos
    combos2, _, meta2 = build_grid_combinations(config, baseline, n_combos=2)
    assert len(combos2) == 2
    assert meta2["n_selected"] == 2
    assert meta2["total_space"] == 4


def test_validate_n_combos_report(baseline):
    """validate_grid_config reports capped status correctly."""
    config = {"persona": ["", "Expert"], "thinking_style": ["", "Step by step"]}

    meta_sub = validate_grid_config(config, baseline, n_combos=2)
    assert meta_sub["is_subsampled"] is True
    assert meta_sub["actual_count"] == 2
    assert meta_sub["capped"] is False

    meta_cap = validate_grid_config(config, baseline, n_combos=100)
    assert meta_cap["is_subsampled"] is False
    assert meta_cap["capped"] is True
    assert meta_cap["actual_count"] == 4


@pytest.mark.asyncio
async def test_restructure_context():
    mock_resp = json.dumps({
        "persona": "Domain expert", "task_intent": "Rank candidates",
        "problem_description": "", "instruction": "", "thinking_style": "", "answer_format": "",
    })
    client = MockLLMClient(responses=[mock_resp])
    result = await restructure_context("normalize terms", client)
    assert result["persona"] == "Domain expert"
    assert all(k in result for k in ("persona", "task_intent", "instruction"))


@pytest.mark.asyncio
async def test_run_grid_search_backend(baseline, eval_data):
    """Grid search evaluates combos via backend_client."""
    config = {"persona": ["", "Expert"]}
    combos, lookup, _ = build_grid_combinations(config, baseline)
    backend_client = _make_backend_client()

    df = await run_grid_search(combos, lookup, eval_data, backend_client, request_delay=0)
    assert len(df) == 2
    assert "accuracy" in df.columns
    # 2 combos × 1 query each = 2 backend calls
    assert backend_client.run_match.call_count == 2


def test_select_grid_winner(baseline):
    config = {"persona": ["", "Expert"]}
    _, lookup, _ = build_grid_combinations(config, baseline)
    ps_ids = list(lookup.keys())

    grid_df = pd.DataFrame([
        {"persona": 1, "accuracy": 0.9, "hits": 9, "total": 10,
         "errors": 0, "prompt_state_id": ps_ids[0]},
        {"persona": 0, "accuracy": 0.5, "hits": 5, "total": 10,
         "errors": 0, "prompt_state_id": ps_ids[1]},
    ])

    result = select_grid_winner(grid_df, lookup)
    assert result["accuracy"] == 0.9
    assert result["round"] == "grid"


@pytest.mark.asyncio
async def test_grid_search_caches_and_reuses(baseline, eval_data, tmp_store):
    """First run saves to cache; second run skips backend calls entirely."""
    tmp_store.register_backend(BackendConnection(
        id="b1", name="Test", backend_type="test", base_url="http://test",
    ))

    config = {"persona": ["", "Expert"]}
    combos, lookup, _ = build_grid_combinations(config, baseline)
    backend_client = _make_backend_client()

    # First run — evaluates all combos and saves to cache
    df1 = await run_grid_search(
        combos, lookup, eval_data, backend_client,
        request_delay=0, store=tmp_store, backend_id="b1",
    )
    assert len(df1) == 2
    entries = tmp_store.list_dataset_runs("b1")
    assert len(entries) == 2

    # Record call count after first run
    calls_after_first = backend_client.run_match.call_count

    # Second run — should hit cache, no new backend calls
    df2 = await run_grid_search(
        combos, lookup, eval_data, backend_client,
        request_delay=0, store=tmp_store, backend_id="b1",
    )
    assert len(df2) == 2
    assert backend_client.run_match.call_count == calls_after_first  # no new calls

    # Accuracy values should match between runs
    acc1 = df1.sort_values("prompt_state_id")["accuracy"].tolist()
    acc2 = df2.sort_values("prompt_state_id")["accuracy"].tolist()
    assert acc1 == acc2


@pytest.mark.asyncio
async def test_grid_search_resumes_partial(baseline, eval_data, tmp_store):
    """Grid search resumes from a partial .jsonl instead of re-evaluating."""
    tmp_store.register_backend(BackendConnection(
        id="b1", name="Test", backend_type="test", base_url="http://test",
    ))

    config = {"persona": ["", "Expert"]}
    combos, lookup, _ = build_grid_combinations(config, baseline)
    backend_client = _make_backend_client()

    # Pre-write a partial result for the first combo
    ps_first = lookup[combos[0][1]]
    rendered = ps_first.render()
    from api.services.prompt_eval import eval_cache_key

    content_hash = eval_cache_key(rendered, eval_data, "", 0.0)
    run_id = f"grid_{content_hash[:8]}"

    # Write a partial file with the single eval_data item already done
    pre_result = {
        "query": "aspirin",
        "predicted": "Acetylsalicylic acid",
        "ground_truth": "Acetylsalicylic acid",
        "hit": True,
        "error": None,
    }
    tmp_store.append_eval_item("b1", run_id, pre_result)

    calls_before = backend_client.run_match.call_count

    df = await run_grid_search(
        combos, lookup, eval_data, backend_client,
        request_delay=0, store=tmp_store, backend_id="b1",
    )
    assert len(df) == 2

    # The first combo should have been resumed from partial (no backend call for it).
    # The second combo needs 1 backend call (1 eval_data item).
    assert backend_client.run_match.call_count == calls_before + 1

    # Partial file should be cleaned up after finalize
    partial_path = tmp_store._dataset_runs_dir("b1") / f"{run_id}.partial.jsonl"
    assert not partial_path.exists()

    # Both combos should be in the cache now
    entries = tmp_store.list_dataset_runs("b1")
    assert len(entries) == 2


def test_exploration_rate_distance_bias(baseline):
    """exploration_rate=0.0 favors low distance, 1.0 favors high distance."""
    config = {
        "persona": ["", "Expert A", "Expert B"],
        "thinking_style": ["", "Step by step", "Focus on semantics"],
        "task_intent": ["", "Identify the best match"],
    }
    # 3 * 3 * 2 = 18 total combos

    # Conservative: favor low distance (many empty fields)
    combos_low, _, meta_low = build_grid_combinations(
        config, baseline, n_combos=6, exploration_rate=0.0, seed=42,
    )
    distances_low = []
    for combo_dict, _ in combos_low:
        d = sum(1 for v in combo_dict.values() if v > 0)
        distances_low.append(d)

    # Aggressive: favor high distance (many filled fields)
    combos_high, _, meta_high = build_grid_combinations(
        config, baseline, n_combos=6, exploration_rate=1.0, seed=42,
    )
    distances_high = []
    for combo_dict, _ in combos_high:
        d = sum(1 for v in combo_dict.values() if v > 0)
        distances_high.append(d)

    # Low exploration should have lower average distance than high
    avg_low = sum(distances_low) / len(distances_low)
    avg_high = sum(distances_high) / len(distances_high)
    assert avg_low < avg_high, (
        f"Expected low exploration avg distance ({avg_low}) < "
        f"high exploration avg distance ({avg_high})"
    )


def test_n_combos_capped_at_full_space(baseline):
    """When n_combos >= total space, return full grid and set capped=True."""
    config = {"persona": ["", "Expert"], "thinking_style": ["", "Step by step"]}
    # 2 * 2 = 4 combos

    combos, lookup, meta = build_grid_combinations(config, baseline, n_combos=100)
    assert len(combos) == 4
    assert meta["capped"] is True
    assert meta["total_space"] == 4
    assert meta["n_selected"] == 4


def test_exploration_rate_reproducible(baseline):
    """Same seed produces same sampling result."""
    config = {
        "persona": ["", "Expert A", "Expert B"],
        "thinking_style": ["", "Step by step", "Focus on semantics"],
    }

    combos_a, _, _ = build_grid_combinations(
        config, baseline, n_combos=4, exploration_rate=0.5, seed=99,
    )
    combos_b, _, _ = build_grid_combinations(
        config, baseline, n_combos=4, exploration_rate=0.5, seed=99,
    )

    ids_a = [ps_id for _, ps_id in combos_a]
    ids_b = [ps_id for _, ps_id in combos_b]
    # Note: ps_ids will differ because derive() generates new UUIDs,
    # but the coord_dicts should be identical
    coords_a = [cd for cd, _ in combos_a]
    coords_b = [cd for cd, _ in combos_b]
    assert coords_a == coords_b


def test_load_eval_dataset(tmp_store):
    tmp_store.register_backend(BackendConnection(
        id="test", name="Test", backend_type="test", base_url="http://test",
    ))

    exp_data = {
        "mappings": [
            {"bom_material": "aspirin", "dataset_entry": "Acetylsalicylic acid"},
        ],
        "runs": [{"traces": [{
            "id": "t1",
            "input": {"query": "aspirin/proc"},
            "output": {},
            "observations": [
                {"name": "entity_profiling", "output": {"core_concept": "aspirin"}},
                {"name": "token_matching", "output": {"candidates": [["Acetylsalicylic acid", 0.9]]}},
            ],
        }]}],
    }
    tmp_store.save_sync("test", "experiments/exp1.json", exp_data)

    result = load_eval_dataset(tmp_store, "test", "exp1")
    assert len(result) == 1
    assert result[0]["ground_truth"] == "Acetylsalicylic acid"
