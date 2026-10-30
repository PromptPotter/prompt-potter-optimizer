from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.config.paths import block_packs_root
from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import BlockPack, Presumption
from promptpotter.shared.errors import ConflictError, NotFoundError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

shapes_optimizer_prompt(__name__)

_PACK_FILE = "pack.json"


@dataclass(frozen=True)
class PackShelf:
    """The packs one account reads: the install's, read-only to it, beside its own, one name one pack."""

    # None: no account is in scope (the boot check), so the install's packs alone.
    account: Path | None

    def _roots(self) -> tuple[Path, ...]:
        install = block_packs_root()
        return (install,) if self.account is None else (install, self.account)

    def file(self, name: str) -> Path | None:
        held = [f for root in self._roots() if (f := root / name / _PACK_FILE).is_file()]
        if len(held) > 1:
            raise ConflictError(
                f"block pack {name!r} is both the install's and this account's; one must be renamed",
                code="block_pack_collision",
            )
        return held[0] if held else None

    def missing(self, names: Sequence[str]) -> list[str]:
        return [name for name in names if self.file(name) is None]

    def read(self, name: str) -> BlockPack:
        file = self.file(name)
        if file is None:
            raise NotFoundError(f"no block pack {name!r}", code="block_pack_not_found")
        return BlockPack.model_validate_json(file.read_text(encoding="utf-8"))

    def names(self) -> list[str]:
        """Every directory under either root holding a `pack.json`, by name."""
        found = {
            p.name
            for root in self._roots()
            if root.is_dir()
            for p in root.iterdir()
            if (p / _PACK_FILE).is_file()
        }
        return sorted(name for name in found if self.file(name) is not None)

    def account_file(self, name: str) -> Path:
        """Where the account's own pack *name* is written, refused while the install holds that name."""
        assert self.account is not None, "a write names the account it lands in"
        if (block_packs_root() / name / _PACK_FILE).is_file():
            raise ConflictError(
                f"the install holds a block pack named {name!r}, so this account cannot write one",
                code="block_pack_collision",
            )
        return self.account / name / _PACK_FILE

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
