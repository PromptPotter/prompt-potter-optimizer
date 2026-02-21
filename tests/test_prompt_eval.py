"""Tests for api/services/prompt_eval.py."""
import json

import pytest

from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.services.prompt_eval import (
    extract_baseline_prompt,
    filter_eval_data,
    local_reranker_eval,
    evaluate_prompt_batch,
    compute_accuracy,
    _render_reranker_input,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def exp_data():
    """Experiment data with an llm_ranking prompt."""
    return {
        "dependencies": {
            "prompts": {
                "llm_ranking_v1": {
                    "template": (
                        "Rank {{core_concept}} given "
                        "{{entity_profile_json}} and {{matches}}."
                    ),
                    "family": "llm_ranking",
                    "version": "1.0",
                    "template_variables": ["core_concept", "entity_profile_json", "matches"],
                }
            }
        }
    }


@pytest.fixture
def query_data():
    """A single query with pipeline_data for reranker eval."""
    return {
        "query": "aspirin",
        "ground_truth": "Acetylsalicylic acid",
        "status": "success",
        "pipeline_data": {
            "entity_profile": {
                "core_concept": "aspirin",
                "description": "pain reliever",
            },
            "token_matched_candidates": [
                "Acetylsalicylic acid",
                "Ibuprofen",
                "Paracetamol",
            ],
        },
    }


@pytest.fixture
def mock_reranker_response():
    """Mock LLM response for reranker evaluation."""
    return json.dumps({
        "profile_summary": "A pain reliever",
        "core_concept_description": "aspirin",
        "ranked_candidates": [
            {
                "candidate": "Acetylsalicylic acid",
                "core_concept_score": 0.95,
                "spec_score": 0.9,
                "evaluation_reasoning": "Direct match",
                "key_match_factors": ["name"],
                "spec_gaps": [],
            }
        ],
    })


# ---------------------------------------------------------------------------
# extract_baseline_prompt
# ---------------------------------------------------------------------------


class TestExtractBaselinePrompt:
    def test_extracts_prompt(self, exp_data):
        ps = extract_baseline_prompt(exp_data)
        assert isinstance(ps, PromptState)
        assert "{{core_concept}}" in ps.instruction
        assert ps.parameters["family"] == "llm_ranking"
        assert ps.parameters["version"] == "1.0"
        assert ps.changes_description == "Baseline reranker_v1 from TermNorm prompt registry"

    def test_raises_when_no_prompt(self):
        with pytest.raises(RuntimeError, match="No llm_ranking prompt"):
            extract_baseline_prompt({"dependencies": {"prompts": {}}})

    def test_raises_when_no_dependencies(self):
        with pytest.raises(RuntimeError, match="No llm_ranking prompt"):
            extract_baseline_prompt({})


# ---------------------------------------------------------------------------
# filter_eval_data
# ---------------------------------------------------------------------------


class TestFilterEvalData:
    def test_filters_correctly(self, query_data):
        good = [query_data]
        bad_no_profile = [{
            "status": "success",
            "pipeline_data": {"token_matched_candidates": []},
        }]
        bad_error = [{"status": "error", "pipeline_data": {}}]

        result = filter_eval_data(good + bad_no_profile + bad_error)
        assert len(result) == 1
        assert result[0]["query"] == "aspirin"

    def test_returns_empty_for_no_matches(self):
        assert filter_eval_data([{"status": "error"}]) == []

    def test_returns_empty_for_empty_input(self):
        assert filter_eval_data([]) == []


# ---------------------------------------------------------------------------
# _render_reranker_input
# ---------------------------------------------------------------------------


class TestRenderRerankerInput:
    def test_renders_template(self, query_data):
        template = (
            "Concept: {{core_concept}}\n"
            "Profile: {{entity_profile_json}}\n"
            "Matches: {{matches}}"
        )
        full_prompt, query, gt = _render_reranker_input(template, query_data)

        assert "aspirin" in full_prompt
        assert "pain reliever" in full_prompt
        assert query == "aspirin"
        assert gt == "Acetylsalicylic acid"
        assert "ranked_candidates" in full_prompt  # JSON format instructions


# ---------------------------------------------------------------------------
# local_reranker_eval
# ---------------------------------------------------------------------------


class TestLocalRerankerEval:
    @pytest.mark.asyncio
    async def test_hit(self, query_data, mock_reranker_response):
        client = MockLLMClient(responses=[mock_reranker_response])
        result = await local_reranker_eval(
            "Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
            query_data,
            client,
        )
        assert result["hit"] is True
        assert result["predicted"] == "Acetylsalicylic acid"
        assert result["ground_truth"] == "Acetylsalicylic acid"
        assert result["error"] is None

    @pytest.mark.asyncio
    async def test_miss(self, query_data):
        miss_response = json.dumps({
            "ranked_candidates": [
                {"candidate": "Ibuprofen", "core_concept_score": 0.5}
            ]
        })
        client = MockLLMClient(responses=[miss_response])
        result = await local_reranker_eval(
            "Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
            query_data,
            client,
        )
        assert result["hit"] is False
        assert result["predicted"] == "Ibuprofen"

    @pytest.mark.asyncio
    async def test_error_handling(self, query_data):
        client = MockLLMClient(responses=["not valid json at all"])
        result = await local_reranker_eval(
            "Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
            query_data,
            client,
        )
        # MockLLMClient with output_format="json" will try to parse,
        # parsed will be None, and the function falls back to json.loads
        # which will raise, caught by the except block
        assert result["error"] is not None or result["predicted"] == "NO_RESULT"


# ---------------------------------------------------------------------------
# evaluate_prompt_batch
# ---------------------------------------------------------------------------


class TestEvaluatePromptBatch:
    @pytest.mark.asyncio
    async def test_evaluates_all_queries(self, query_data, mock_reranker_response):
        ps = PromptState(
            instruction="Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}."
        )
        client = MockLLMClient(responses=[mock_reranker_response])
        eval_data = [query_data, query_data]

        results = await evaluate_prompt_batch(ps, eval_data, client)
        assert len(results) == 2
        assert all(r["hit"] for r in results)

    @pytest.mark.asyncio
    async def test_on_result_callback(self, query_data, mock_reranker_response):
        ps = PromptState(
            instruction="Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}."
        )
        client = MockLLMClient(responses=[mock_reranker_response])

        callback_calls = []
        def on_result(result, index, total):
            callback_calls.append((index, total, result["hit"]))

        await evaluate_prompt_batch(
            ps, [query_data], client, on_result=on_result
        )
        assert len(callback_calls) == 1
        assert callback_calls[0] == (0, 1, True)


# ---------------------------------------------------------------------------
# compute_accuracy
# ---------------------------------------------------------------------------


class TestComputeAccuracy:
    def test_all_hits(self):
        results = [{"hit": True, "error": None}] * 5
        acc = compute_accuracy(results)
        assert acc == {"hits": 5, "total": 5, "accuracy": 1.0, "errors": 0}

    def test_no_hits(self):
        results = [{"hit": False, "error": None}] * 3
        acc = compute_accuracy(results)
        assert acc == {"hits": 0, "total": 3, "accuracy": 0.0, "errors": 0}

    def test_with_errors(self):
        results = [
            {"hit": True, "error": None},
            {"hit": False, "error": "timeout"},
            {"hit": False, "error": None},
        ]
        acc = compute_accuracy(results)
        assert acc["hits"] == 1
        assert acc["errors"] == 1
        assert abs(acc["accuracy"] - 1 / 3) < 1e-9

    def test_empty_results(self):
        acc = compute_accuracy([])
        assert acc == {"hits": 0, "total": 0, "accuracy": 0.0, "errors": 0}
