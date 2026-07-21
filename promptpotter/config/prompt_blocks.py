"""The prompt building-block library — reusable field values L1 may reuse or recombine.

Data rides beside this module in ``prompt_variants.json``, each entry tagged with the
source it was adopted from: ``PromptWizard`` (whose thinking styles are the Self-Discover
reasoning modules) and PromptPotter's own. Empty placeholder entries are dropped here, so
a field whose only entry is ``""`` (``problem_description`` — task-specific by nature)
does not reach the catalogue at all.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

BUNDLED_PATH = Path(__file__).parent / "prompt_variants.json"

# The imported Self-Discover reasoning modules — task-AGNOSTIC strategies ("break the problem
# into parts", "what are the key assumptions", "imagine the best solution is wrong, what else").
GENERAL_SOURCE = "PromptWizard"


@cache
def general_reasoning_blocks() -> dict[str, tuple[str, ...]]:
    """Task-agnostic reasoning material — the `guidance` fallback when no EARNED block fits yet.

    General reasoning strategies (Self-Discover modules + general personas) help ANY reasoning
    task, unlike the house seeds, which were adopted from ranking runs and mis-cue a logic task.
    Capped per field to stay compact — L1 needs grounding material, not the whole 38-module tail.
    """
    return {field: texts[:8] for field, texts in prompt_blocks(GENERAL_SOURCE).items()}


@cache
def prompt_blocks(source: str | None = None) -> dict[str, tuple[str, ...]]:
    """Field name → its reusable block texts, in authored order.

    Unfiltered, this is the library's *declared value space* — what ``restrict`` admits
    and the L1 validator checks against. Filtered to ``GENERAL_SOURCE``, it is the
    task-agnostic subset behind :func:`general_reasoning_blocks`.
    """
    raw: dict[str, list[dict[str, str]]] = json.loads(BUNDLED_PATH.read_text(encoding="utf-8"))[
        "prompt_fields"
    ]
    blocks = {
        field: tuple(
            text
            for v in variants
            if (source is None or v["source"] == source) and (text := v["text"].strip())
        )
        for field, variants in raw.items()
    }
    return {field: texts for field, texts in blocks.items() if texts}
