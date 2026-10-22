from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import PromptBlock
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Mapping

shapes_optimizer_prompt(__name__)

BUNDLED_PATH = Path(__file__).parent / "prompt_variants.json"


@cache
def library_entries() -> Mapping[str, PoolEntry]:
    """By content id, in the library's own order; a blank block is no material, so no entry."""
    fields: dict[str, list[dict[str, object]]] = json.loads(
        BUNDLED_PATH.read_text(encoding="utf-8")
    )["prompt_fields"]
    entries: dict[str, PoolEntry] = {}
    for field, raw in fields.items():
        for block in (PromptBlock.model_validate(item) for item in raw):
            if not block.text.strip():
                continue
            entry = PoolEntry(
                id=entry_id(field, block.text), group=(field, block.source), text=block.text
            )
            if entries.setdefault(entry.id, entry) is not entry:
                raise ValueError(f"{BUNDLED_PATH.name} repeats a block's text under {field!r}")
    return entries


def library_identity() -> list[tuple[list[str], str]]:
    """What a library-sourced pool's treatment hashes: group and text, so a provenance edit re-keys nothing."""
    return [(list(entry.group), entry.text) for entry in library_entries().values()]
