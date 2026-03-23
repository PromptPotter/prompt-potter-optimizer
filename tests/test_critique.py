"""Tests for critique agent and thinking style sampling."""

import pytest

from api.services.campaign.critique import CritiqueAgent, sample_thinking_styles
from api.services.campaign.critique_stats import CritiqueContext
from api.services.llm_client import MockLLMClient



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



def test_sample_thinking_styles_returns_requested_count():

    styles = sample_thinking_styles(n=3, seed=42)
    assert len(styles) == 3
    assert all(s.strip() for s in styles)


def test_sample_thinking_styles_no_empty():

    styles = sample_thinking_styles(n=5, seed=0)
    assert len(styles) == 5
    for s in styles:
        assert isinstance(s, str)
        assert s.strip()


def test_sample_thinking_styles_deterministic():

    a = sample_thinking_styles(n=3, seed=99)
    b = sample_thinking_styles(n=3, seed=99)
    assert a == b


def test_sample_thinking_styles_different_seeds():

    a = sample_thinking_styles(n=3, seed=1)
    b = sample_thinking_styles(n=3, seed=2)
    # Could coincidentally match, but very unlikely with 30+ styles
    assert a != b
