from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from promptpotter.config.paths import block_packs_root
from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import BlockPack, PackRefusal, Presumption
from promptpotter.shared.errors import ConflictError, NotFoundError, validation_reason
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

shapes_optimizer_prompt(__name__)

PACK_FILE = "pack.json"


def _parsed(name: str, file: Path) -> BlockPack | PackRefusal:
    """Each failing field by its path and rule alone: a value or a server path never leaves."""
    try:
        return BlockPack.model_validate_json(file.read_bytes())
    except ValidationError as exc:
        return PackRefusal(
            name=name,
            code="block_pack_invalid",
            reason=f"its pack.json is no pack ({validation_reason(exc, shown=3)}); fix or remove it",
        )


@dataclass(frozen=True)
class PackShelf:
    """The packs one account reads: the install's, read-only to it, beside its own, one name one
    pack. A pack that cannot serve is refused alone; the rest serve on."""

    # None: no account is in scope (the boot check), so the install's packs alone.
    account: Path | None

    def _roots(self) -> tuple[Path, ...]:
        install = block_packs_root()
        return (install,) if self.account is None else (install, self.account)

    def _files(self, name: str) -> list[Path]:
        return [f for root in self._roots() if (f := root / name / PACK_FILE).is_file()]

    def _opened(self, name: str) -> BlockPack | PackRefusal | None:
        held = self._files(name)
        if len(held) > 1:
            return PackRefusal(
                name=name,
                code="block_pack_collision",
                reason="the install and this account both hold a pack of this name; rename one",
            )
        return _parsed(name, held[0]) if held else None

    def shelved(self) -> dict[str, BlockPack | PackRefusal]:
        """Every directory under either root holding a `pack.json`, by name: its pack, or why not."""
        found = {
            p.name
            for root in self._roots()
            if root.is_dir()
            for p in root.iterdir()
            if (p / PACK_FILE).is_file()
        }
        return {
            name: opened for name in sorted(found) if (opened := self._opened(name)) is not None
        }

    def names(self) -> list[str]:
        return [name for name, pack in self.shelved().items() if isinstance(pack, BlockPack)]

    def refusals(self) -> list[PackRefusal]:
        return [pack for pack in self.shelved().values() if isinstance(pack, PackRefusal)]

    def missing(self, names: Sequence[str]) -> list[str]:
        return [name for name in names if not self._files(name)]

    def read(self, name: str) -> BlockPack:
        opened = self._opened(name)
        if opened is None:
            raise NotFoundError(f"no block pack {name!r}", code="block_pack_not_found")
        if isinstance(opened, PackRefusal):
            raise ConflictError(
                f"block pack {name!r} is refused: {opened.reason}", code=opened.code
            )
        return opened

    def account_file(self, name: str) -> Path:
        """Where the account's own pack *name* is written, refused while the install holds that name."""
        assert self.account is not None, "a write names the account it lands in"
        if (block_packs_root() / name / PACK_FILE).is_file():
            raise ConflictError(
                f"the install holds a block pack named {name!r}, so this account cannot write one",
                code="block_pack_collision",
            )
        return self.account / name / PACK_FILE

    def _pack_entries(self, name: str) -> list[tuple[PoolEntry, frozenset[Presumption]]]:
        """One entry per part a snippet suits, in the snippet's order and then the parts' render order."""
        return [
            (
                PoolEntry(id=entry_id(part, s.text), group=(part, s.source), text=s.text),
                s.presumes,
            )
            for s in self.read(name).snippets
            for part in s.parts
        ]

    def _held(self, names: Sequence[str]) -> dict[str, tuple[PoolEntry, frozenset[Presumption]]]:
        held: dict[str, tuple[PoolEntry, frozenset[Presumption]]] = {}
        holder: dict[str, str] = {}
        for name in names:
            for entry, presumes in self._pack_entries(name):
                if entry.id in holder:
                    raise ValueError(
                        f"pack {name!r} repeats {entry.id}, held by {holder[entry.id]!r}"
                    )
                holder[entry.id] = name
                held[entry.id] = (entry, presumes)
        return held

    def entries(
        self, names: Sequence[str], *, admits: frozenset[Presumption]
    ) -> dict[str, PoolEntry]:
        """By content id, in the packs' order and then each pack's own, less what the run cannot admit."""
        return {
            i: entry for i, (entry, presumes) in self._held(names).items() if presumes <= admits
        }
