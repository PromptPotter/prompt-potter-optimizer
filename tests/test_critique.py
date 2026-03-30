"""Tests for critique agent and thinking style sampling."""

import pytest

from api.services.campaign.critique import CritiqueAgent, CritiqueContext, sample_thinking_styles
from tests.mock_llm_client import MockLLMClient


@pytest.mark.asyncio
async def test_critique_returns_dict():

    agent = CritiqueAgent(MockLLMClient(), model=None)
    ctx = CritiqueContext(results=[], accuracy=0.5)
    result = await agent.run(ctx)
    assert isinstance(result, dict)
    assert "summary" in result
    assert "positive_critique" in result
    assert "negative_critique" in result


@pytest.mark.asyncio
async def test_critique_extracts_json_fields():

    import json
    client = MockLLMClient(responses=[json.dumps({
        "summary": "Fix ranking",
        "positive_critique": "Good recall",
        "negative_critique": "Poor precision",
        "priority_fix": "Adjust prefix",
        "suggested_axes": ["query_prefix"],
    })])
    agent = CritiqueAgent(client, model=None)
    ctx = CritiqueContext(results=[], accuracy=0.3)
    result = await agent.run(ctx)
    assert result["summary"] == "Fix ranking"
    assert result["positive_critique"] == "Good recall"
    assert result["suggested_axes"] == ["query_prefix"]


@pytest.mark.asyncio
async def test_critique_falls_through_on_non_json():

    client = MockLLMClient(responses=["Plain text critique about failures"])
    agent = CritiqueAgent(client, model=None)
    ctx = CritiqueContext(results=[], accuracy=0.3)
    result = await agent.run(ctx)
    assert isinstance(result, dict)
    assert result["summary"] == "Plain text critique about failures"



def test_sample_thinking_styles():
    styles = sample_thinking_styles(n=5, seed=42)
    assert len(styles) == 5
    assert all(isinstance(s, str) and s.strip() for s in styles)

    # Deterministic
    assert sample_thinking_styles(n=3, seed=99) == sample_thinking_styles(n=3, seed=99)
    # Different seeds → different results
    assert sample_thinking_styles(n=3, seed=1) != sample_thinking_styles(n=3, seed=2)
