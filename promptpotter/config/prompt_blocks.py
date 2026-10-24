from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.config.paths import block_pack_path
from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import BlockPack, Presumption
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

shapes_optimizer_prompt(__name__)


def missing_packs(names: Sequence[str]) -> list[str]:
    return [name for name in names if not block_pack_path(name).is_file()]


def _pack_entries(name: str) -> list[tuple[PoolEntry, frozenset[Presumption]]]:
    pack = BlockPack.model_validate_json(block_pack_path(name).read_text(encoding="utf-8"))
    return [
        (
            PoolEntry(id=entry_id(field, block.text), group=(field, block.source), text=block.text),
            block.presumes,
        )
        for field, blocks in pack.prompt_fields.items()
        for block in blocks
    ]


def pack_entries(names: Sequence[str], *, admits: frozenset[Presumption]) -> dict[str, PoolEntry]:
    """By content id, in the packs' order and then each pack's own, less what the run cannot admit."""
    entries: dict[str, PoolEntry] = {}
    holder: dict[str, str] = {}
    for name in names:
        for entry, presumes in _pack_entries(name):
            if entry.id in holder:
                raise ValueError(f"pack {name!r} repeats {entry.id}, held by {holder[entry.id]!r}")
            holder[entry.id] = name
            if presumes <= admits:
                entries[entry.id] = entry
    return entries


def pack_identity(names: Sequence[str]) -> dict[str, list[tuple[list[str], str, list[str]]]]:
    """Group, text and the presumptions that admit it, per pack: a provenance edit re-keys nothing."""
    return {
        name: [(list(e.group), e.text, sorted(presumes)) for e, presumes in _pack_entries(name)]
        for name in names
    }
