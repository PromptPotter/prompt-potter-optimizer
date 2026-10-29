from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.config.paths import block_pack_path, block_packs_root
from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import BlockPack, Presumption
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

shapes_optimizer_prompt(__name__)


def missing_packs(names: Sequence[str]) -> list[str]:
    return [name for name in names if not block_pack_path(name).is_file()]


def read_pack(name: str) -> BlockPack:
    return BlockPack.model_validate_json(block_pack_path(name).read_text(encoding="utf-8"))


def installed_packs() -> list[str]:
    """Every directory under the packs root holding a `pack.json`, by name."""
    root = block_packs_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if block_pack_path(p.name).is_file())


def _pack_entries(name: str) -> list[tuple[PoolEntry, frozenset[Presumption]]]:
    """One entry per part a snippet suits, in the snippet's order and then the parts' render order."""
    return [
        (
            PoolEntry(id=entry_id(part, s.text), group=(part, s.source), text=s.text),
            s.presumes,
        )
        for s in read_pack(name).snippets
        for part in s.parts
    ]


def _held(names: Sequence[str]) -> dict[str, tuple[PoolEntry, frozenset[Presumption]]]:
    held: dict[str, tuple[PoolEntry, frozenset[Presumption]]] = {}
    holder: dict[str, str] = {}
    for name in names:
        for entry, presumes in _pack_entries(name):
            if entry.id in holder:
                raise ValueError(f"pack {name!r} repeats {entry.id}, held by {holder[entry.id]!r}")
            holder[entry.id] = name
            held[entry.id] = (entry, presumes)
    return held


def pack_entries(names: Sequence[str], *, admits: frozenset[Presumption]) -> dict[str, PoolEntry]:
    """By content id, in the packs' order and then each pack's own, less what the run cannot admit."""
    return {i: entry for i, (entry, presumes) in _held(names).items() if presumes <= admits}
