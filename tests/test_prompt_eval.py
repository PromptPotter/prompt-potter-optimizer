"""Tests for api/services/prompt_eval.py."""
from unittest.mock import AsyncMock, patch

import pytest

from api.models.prompt_state import PromptState
from api.services.prompt_eval import (
    extract_baseline_prompt,
    filter_eval_data,
    backend_reranker_eval,
    evaluate_prompt_batch,
    compute_accuracy,
)


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
async def test_backend_reranker_eval():
    """backend_reranker_eval returns hit when backend predicts correctly."""
    mock_client = AsyncMock()
    mock_client.run_match.return_value = {
        "data": {
            "ranked_candidates": [
                {"candidate": "Acetylsalicylic acid", "core_concept_score": 0.95}
            ]
        }
    }

    qd = {"query": "aspirin", "ground_truth": "Acetylsalicylic acid"}
    result = await backend_reranker_eval(qd, mock_client, "rendered prompt")

    assert result["hit"] is True
    assert result["predicted"] == "Acetylsalicylic acid"
    assert result["error"] is None

    # Verify run_match was called correctly
    mock_client.run_match.assert_called_once_with(
        "aspirin",
        skip_llm_ranking=False,
        pipeline_params=None,
        ranking_prompt="rendered prompt",
    )


@pytest.mark.asyncio
async def test_backend_reranker_eval_miss():
    """backend_reranker_eval returns miss when backend predicts incorrectly."""
    mock_client = AsyncMock()
    mock_client.run_match.return_value = {
        "data": {
            "ranked_candidates": [
                {"candidate": "Ibuprofen", "core_concept_score": 0.7}
            ]
        }
    }

    qd = {"query": "aspirin", "ground_truth": "Acetylsalicylic acid"}
    result = await backend_reranker_eval(qd, mock_client, "rendered prompt")

    assert result["hit"] is False
    assert result["predicted"] == "Ibuprofen"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_backend_reranker_eval_error():
    """backend_reranker_eval handles exceptions gracefully."""
    mock_client = AsyncMock()
    mock_client.run_match.side_effect = RuntimeError("connection refused")

    qd = {"query": "aspirin", "ground_truth": "Acetylsalicylic acid"}
    result = await backend_reranker_eval(qd, mock_client, "rendered prompt")

    assert result["hit"] is False
    assert result["predicted"] == "ERROR"
    assert "connection refused" in result["error"]


@pytest.mark.asyncio
async def test_evaluate_prompt_batch_backend():
    """evaluate_prompt_batch calls backend for each query."""
    mock_client = AsyncMock()
    mock_client.run_match.return_value = {
        "data": {
            "ranked_candidates": [
                {"candidate": "Acetylsalicylic acid", "core_concept_score": 0.95}
            ]
        }
    }

    ps = PromptState(
        instruction="Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}."
    )
    eval_data = [
        {"query": "aspirin", "ground_truth": "Acetylsalicylic acid"},
        {"query": "tylenol", "ground_truth": "Paracetamol"},
    ]

    results = await evaluate_prompt_batch(ps, eval_data, mock_client)
    assert len(results) == 2
    assert mock_client.run_match.call_count == 2

    # request_delay sleeps between calls (not before the first)
    mock_client2 = AsyncMock()
    mock_client2.run_match.return_value = {
        "data": {"ranked_candidates": [{"candidate": "X"}]}
    }
    with patch("api.services.prompt_eval.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await evaluate_prompt_batch(
            ps, eval_data + [eval_data[0]], mock_client2, request_delay=0.5,
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
