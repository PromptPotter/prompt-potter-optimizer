"""A pool is the entries a variation node may draw from, banked so a round reads only what its own state holds."""

from __future__ import annotations

import enum
from abc import abstractmethod
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from promptpotter.domain.optimizer_state import StateSlice
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from promptpotter.domain.sample import Sample

__all__ = [
    "EARNED_SOURCE",
    "Access",
    "BlockPool",
    "DemoPool",
    "Pool",
    "PoolEntry",
    "PoolSource",
    "entry_id",
    "kept",
    "unnamed",
]

# How a reader takes a pool: not at all, as material it may adapt, or by entry id alone.
Access = Literal["off", "open", "closed"]

# The source of a block no library holds: a run on the same answer space measured it as a lift.
EARNED_SOURCE: Annotated[str, shapes_optimizer_prompt] = "earned"


class PoolSource(enum.StrEnum):
    # The shipped prompt block library and the blocks runs earned beside it: both move under a
    # running campaign, so the pool banks its entries whole.
    LIBRARY = "library"
    # The cycle's demo split, pinned by its bank's hash: the pool banks sample keys.
    PARTITION = "partition"


@shapes_optimizer_prompt
def entry_id(field: str, text: str) -> str:
    """A block's id is its content: an edited text is a new entry, never the old id re-pointed."""
    return f"{field}.{stable_hash(text, length=10)}"


class PoolEntry(FrozenModel):
    id: str
    # The leading run an `active` path may name: ``(field, source)`` for a block.
    group: tuple[str, ...]
    text: str


def _in_play(path: str, entry: str, group: Sequence[str]) -> bool:
    if path in ("*", entry):
        return True
    parts = tuple(path.split("/"))
    return parts == tuple(group[: len(parts)])


def kept(paths: Sequence[str], entries: Iterable[tuple[str, Sequence[str]]]) -> tuple[str, ...]:
    """The ``(id, group)`` entries some path keeps — ``*``, the id, or a leading run of its group — in source order."""
    return tuple(i for i, group in entries if any(_in_play(p, i, group) for p in paths))


def unnamed(paths: Sequence[str], entries: Iterable[tuple[str, Sequence[str]]]) -> list[str]:
    """The paths, ``*`` aside, that keep no entry: a typo, refused before any spend."""
    held = list(entries)
    return [p for p in paths if p != "*" and not kept([p], held)]


class Pool(StateSlice, kind=None):
    source: ClassVar[PoolSource]
    writers = "many"

    @abstractmethod
    def drawn(self, demo: Mapping[str, Sample]) -> tuple[object, ...]:
        """Its entries in draw order, read off this slice and the cycle's demo split alone."""


class BlockPool(Pool, kind="block_pool"):
    source: ClassVar[PoolSource] = PoolSource.LIBRARY
    # Required, so a reader no walked initializer writes for is refused at load.
    entries: tuple[PoolEntry, ...]

    def drawn(self, demo: Mapping[str, Sample]) -> tuple[PoolEntry, ...]:
        return self.entries


class DemoPool(Pool, kind="demo_pool"):
    source: ClassVar[PoolSource] = PoolSource.PARTITION
    keys: tuple[str, ...]

    def drawn(self, demo: Mapping[str, Sample]) -> tuple[Sample, ...]:
        return tuple(demo[key] for key in self.keys)
