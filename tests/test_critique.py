"""Tests for critique agent and thinking style sampling."""

import pytest

from api.services.campaign.critique import CritiqueAgent, sample_thinking_styles
from api.services.campaign.critique_stats import CritiqueContext
from api.services.llm_client import MockLLMClient


# ---------------------------------------------------------------------------
# CritiqueAgent.run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critique_returns_string():
    """CritiqueAgent.run returns a non-empty string from LLM response."""
    agent = CritiqueAgent(MockLLMClient(), model=None)
    ctx = CritiqueContext(results=[], accuracy=0.5)
    result = await agent.run(ctx)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_critique_extracts_json_summary():
    """When LLM returns JSON with summary key, extracts it."""
    import json
    client = MockLLMClient(responses=[json.dumps({"summary": "Fix ranking"})])
    agent = CritiqueAgent(client, model=None)
    ctx = CritiqueContext(results=[], accuracy=0.3)
    result = await agent.run(ctx)
    assert result == "Fix ranking"


@pytest.mark.asyncio
async def test_critique_falls_through_on_non_json():
    """When LLM returns non-JSON text, uses raw content."""
    client = MockLLMClient(responses=["Plain text critique about failures"])
    agent = CritiqueAgent(client, model=None)
    ctx = CritiqueContext(results=[], accuracy=0.3)
    result = await agent.run(ctx)
    assert result == "Plain text critique about failures"


# ---------------------------------------------------------------------------
# sample_thinking_styles
# ---------------------------------------------------------------------------


def test_sample_thinking_styles_returns_requested_count():
    """Returns the requested number of non-empty styles."""
    styles = sample_thinking_styles(n=3, seed=42)
    assert len(styles) == 3
    assert all(s.strip() for s in styles)


def test_sample_thinking_styles_no_empty():
    """All returned styles are non-empty strings."""
    styles = sample_thinking_styles(n=5, seed=0)
    assert len(styles) == 5
    for s in styles:
        assert isinstance(s, str)
        assert s.strip()


def test_sample_thinking_styles_deterministic():
    """Same seed → same result."""
    a = sample_thinking_styles(n=3, seed=99)
    b = sample_thinking_styles(n=3, seed=99)
    assert a == b


def test_sample_thinking_styles_different_seeds():
    """Different seeds → (likely) different results."""
    a = sample_thinking_styles(n=3, seed=1)
    b = sample_thinking_styles(n=3, seed=2)
    # Could coincidentally match, but very unlikely with 30+ styles
    assert a != b
