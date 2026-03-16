"""Critique agent for the feedback cycle.

Researcher agent with two specialized tools (negative_critique, positive_critique)
that analyze evaluation results to produce structured feedback for the next round's
candidate generation. Architecture: agent base class + tool registry, designed to
evolve into a multi-agent hub with additional analysis tools.

Thinking style sampling is co-located here as both critique and styles are
meta-level optimizer agent concerns (not pipeline prompt fields).
"""

from __future__ import annotations

import json
import logging
import random
from typing import TYPE_CHECKING

from api.config.optimizer_prompt_loader import load_optimizer_prompt
from api.config.settings import load_variant_library

if TYPE_CHECKING:
    from api.services.llm_client import LLMClientBase

logger = logging.getLogger(__name__)

DISPLAY_TRUNCATE = 60
MAX_EXAMPLES = 15


class CritiqueAgent:
    """Researcher agent that analyzes eval results via specialized tools.

    Routes to ``negative_critique`` (accuracy < threshold) or
    ``positive_critique`` (accuracy >= threshold). Each tool is an async
    callable registered in ``self.tools`` — new analysis tools can be added
    by subclassing or extending the registry.
    """

    def __init__(self, llm_client: LLMClientBase, model: str | None = None):
        self.llm_client = llm_client
        self.model = model
        self.tools: dict[str, ...] = {
            "negative_critique": self._negative_critique,
            "positive_critique": self._positive_critique,
        }

    async def run(
        self,
        results: list[dict],
        accuracy: float,
        threshold: float = 0.7,
    ) -> str:
        """Route to the appropriate critique tool based on accuracy."""
        tool_name = "positive_critique" if accuracy >= threshold else "negative_critique"
        logger.info("Critique agent: using %s (acc=%.3f, threshold=%.2f)",
                     tool_name, accuracy, threshold)
        return await self.tools[tool_name](results, accuracy)

    async def _negative_critique(self, results: list[dict], accuracy: float) -> str:
        """Analyze failures: categories, root causes, priority fixes."""
        failures = [r for r in results if not r.get("hit") and not r.get("error")]
        failure_lines = "\n".join(
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
            f"Predicted: {r.get('predicted', '?')[:DISPLAY_TRUNCATE]}  |  "
            f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
            for r in failures[:MAX_EXAMPLES]
        )

        prompt = load_optimizer_prompt("critique_negative").compile(
            accuracy_pct=f"{accuracy:.1%}",
            n_failures=len(failures),
            max_examples=MAX_EXAMPLES,
            failure_lines=failure_lines,
        )

        response = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            temperature=0.3,
            max_tokens=2048,
            output_format="json",
        )
        result = response.parsed or json.loads(response.content)
        return result.get("summary", json.dumps(result, indent=2))

    async def _positive_critique(self, results: list[dict], accuracy: float) -> str:
        """Analyze successes: patterns, how to extend to remaining failures."""
        successes = [r for r in results if r.get("hit")]
        failures = [r for r in results if not r.get("hit") and not r.get("error")]

        success_lines = "\n".join(
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
            f"Predicted: {r.get('predicted', '?')[:DISPLAY_TRUNCATE]}"
            for r in successes[:MAX_EXAMPLES]
        )
        failure_lines = "\n".join(
            f"  Query: {r['query'][:DISPLAY_TRUNCATE]}  |  "
            f"Predicted: {r.get('predicted', '?')[:DISPLAY_TRUNCATE]}  |  "
            f"GT: {r['ground_truth'][:DISPLAY_TRUNCATE]}"
            for r in failures[:5]
        )

        prompt = load_optimizer_prompt("critique_positive").compile(
            accuracy_pct=f"{accuracy:.1%}",
            n_successes=len(successes),
            max_examples=MAX_EXAMPLES,
            success_lines=success_lines,
            n_failures=len(failures),
            failure_lines=failure_lines,
        )

        response = await self.llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            model=self.model,
            temperature=0.3,
            max_tokens=2048,
            output_format="json",
        )
        result = response.parsed or json.loads(response.content)
        return result.get("summary", json.dumps(result, indent=2))


def sample_thinking_styles(n: int = 3, seed: int | None = None) -> list[str]:
    """Sample thinking styles from the variant library for meta-prompt injection.

    Returns up to *n* non-empty styles randomly selected from the
    ``thinking_style`` axis of ``prompt_variants.json``.
    """
    lib = load_variant_library()
    styles = lib.get("prompt_fields", {}).get("thinking_style", [])
    styles = [s for s in styles if s and s.strip()]
    if not styles:
        return []
    rng = random.Random(seed)
    return rng.sample(styles, min(n, len(styles)))
