"""Critique agent for the feedback cycle.

Analyzes evaluation results to produce structured feedback for the next
round's candidate generation. Uses pipeline-aware stats from critique_stats.
"""

from __future__ import annotations

import json
import logging
import random
from typing import TYPE_CHECKING

from api.config.settings import load_variant_library
from api.core.llm_call import get_node_config, llm_call

if TYPE_CHECKING:
    from api.services.campaign.critique_stats import CritiqueContext
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


_EMPTY_CRITIQUE: dict[str, str | list] = {
    "positive_critique": "",
    "negative_critique": "",
    "priority_fix": "",
    "suggested_axes": [],
    "summary": "",
}


class CritiqueAgent:
    """Analyzes eval results via pipeline-aware critique stats.

    Returns a 5-field dict (positive_critique, negative_critique,
    priority_fix, suggested_axes, summary) fed to both L1 Generate
    and L2 Refine Context.
    """

    def __init__(
        self,
        llm_client: "LLMClientBase",
        model: str | None = None,
        positive_threshold: float = 0.7,
    ):
        self.llm_client = llm_client
        self.model = model
        self.positive_threshold = positive_threshold

    async def run(self, ctx: "CritiqueContext") -> dict:
        """Build critique from pipeline stats + LLM analysis.

        Returns dict with keys: positive_critique, negative_critique,
        priority_fix, suggested_axes, summary.
        """
        from api.services.campaign.critique_stats import assemble_critique_prompt

        prompt = assemble_critique_prompt(ctx)
        logger.info(
            "Rich critique: %d chars prompt, round %d, acc=%.3f",
            len(prompt), ctx.current_round + 1, ctx.accuracy,
        )

        response = await llm_call(
            self.llm_client,
            messages=[{"role": "user", "content": prompt}],
            config=get_node_config("critique"),
            model=self.model,
        )

        return _parse_critique(response.content)


def _parse_critique(content: str) -> dict:
    """Parse LLM critique response into the 5-field dict."""
    try:
        result = json.loads(content)
        return {
            "positive_critique": result.get("positive_critique", ""),
            "negative_critique": result.get("negative_critique", ""),
            "priority_fix": result.get("priority_fix", ""),
            "suggested_axes": result.get("suggested_axes", []),
            "summary": result.get("summary", content),
        }
    except (json.JSONDecodeError, TypeError):
        return {**_EMPTY_CRITIQUE, "summary": content}


def format_critique_for_prompt(critique: dict) -> str:
    """Format critique dict into text for injection into L1/L2 prompts."""
    parts = []
    if critique.get("positive_critique"):
        parts.append(f"Strengths: {critique['positive_critique']}")
    if critique.get("negative_critique"):
        parts.append(f"Weaknesses: {critique['negative_critique']}")
    if critique.get("priority_fix"):
        parts.append(f"Priority fix: {critique['priority_fix']}")
    if critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(critique['suggested_axes'])}")
    if critique.get("summary"):
        parts.append(f"Summary: {critique['summary']}")
    return "\n".join(parts)


def sample_thinking_styles(n: int = 3, seed: int | None = None) -> list[str]:
    """Sample thinking styles from the variant library for meta-prompt injection."""
    lib = load_variant_library()
    styles = lib.get("prompt_fields", {}).get("thinking_style", [])
    styles = [s for s in styles if s and s.strip()]
    if not styles:
        return []
    rng = random.Random(seed)
    return rng.sample(styles, min(n, len(styles)))
