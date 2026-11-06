from __future__ import annotations

import enum
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Self

from pydantic import ValidationError

from promptpotter.config.paths import block_packs_root
from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import (
    BlockPack,
    Cited,
    Presumption,
    Snippet,
    snippet_id,
)
from promptpotter.shared.errors import ConflictError, NotFoundError, validation_reason
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

PACK_FILE = "pack.json"


class PackRefusalKind(enum.StrEnum):
    """Each kind the shelf holds but does not serve, a whole pack or one snippet of the account's,
    while the rest of the library serves on: its code, and what it is. A refusal names its pack in
    `details`."""

    reason: str

    def __new__(cls, code: str, reason: str) -> Self:
        kind = str.__new__(cls, code)
        kind._value_ = code
        kind.reason = reason
        return kind

    INVALID = "block_pack_invalid", "its `pack.json` is no pack"
    COLLISION = "block_pack_collision", "the install and this account both hold the name"
    REPEATS = (
        "block_pack_repeats",
        "an install pack holding a text an install pack before it in name order holds",
    )
    SNIPPET_HELD = (
        "block_snippet_held",
        "one snippet of the account's pack whose text an install pack holds, which serves as that "
        "snippet while the rest of the account's pack serves on",
    )


PACK_REFUSAL_KINDS = (
    "; ".join(f"`{kind}`: {kind.reason}" for kind in PackRefusalKind) + ". One text is one snippet"
)


def _refused(kind: PackRefusalKind, name: str, said: str) -> ConflictError:
    """Each raiser's one door: a refusal of the shelf is a row of `PackRefusalKind`."""
    return ConflictError(said, code=kind.value, details={"pack": name})


def _parsed(name: str, file: Path) -> BlockPack | ConflictError:
    """Each failing field by its path and rule alone: a value or a server path never leaves."""
    try:
        return BlockPack.model_validate_json(file.read_bytes())
    except ValidationError as exc:
        return _refused(
            PackRefusalKind.INVALID,
            name,
            f"block pack {name!r} is refused: its pack.json is no pack "
            f"({validation_reason(exc, shown=3)}); fix or remove it",
        )


def _found(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / PACK_FILE).is_file())


def _serving(pack: BlockPack, kept: list[Snippet]) -> BlockPack:
    """*pack* less the snippets it does not serve, and the works only those cited."""
    cited = {
        work
        for s in kept
        if isinstance(s.provenance, Cited)
        for work in (s.provenance.work, *s.provenance.via)
    }
    return BlockPack(
        works={k: w for k, w in pack.works.items() if k in cited}, snippets=tuple(kept)
    )


@dataclass(frozen=True)
class _Read:
    shelved: dict[str, BlockPack | ConflictError]
    # Snippets of the account's packs that serve as an install pack's snippet of the same text.
    skipped: list[ConflictError] = field(default_factory=list)


@dataclass(frozen=True)
class PackShelf:
    """The packs one account reads: the install's, read-only to it, beside its own, one name one
    pack and one text one snippet. A pack that cannot serve is refused alone; the rest serve on.
    Parsed once per instance: a write reads a fresh shelf."""

    # None: no account is in scope (the boot check), so the install's packs alone.
    account: Path | None

    def _roots(self) -> tuple[Path, ...]:
        install = block_packs_root()
        return (install,) if self.account is None else (install, self.account)

    def _files(self, name: str) -> list[Path]:
        return [f for root in self._roots() if (f := root / name / PACK_FILE).is_file()]

    def _opened(self, name: str) -> BlockPack | ConflictError:
        held = self._files(name)
        if len(held) > 1:
            return _refused(
                PackRefusalKind.COLLISION,
                name,
                f"block pack {name!r} is refused: the install and this account both hold a pack "
                "of this name; rename one",
            )
        return _parsed(name, held[0])

    @cached_property
    def _read(self) -> _Read:
        """The install's packs first, by name, a pack holding a text one before it holds refused;
        then the account's, by name, where a text the shelf already holds is that snippet, so it
        is skipped alone and the account's pack is never refused for it."""
        read = _Read(shelved={})
        holder: dict[str, str] = {}
        for name in _found(block_packs_root()):
            opened = self._opened(name)
            if isinstance(opened, BlockPack):
                ids = [snippet_id(s.text) for s in opened.snippets]
                if repeated := next((i for i in ids if i in holder), None):
                    opened = _refused(
                        PackRefusalKind.REPEATS,
                        name,
                        f"block pack {name!r} is refused: its snippet {repeated} is pack "
                        f"{holder[repeated]!r}'s text too, and one text is one snippet; remove "
                        "it from one",
                    )
                else:
                    holder.update(dict.fromkeys(ids, name))
            read.shelved[name] = opened
        for name in [] if self.account is None else _found(self.account):
            if name in read.shelved:
                continue
            opened = self._opened(name)
            if isinstance(opened, BlockPack):
                kept: list[Snippet] = []
                for s in opened.snippets:
                    if (sid := snippet_id(s.text)) in holder:
                        read.skipped.append(
                            _refused(
                                PackRefusalKind.SNIPPET_HELD,
                                name,
                                f"block pack {name!r}: its snippet {sid} is pack "
                                f"{holder[sid]!r}'s text, so it serves as that pack's snippet",
                            )
                        )
                    else:
                        kept.append(s)
                        holder[sid] = name
                if len(kept) < len(opened.snippets):
                    opened = _serving(opened, kept)
            read.shelved[name] = opened
        return read

    def shelved(self) -> dict[str, BlockPack | ConflictError]:
        """Every directory under either root holding a `pack.json`: the install's by name, then
        the account's — its pack as it serves, or why not."""
        return self._read.shelved

    def skipped(self) -> list[ConflictError]:
        return self._read.skipped

    def names(self) -> list[str]:
        return [name for name, pack in self.shelved().items() if isinstance(pack, BlockPack)]

    def refusals(self) -> dict[str, ConflictError]:
        return {name: p for name, p in self.shelved().items() if isinstance(p, ConflictError)}

    def missing(self, names: Sequence[str]) -> list[str]:
        return [name for name in names if not self._files(name)]

    def read(self, name: str) -> BlockPack:
        opened = self.shelved().get(name)
        if opened is None:
            raise NotFoundError(f"no block pack {name!r}", code="block_pack_not_found")
        if isinstance(opened, ConflictError):
            raise opened
        return opened

    def written(self, name: str) -> BlockPack:
        """The account's pack *name* as its file holds it, a skipped snippet among them: what a
        bank appends to, so a skip never drops what the account wrote."""
        file = self.account_file(name)
        if not file.is_file():
            # Absent until its first bank: an account's pack is made by writing into it.
            return BlockPack(works={}, snippets=())
        opened = _parsed(name, file)
        if isinstance(opened, ConflictError):
            raise opened
        return opened

    def account_file(self, name: str) -> Path:
        """Where the account's own pack *name* is written, refused while the install holds that name."""
        assert self.account is not None, "a write names the account it lands in"
        if (block_packs_root() / name / PACK_FILE).is_file():
            raise _refused(
                PackRefusalKind.COLLISION,
                name,
                f"the install holds a block pack named {name!r}, so this account cannot write one",
            )
        return self.account / name / PACK_FILE

    @shapes_optimizer_prompt
    def entries(
        self, names: Sequence[str], *, admits: frozenset[Presumption]
    ) -> dict[str, PoolEntry]:
        """By content id, one per part a snippet suits, in the packs' order, then each pack's own,
        then `PROMPT_FIELDS` order, less what the run cannot admit. A pack named but missing or
        refused holds none: `BlockPoolInitializer.refusal` names it."""
        shelved = self.shelved()
        held = [
            PoolEntry(id=entry_id(part, s.text), group=(part, s.source), text=s.text)
            for name in names
            if isinstance(pack := shelved.get(name), BlockPack)
            for s in pack.snippets
            if s.presumes <= admits
            for part in s.parts
        ]
        return {entry.id: entry for entry in held}
