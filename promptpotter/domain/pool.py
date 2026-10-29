"""A pool is the entries a variation node may draw from, banked so a round reads only what its own state holds.

Each pool is a ``StateSlice`` of one kind from one ``PoolSource``, banking what a reader draws.
"""

from __future__ import annotations

import enum
from abc import abstractmethod
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import Field

from promptpotter.domain.optimizer_state import StateSlice
from promptpotter.domain.prompt_block import AUTHORED_SOURCE, snippet_id
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from promptpotter.domain.sample import Sample


# How a reader takes a pool: not at all, as material it may adapt, or by entry id alone.
Access = Literal["off", "open", "closed"]

# The source of a block no library holds: a run on the same answer space measured it as a lift.
EARNED_SOURCE: Annotated[str, shapes_optimizer_prompt] = "earned"


class PoolSource(enum.StrEnum):
    # The installed block packs and the blocks runs earned beside them: both move under a
    # running campaign, so the pool banks its entries whole.
    LIBRARY = "library"
    # The cycle's demo split, pinned by its bank's hash: the pool banks sample keys.
    PARTITION = "partition"


@shapes_optimizer_prompt
def entry_id(field: str, text: str) -> str:
    """A block's id is its content: an edited text is a new entry, never the old id re-pointed."""
    return f"{field}.{snippet_id(text)}"


class PoolEntry(FrozenModel):
    id: str
    # The leading run an `active` path may name: ``(field, source)`` for a block.
    group: tuple[str, ...]
    text: str


OfferKind = Literal["all", "part", "source", "entry"]

_LABEL_CHARS = 72


class PoolOffer(FrozenModel):
    """One path an initializer's `active` knob may name, with what it keeps."""

    path: str = Field(description="The value to write into `active`")
    kind: OfferKind = Field(
        description="`all` is `*`, `part` a block's field, `source` the run of that field from one "
        "source, `entry` one entry by id"
    )
    label: str = Field(
        description="What the path reads as: a part's field key, labelled as a fill's part is; a "
        "source in plain words; an entry's text cut short"
    )
    entries: int = Field(description="How many entries the path keeps")


def _source_words(source: str) -> str:
    """A block's source in plain words: a cited work's source is its arXiv id."""
    if source == AUTHORED_SOURCE:
        return "Written by hand"
    if source == EARNED_SOURCE:
        return "Earned in past runs"
    return f"arXiv {source}"


def offers(entries: Sequence[PoolEntry]) -> list[PoolOffer]:
    """``*``, then each group run under its parent in first-seen order, each entry under its group; none over none."""
    if not entries:
        return []
    out = [PoolOffer(path="*", kind="all", label="All entries", entries=len(entries))]
    runs: dict[tuple[str, ...], list[PoolEntry]] = {}
    for entry in entries:
        for depth in range(1, len(entry.group) + 1):
            runs.setdefault(entry.group[:depth], []).append(entry)
    placed: set[tuple[str, ...]] = set()
    for entry in entries:
        for depth in range(1, len(entry.group) + 1):
            run = entry.group[:depth]
            if run not in placed:
                placed.add(run)
                part = depth == 1
                out.append(
                    PoolOffer(
                        path="/".join(run),
                        kind="part" if part else "source",
                        label=run[0] if part else _source_words(run[-1]),
                        entries=len(runs[run]),
                    )
                )
        text = " ".join(entry.text.split())
        label = text if len(text) <= _LABEL_CHARS else text[: _LABEL_CHARS - 1] + "…"
        out.append(PoolOffer(path=entry.id, kind="entry", label=label, entries=1))
    return out


@shapes_optimizer_prompt
def _in_play(path: str, entry: str, group: Sequence[str]) -> bool:
    if path in ("*", entry):
        return True
    parts = tuple(path.split("/"))
    return parts == tuple(group[: len(parts)])


@shapes_optimizer_prompt
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
    def drawn(self, demo: Mapping[str, Sample], /) -> tuple[object, ...]:
        """Its entries in draw order, read off this slice and the cycle's demo split alone."""

    @classmethod
    @abstractmethod
    def part(cls, entry: PoolEntry) -> str:
        """The part of a candidate the entry lands in: a prompt field, or the shots."""


class BlockPool(Pool, kind="block_pool"):
    source: ClassVar[PoolSource] = PoolSource.LIBRARY
    # Required, so a reader no walked initializer writes for is refused at load.
    entries: tuple[PoolEntry, ...]

    def drawn(self, _demo: Mapping[str, Sample]) -> tuple[PoolEntry, ...]:
        return self.entries

    @classmethod
    def part(cls, entry: PoolEntry) -> str:
        return entry.group[0]


class DemoPool(Pool, kind="demo_pool"):
    source: ClassVar[PoolSource] = PoolSource.PARTITION
    keys: tuple[str, ...]

    def drawn(self, demo: Mapping[str, Sample]) -> tuple[Sample, ...]:
        return tuple(demo[key] for key in self.keys)

    @classmethod
    def part(cls, _entry: PoolEntry) -> str:
        return SHOTS_KEY
