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

if TYPE_CHECKING:
    from api.services.campaign.critique_stats import CritiqueContext
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)


class CritiqueAgent:
    """Analyzes eval results via pipeline-aware critique stats."""

    def __init__(self, llm_client: "LLMClientBase", model: str | None = None):
        self.llm_client = llm_client
        self.model = model

    async def run(self, ctx: "CritiqueContext") -> str:
        """Build critique from pipeline stats + LLM analysis."""
        from api.services.campaign.critique_stats import assemble_critique_prompt

        prompt = assemble_critique_prompt(ctx)
        logger.info(
            "Rich critique: %d chars prompt, round %d, acc=%.3f",
            len(prompt), ctx.current_round + 1, ctx.accuracy,
        )

        response = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            temperature=0.3,
            max_tokens=4096,
        )

        try:
            result = json.loads(response.content)
            return result.get("summary", response.content)
        except (json.JSONDecodeError, TypeError):
            return response.content


def sample_thinking_styles(n: int = 3, seed: int | None = None) -> list[str]:
    """Sample thinking styles from the variant library for meta-prompt injection."""
    lib = load_variant_library()
    styles = lib.get("prompt_fields", {}).get("thinking_style", [])
    styles = [s for s in styles if s and s.strip()]
    if not styles:
        return []
    rng = random.Random(seed)
    return rng.sample(styles, min(n, len(styles)))
