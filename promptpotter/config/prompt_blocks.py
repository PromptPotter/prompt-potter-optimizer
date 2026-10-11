from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.domain.prompt_block import PromptBlock
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

shapes_optimizer_prompt(__name__)

BUNDLED_PATH = Path(__file__).parent / "prompt_variants.json"

GENERAL_SOURCE = "PromptWizard"


@cache
def general_reasoning_blocks() -> dict[str, tuple[str, ...]]:
    """The `guidance` fallback: general strategies fit any task, where the house seeds mis-cue outside ranking."""
    return {field: texts[:8] for field, texts in prompt_blocks(GENERAL_SOURCE).items()}


def block_library() -> dict[str, tuple[PromptBlock, ...]]:
    fields: dict[str, list[dict[str, object]]] = json.loads(
        BUNDLED_PATH.read_text(encoding="utf-8")
    )["prompt_fields"]
    library = {
        field: tuple(PromptBlock.model_validate(entry) for entry in entries)
        for field, entries in fields.items()
    }
    ids = [block.id for blocks in library.values() for block in blocks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{BUNDLED_PATH.name} repeats a block id")
    return library


def library_identity(
    library: Mapping[str, Sequence[PromptBlock]],
) -> dict[str, list[tuple[str, str]]]:
    """What potter's treatment hashes: text and source only, so a provenance edit re-keys nothing."""
    return {
        field: [(block.text, block.source) for block in blocks] for field, blocks in library.items()
    }


@cache
def prompt_blocks(source: str | None = None) -> dict[str, tuple[str, ...]]:
    """Unfiltered, the library's declared value space: what `restrict` admits and the L1 validator checks."""
    blocks = {
        field: tuple(
            text
            for block in entries
            if (source is None or block.source == source) and (text := block.text.strip())
        )
        for field, entries in block_library().items()
    }
    return {field: texts for field, texts in blocks.items() if texts}
