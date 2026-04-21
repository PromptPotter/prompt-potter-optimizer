"""Critique agent — LLM analysis of a round's results."""

from __future__ import annotations

import json
import logging
import random
from typing import TYPE_CHECKING

from promptpotter.application.intelligence import load_variant_library
from promptpotter.application.optimization.nodes.critique_payload import (
    RoundSnapshot,
    assemble_critique_sections,
)
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.infrastructure.llm.client import LLMClientBase

logger = logging.getLogger(__name__)

__all__ = [
    "CritiqueAgent",
    "RoundSnapshot",
    "format_critique_for_prompt",
    "sample_thinking_styles",
]


class CritiqueAgent:
    """Analyzes scoring results; returns 6-field critique dict consumed by L1/L2."""

    def __init__(
        self,
        llm_client: LLMClientBase,
        model: str | None = None,
    ):
        self.llm_client = llm_client
        self.model = model

    async def run(self, ctx: RoundSnapshot) -> dict:
        """Build critique from pipeline stats + LLM analysis."""
        sections = assemble_critique_sections(ctx)
        _compile_vars = {"stat_sections": sections}
        _template = load_optimizer_prompt("critique")
        prompt = _template.compile_prompt(**_compile_vars)
        logger.info(
            "Rich critique: %d chars prompt, round %d, acc=%.3f",
            len(prompt),
            ctx.current_round + 1,
            ctx.accuracy,
        )

        response = await llm_call(
            self.llm_client,
            messages=[{"role": "user", "content": prompt}],
            node="critique",
            model=self.model,
            trace_meta={
                "template_name": "critique",
                "template_fields": _template.prompt_field_dict(),
                "variables": _compile_vars,
            },
        )

        return _parse_critique(response.content)


def _parse_critique(content: str) -> dict:
    """Parse LLM critique response into the 6-field dict."""
    try:
        result = json.loads(content)
        return {
            "positive_critique": result.get("positive_critique", ""),
            "negative_critique": result.get("negative_critique", ""),
            "priority_fix": result.get("priority_fix", ""),
            "suggested_axes": result.get("suggested_axes", []),
            "failure_highlights": result.get("failure_highlights", []),
            "summary": result.get("summary", content),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "positive_critique": "",
            "negative_critique": "",
            "priority_fix": "",
            "suggested_axes": [],
            "failure_highlights": [],
            "summary": content,
        }


def format_critique_for_prompt(critique: dict) -> str:
    """Critique dict → compact text for L1/L2 (summary + priority_fix + axes + highlights)."""
    parts = []
    if critique.get("summary"):
        parts.append(critique["summary"])
    if critique.get("priority_fix"):
        parts.append(f"Priority fix: {critique['priority_fix']}")
    if critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(critique['suggested_axes'])}")
    highlights = critique.get("failure_highlights", [])
    if highlights:
        parts.append("Key failures:")
        for h in highlights[:5]:
            parts.append(f"  {h}")
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
