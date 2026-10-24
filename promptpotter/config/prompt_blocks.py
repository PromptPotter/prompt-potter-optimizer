from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import Presumption, PromptBlock
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Mapping

shapes_optimizer_prompt(__name__)

BUNDLED_PATH = Path(__file__).parent / "prompt_variants.json"


@cache
def _library() -> Mapping[str, tuple[PoolEntry, frozenset[Presumption]]]:
    """By content id, in the library's own order; a blank block is no material, so no entry."""
    fields: dict[str, list[dict[str, object]]] = json.loads(
        BUNDLED_PATH.read_text(encoding="utf-8")
    )["prompt_fields"]
    entries: dict[str, tuple[PoolEntry, frozenset[Presumption]]] = {}
    for field, raw in fields.items():
        for block in (PromptBlock.model_validate(item) for item in raw):
            if not block.text.strip():
                continue
            entry = PoolEntry(
                id=entry_id(field, block.text), group=(field, block.source), text=block.text
            )
            if entries.setdefault(entry.id, (entry, block.presumes))[0] is not entry:
                raise ValueError(f"{BUNDLED_PATH.name} repeats a block's text under {field!r}")
    return entries


def library_entries(*, admits: frozenset[Presumption]) -> dict[str, PoolEntry]:
    return {i: entry for i, (entry, presumes) in _library().items() if presumes <= admits}


def library_identity() -> list[tuple[list[str], str, list[str]]]:
    """Group, text and the presumptions that admit it: a provenance edit re-keys nothing."""
    return [(list(e.group), e.text, sorted(presumes)) for e, presumes in _library().values()]
