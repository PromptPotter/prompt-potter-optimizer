"""Tests for api/services/prompt_optimizer.py."""
import json

import pytest

from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.services.prompt_optimizer import (
    generate_candidates,
    select_round_winner,
    generate_suggestions,
    save_campaign_winner,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def current_ps():
    return PromptState(
        instruction="Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
        changes_description="baseline",
    )


@pytest.fixture
def eval_results():
    """Mixed hit/miss results for testing."""
    return [
        {"query": "aspirin", "predicted": "Acetylsalicylic acid",
         "ground_truth": "Acetylsalicylic acid", "hit": True, "error": None},
        {"query": "tylenol", "predicted": "Ibuprofen",
         "ground_truth": "Paracetamol", "hit": False, "error": None},
        {"query": "advil", "predicted": "Ibuprofen",
         "ground_truth": "Ibuprofen", "hit": True, "error": None},
    ]


@pytest.fixture
def mock_generate_response():
    return json.dumps({
        "variants": [
            {
                "variant_name": "v1_semantic_focus",
                "changes_description": "Added semantic matching emphasis",
                "prompt_text": (
                    "Focus on semantic meaning. Rank {{core_concept}} "
                    "given {{entity_profile_json}} and {{matches}}."
                ),
            },
            {
                "variant_name": "v2_structured",
                "changes_description": "Added structured reasoning",
                "prompt_text": (
                    "Step by step, rank {{core_concept}} "
                    "given {{entity_profile_json}} and {{matches}}."
                ),
            },
        ]
    })


# ---------------------------------------------------------------------------
# generate_candidates
# ---------------------------------------------------------------------------


class TestGenerateCandidates:
    @pytest.mark.asyncio
    async def test_generates_n_candidates(self, current_ps, eval_results, mock_generate_response):
        client = MockLLMClient(responses=[mock_generate_response])
        candidates = await generate_candidates(
            current_ps, 0.67, eval_results,
            n_variants=2, creativity=0.7,
            llm_client=client,
        )
        assert len(candidates) == 2
        assert all(isinstance(c, PromptState) for c in candidates)
        assert all(c.parent_id == current_ps.id for c in candidates)

    @pytest.mark.asyncio
    async def test_limits_to_n_variants(self, current_ps, eval_results):
        response = json.dumps({
            "variants": [
                {
                    "variant_name": f"v{i}",
                    "changes_description": f"change {i}",
                    "prompt_text": (
                        f"Prompt {i} {{{{core_concept}}}} "
                        f"{{{{entity_profile_json}}}} {{{{matches}}}}"
                    ),
                }
                for i in range(5)
            ]
        })
        client = MockLLMClient(responses=[response])
        candidates = await generate_candidates(
            current_ps, 0.5, eval_results,
            n_variants=3, creativity=0.7,
            llm_client=client,
        )
        assert len(candidates) == 3

    @pytest.mark.asyncio
    async def test_sets_changes_description(self, current_ps, eval_results, mock_generate_response):
        client = MockLLMClient(responses=[mock_generate_response])
        candidates = await generate_candidates(
            current_ps, 0.67, eval_results,
            n_variants=2, creativity=0.7,
            llm_client=client,
        )
        assert candidates[0].changes_description == "Added semantic matching emphasis"


# ---------------------------------------------------------------------------
# select_round_winner
# ---------------------------------------------------------------------------


class TestSelectRoundWinner:
    def test_selects_better_candidate(self, current_ps):
        candidate = current_ps.derive(
            instruction="better prompt",
            changes_description="improved",
        )

        current_best = {
            "label": "baseline",
            "prompt_state": current_ps,
            "accuracy": 0.5,
            "results": [{"hit": True}, {"hit": False}],
        }

        all_results = {
            candidate.id: [{"hit": True}, {"hit": True}],  # 100%
        }

        result = select_round_winner(
            [candidate], all_results, current_best,
            improvement_threshold=0.01,
        )
        assert result["improved"] is True
        assert result["accuracy"] == 1.0
        assert result["prompt_state"].id == candidate.id

    def test_keeps_current_when_no_improvement(self, current_ps):
        candidate = current_ps.derive(
            instruction="slightly different",
            changes_description="marginal",
        )

        current_best = {
            "label": "baseline",
            "prompt_state": current_ps,
            "accuracy": 0.8,
            "results": [{"hit": True}] * 4 + [{"hit": False}],
        }

        all_results = {
            candidate.id: [{"hit": True}] * 4 + [{"hit": False}],  # same 80%
        }

        result = select_round_winner(
            [candidate], all_results, current_best,
            improvement_threshold=0.05,
        )
        assert result["improved"] is False
        assert result["prompt_state"].id == current_ps.id

    def test_comparison_rows_included(self, current_ps):
        candidate = current_ps.derive(instruction="v2", changes_description="v2")

        current_best = {
            "label": "baseline",
            "prompt_state": current_ps,
            "accuracy": 0.5,
            "results": [{"hit": True}, {"hit": False}],
        }

        all_results = {candidate.id: [{"hit": True}, {"hit": True}]}

        result = select_round_winner(
            [candidate], all_results, current_best, improvement_threshold=0.01,
        )
        assert "comparison_rows" in result
        assert len(result["comparison_rows"]) == 2  # current + 1 candidate


# ---------------------------------------------------------------------------
# generate_suggestions
# ---------------------------------------------------------------------------


class TestGenerateSuggestions:
    @pytest.mark.asyncio
    async def test_returns_suggestions_dict(self, current_ps, eval_results):
        mock_response = json.dumps({
            "failure_patterns": [{"category": "reranker_misjudged", "count": 1,
                                  "description": "test", "examples": ["tylenol"]}],
            "parameter_suggestions": [],
            "prompt_phrase_fragments": [],
            "suggested_config": {},
            "summary": "Try focusing on semantic meaning.",
        })
        client = MockLLMClient(responses=[mock_response])

        rounds = [{
            "round": 0,
            "label": "baseline",
            "prompt_state": current_ps,
            "accuracy": 0.67,
            "results": eval_results,
        }]

        suggestions = await generate_suggestions(
            rounds, eval_results, {"n_variants": 3},
            llm_client=client,
        )
        assert "summary" in suggestions
        assert "failure_patterns" in suggestions


# ---------------------------------------------------------------------------
# save_campaign_winner
# ---------------------------------------------------------------------------


class TestSaveCampaignWinner:
    def test_saves_best_round(self, tmp_store, current_ps):
        better_ps = current_ps.derive(
            instruction="better", changes_description="improved"
        )
        rounds = [
            {"prompt_state": current_ps, "accuracy": 0.5},
            {"prompt_state": better_ps, "accuracy": 0.8},
        ]
        config = {"n_variants": 3}

        # Register a backend first
        from api.models.backend import BackendConnection
        tmp_store.register_backend(BackendConnection(
            id="test-backend", name="Test", backend_type="test", base_url="http://test",
        ))

        result = save_campaign_winner(rounds, config, tmp_store, "test-backend")
        assert result["accuracy"] == 0.8
        assert result["improvement"] == pytest.approx(0.3)
        assert result["winner_id"] == better_ps.id

    def test_selects_best_not_last(self, tmp_store, current_ps):
        """If a middle round was best, it should be saved."""
        mid_ps = current_ps.derive(instruction="mid", changes_description="mid")
        last_ps = mid_ps.derive(instruction="last", changes_description="last")
        rounds = [
            {"prompt_state": current_ps, "accuracy": 0.5},
            {"prompt_state": mid_ps, "accuracy": 0.9},
            {"prompt_state": last_ps, "accuracy": 0.7},
        ]

        from api.models.backend import BackendConnection
        tmp_store.register_backend(BackendConnection(
            id="test-backend", name="Test", backend_type="test", base_url="http://test",
        ))

        result = save_campaign_winner(rounds, {}, tmp_store, "test-backend")
        assert result["accuracy"] == 0.9
        assert result["winner_id"] == mid_ps.id
