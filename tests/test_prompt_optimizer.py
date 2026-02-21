"""Tests for api/services/prompt_optimizer.py."""
import json

import pytest

from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.services.prompt_optimizer import (
    generate_candidates,
    select_round_winner,
    save_campaign_winner,
)


@pytest.fixture
def current_ps():
    return PromptState(
        instruction="Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
        changes_description="baseline",
    )


@pytest.fixture
def eval_results():
    return [
        {"query": "aspirin", "predicted": "Acetylsalicylic acid",
         "ground_truth": "Acetylsalicylic acid", "hit": True, "error": None},
        {"query": "tylenol", "predicted": "Ibuprofen",
         "ground_truth": "Paracetamol", "hit": False, "error": None},
    ]


@pytest.mark.asyncio
async def test_generate_candidates(current_ps, eval_results):
    response = json.dumps({
        "variants": [
            {"variant_name": "v1", "changes_description": "semantic focus",
             "prompt_text": "Focus on meaning. Rank {{core_concept}} given {{entity_profile_json}} and {{matches}}."},
            {"variant_name": "v2", "changes_description": "structured",
             "prompt_text": "Step by step, rank {{core_concept}} given {{entity_profile_json}} and {{matches}}."},
        ]
    })
    client = MockLLMClient(responses=[response])
    candidates = await generate_candidates(
        current_ps, 0.5, eval_results, n_variants=2, creativity=0.7, llm_client=client,
    )
    assert len(candidates) == 2
    assert all(isinstance(c, PromptState) for c in candidates)
    assert all(c.parent_id == current_ps.id for c in candidates)


def test_select_round_winner(current_ps):
    candidate = current_ps.derive(instruction="better", changes_description="improved")

    current_best = {
        "label": "baseline", "prompt_state": current_ps,
        "accuracy": 0.5, "results": [{"hit": True}, {"hit": False}],
    }

    # Candidate wins
    result = select_round_winner(
        [candidate], {candidate.id: [{"hit": True}, {"hit": True}]},
        current_best, improvement_threshold=0.01,
    )
    assert result["improved"] is True
    assert result["accuracy"] == 1.0

    # No improvement keeps current
    result = select_round_winner(
        [candidate], {candidate.id: [{"hit": True}, {"hit": False}]},
        current_best, improvement_threshold=0.05,
    )
    assert result["improved"] is False


def test_save_campaign_winner(tmp_store, current_ps):
    from api.models.backend import BackendConnection
    tmp_store.register_backend(BackendConnection(
        id="test", name="Test", backend_type="test", base_url="http://test",
    ))
    better_ps = current_ps.derive(instruction="better", changes_description="improved")
    rounds = [
        {"prompt_state": current_ps, "accuracy": 0.5},
        {"prompt_state": better_ps, "accuracy": 0.9},
    ]
    result = save_campaign_winner(rounds, {}, tmp_store, "test")
    assert result["accuracy"] == 0.9
    assert result["winner_id"] == better_ps.id
