"""Tests for api/services/prompt_eval.py."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.services.prompt_eval import (
    extract_baseline_prompt,
    filter_eval_data,
    evaluate_prompt_batch,
    compute_accuracy,
)


@pytest.fixture
def query_data():
    return {
        "query": "aspirin",
        "ground_truth": "Acetylsalicylic acid",
        "status": "success",
        "pipeline_data": {
            "entity_profile": {"core_concept": "aspirin", "description": "pain reliever"},
            "token_matched_candidates": ["Acetylsalicylic acid", "Ibuprofen", "Paracetamol"],
        },
    }


@pytest.fixture
def mock_hit_response():
    return json.dumps({
        "ranked_candidates": [
            {"candidate": "Acetylsalicylic acid", "core_concept_score": 0.95}
        ]
    })


def test_extract_baseline_and_filter():
    exp_data = {
        "dependencies": {
            "prompts": {
                "llm_ranking_v1": {
                    "template": "Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
                    "family": "llm_ranking",
                    "version": "1.0",
                    "template_variables": ["core_concept"],
                }
            }
        }
    }
    ps = extract_baseline_prompt(exp_data)
    assert isinstance(ps, PromptState)
    assert "{{core_concept}}" in ps.instruction

    with pytest.raises(RuntimeError):
        extract_baseline_prompt({})

    # filter_eval_data keeps only success + entity_profile
    good = [{"status": "success", "pipeline_data": {"entity_profile": {"x": 1}}}]
    bad = [{"status": "error", "pipeline_data": {}}]
    assert len(filter_eval_data(good + bad)) == 1


@pytest.mark.asyncio
async def test_evaluate_batch_and_delay(query_data, mock_hit_response):
    ps = PromptState(
        instruction="Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}."
    )
    client = MockLLMClient(responses=[mock_hit_response])

    results = await evaluate_prompt_batch(ps, [query_data, query_data], client)
    assert len(results) == 2
    assert all(r["hit"] for r in results)

    # request_delay sleeps between calls (not before the first)
    client2 = MockLLMClient(responses=[mock_hit_response])
    with patch("api.services.prompt_eval.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await evaluate_prompt_batch(
            ps, [query_data, query_data, query_data], client2, request_delay=0.5,
        )
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.5)


def test_compute_accuracy():
    results = [
        {"hit": True, "error": None},
        {"hit": False, "error": "timeout"},
        {"hit": False, "error": None},
    ]
    acc = compute_accuracy(results)
    assert acc["hits"] == 1
    assert acc["errors"] == 1
    assert abs(acc["accuracy"] - 1 / 3) < 1e-9
