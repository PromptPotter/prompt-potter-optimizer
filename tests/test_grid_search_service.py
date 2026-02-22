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
    EXPLORATION_PRESETS,
    GRID_SEARCHABLE_FIELDS,
    validate_grid_config,
    build_grid_combinations,
    restructure_context,
    run_grid_search,
    analyze_grid_results,
    select_grid_winner,
    load_eval_dataset,
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
        "status": "success",
        "pipeline_data": {
            "entity_profile": {"core_concept": "aspirin"},
            "token_matched_candidates": ["Acetylsalicylic acid", "Ibuprofen"],
        },
    }]


@pytest.fixture
def mock_hit():
    return json.dumps({
        "ranked_candidates": [
            {"candidate": "Acetylsalicylic acid", "core_concept_score": 0.95}
        ]
    })


def test_constants_wellformed():
    """DEFAULT_GRID_AXES and EXPLORATION_PRESETS are consistent."""
    for key in DEFAULT_GRID_AXES:
        assert key in GRID_SEARCHABLE_FIELDS
        assert "" in DEFAULT_GRID_AXES[key]
        assert len(DEFAULT_GRID_AXES[key]) >= 2

    for name, axes in EXPLORATION_PRESETS.items():
        assert not (set(axes.keys()) - GRID_SEARCHABLE_FIELDS)


def test_validate_and_build(baseline):
    config = {"persona": ["", "Expert"], "thinking_style": ["", "Step by step"]}
    meta = validate_grid_config(config, baseline)
    assert meta["total"] == 4

    combos, lookup = build_grid_combinations(config, baseline)
    assert len(combos) == 4
    for _, ps_id in combos:
        assert isinstance(lookup[ps_id], PromptState)
        assert lookup[ps_id].parent_id == baseline.id

    # Invalid field rejected
    with pytest.raises(ValueError):
        validate_grid_config({"bogus": ["a", "b"]}, baseline)

    # Subsampling
    combos2, _ = build_grid_combinations(config, baseline, max_combinations=2)
    assert len(combos2) == 2


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
async def test_run_grid_search_and_delay(baseline, eval_data, mock_hit):
    config = {"persona": ["", "Expert"]}
    combos, lookup = build_grid_combinations(config, baseline)
    client = MockLLMClient(responses=[mock_hit])

    df = await run_grid_search(combos, lookup, eval_data, client, request_delay=0)
    assert len(df) == 2
    assert "accuracy" in df.columns

    # request_delay is passed through to evaluate_prompt_batch
    with patch("api.services.grid_search.evaluate_prompt_batch", new_callable=AsyncMock) as mock_batch:
        mock_batch.return_value = [{"hit": True, "error": None}]
        await run_grid_search(combos, lookup, eval_data, client, request_delay=2.5)
        for call in mock_batch.call_args_list:
            assert call.kwargs["request_delay"] == 2.5


def test_select_grid_winner(baseline):
    config = {"persona": ["", "Expert"]}
    _, lookup = build_grid_combinations(config, baseline)
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
async def test_grid_search_caches_and_reuses(baseline, eval_data, mock_hit, tmp_store):
    """First run saves to cache; second run skips LLM calls entirely."""
    tmp_store.register_backend(BackendConnection(
        id="b1", name="Test", backend_type="test", base_url="http://test",
    ))

    config = {"persona": ["", "Expert"]}
    combos, lookup = build_grid_combinations(config, baseline)
    client = MockLLMClient(responses=[mock_hit])

    # First run — evaluates all combos and saves to cache
    df1 = await run_grid_search(
        combos, lookup, eval_data, client,
        request_delay=0, store=tmp_store, backend_id="b1",
    )
    assert len(df1) == 2
    entries = tmp_store.list_dataset_runs("b1")
    assert len(entries) == 2

    # Record call count after first run
    calls_after_first = client._call_count

    # Second run — should hit cache, no new LLM calls
    df2 = await run_grid_search(
        combos, lookup, eval_data, client,
        request_delay=0, store=tmp_store, backend_id="b1",
    )
    assert len(df2) == 2
    assert client._call_count == calls_after_first  # no new calls

    # Accuracy values should match between runs
    acc1 = df1.sort_values("prompt_state_id")["accuracy"].tolist()
    acc2 = df2.sort_values("prompt_state_id")["accuracy"].tolist()
    assert acc1 == acc2


@pytest.mark.asyncio
async def test_grid_search_resumes_partial(baseline, eval_data, mock_hit, tmp_store):
    """Grid search resumes from a partial .jsonl instead of re-evaluating."""
    tmp_store.register_backend(BackendConnection(
        id="b1", name="Test", backend_type="test", base_url="http://test",
    ))

    config = {"persona": ["", "Expert"]}
    combos, lookup = build_grid_combinations(config, baseline)
    client = MockLLMClient(responses=[mock_hit])

    # Pre-write a partial result for the first combo
    ps_first = lookup[combos[0][1]]
    rendered = ps_first.render()
    from api.services.prompt_eval import eval_cache_key

    content_hash = eval_cache_key(rendered, eval_data, "", 0.1)
    run_id = f"grid_{content_hash[:8]}"

    # Write a partial file with the single eval_data item already done
    pre_result = {
        "query": "aspirin",
        "predicted": "Acetylsalicylic acid",
        "ground_truth": "Acetylsalicylic acid",
        "hit": True,
        "confidence": 0.95,
        "error": None,
    }
    tmp_store.append_eval_item("b1", run_id, pre_result)

    calls_before = client._call_count

    df = await run_grid_search(
        combos, lookup, eval_data, client,
        request_delay=0, store=tmp_store, backend_id="b1",
    )
    assert len(df) == 2

    # The first combo should have been resumed from partial (no LLM call for it).
    # The second combo needs 1 LLM call (1 eval_data item).
    assert client._call_count == calls_before + 1

    # Partial file should be cleaned up after finalize
    partial_path = tmp_store._dataset_runs_dir("b1") / f"{run_id}.partial.jsonl"
    assert not partial_path.exists()

    # Both combos should be in the cache now
    entries = tmp_store.list_dataset_runs("b1")
    assert len(entries) == 2


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
