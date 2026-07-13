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

HOUSE_SOURCE = "PromptPotter"


@cache
def prompt_blocks(source: str | None = None) -> dict[str, tuple[str, ...]]:
    """Field name → its reusable block texts, in authored order.

    Unfiltered, this is the library's *declared value space* — what ``restrict`` admits.
    Filtered to ``HOUSE_SOURCE``, it is the far smaller set adopted from this project's own
    runs — what ``guidance`` recommends, where the value space stays open anyway and the
    imported long tail is a menu rather than evidence.
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
