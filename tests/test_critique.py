"""Tests for critique agent and thinking style sampling."""

import pytest

from api.services.campaign.critique import CritiqueAgent, sample_thinking_styles
from api.services.llm_client import MockLLMClient


# ---------------------------------------------------------------------------
# CritiqueAgent routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_negative_critique_below_threshold():
    """Routes to negative_critique when accuracy < threshold."""
    agent = CritiqueAgent(MockLLMClient(), model=None)
    routed_tool = []

    async def fake_neg(results, accuracy):
        routed_tool.append("negative_critique")
        return "negative feedback"

    async def fake_pos(results, accuracy):
        routed_tool.append("positive_critique")
        return "positive feedback"

    agent.tools["negative_critique"] = fake_neg
    agent.tools["positive_critique"] = fake_pos

    result = await agent.run([], 0.5, threshold=0.7)
    assert routed_tool == ["negative_critique"]
    assert result == "negative feedback"


@pytest.mark.asyncio
async def test_positive_critique_above_threshold():
    """Routes to positive_critique when accuracy >= threshold."""
    agent = CritiqueAgent(MockLLMClient(), model=None)
    routed_tool = []

    async def fake_neg(results, accuracy):
        routed_tool.append("negative_critique")
        return "negative feedback"

    async def fake_pos(results, accuracy):
        routed_tool.append("positive_critique")
        return "positive feedback"

    agent.tools["negative_critique"] = fake_neg
    agent.tools["positive_critique"] = fake_pos

    result = await agent.run([], 0.7, threshold=0.7)
    assert routed_tool == ["positive_critique"]
    assert result == "positive feedback"


@pytest.mark.asyncio
async def test_positive_critique_at_exact_threshold():
    """Boundary: exactly at threshold routes to positive."""
    agent = CritiqueAgent(MockLLMClient(), model=None)
    routed_tool = []

    async def fake_pos(results, accuracy):
        routed_tool.append("positive_critique")
        return "positive"

    agent.tools["positive_critique"] = fake_pos
    agent.tools["negative_critique"] = lambda r, a: None

    await agent.run([], 0.7, threshold=0.7)
    assert routed_tool == ["positive_critique"]


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
