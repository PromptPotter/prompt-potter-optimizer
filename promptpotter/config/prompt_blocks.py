from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from pydantic import ValidationError

from promptpotter.config.paths import block_packs_root
from promptpotter.domain.pool import PoolEntry, entry_id
from promptpotter.domain.prompt_block import BlockPack, PackRefusal, Presumption
from promptpotter.shared.errors import ConflictError, NotFoundError
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

shapes_optimizer_prompt(__name__)

_PACK_FILE = "pack.json"


class Shelved(NamedTuple):
    """Every pack the account reads, by name, beside each it refuses: one bad pack fails only itself."""

    packs: dict[str, BlockPack]
    refused: list[PackRefusal]


def unreadable_pack(name: str, exc: ValidationError) -> PackRefusal:
    """Names no path: a refusal is served to the account, which never sees the install's tree."""
    first = exc.errors()[0]
    return PackRefusal(
        pack=name,
        code="block_pack_unreadable",
        reason=(
            f"block pack {name!r} does not validate: {exc.error_count()} error(s), the first at "
            f"{'.'.join(map(str, first['loc'])) or 'its root'} ({first['msg']}). "
            "`python -m promptpotter restamp --apply` prunes a key this build dropped; "
            "anything else is fixed in its pack.json, or the pack deleted"
        ),
    )


@dataclass(frozen=True)
class PackShelf:
    """The packs one account reads: the install's, read-only to it, beside its own, one name one pack."""

    # None: no account is in scope (the boot check), so the install's packs alone.
    account: Path | None

    def _roots(self) -> tuple[Path, ...]:
        install = block_packs_root()
        return (install,) if self.account is None else (install, self.account)

    def _load(self, name: str) -> BlockPack | PackRefusal | None:
        held = [f for root in self._roots() if (f := root / name / _PACK_FILE).is_file()]
        if not held:
            return None
        if len(held) > 1:
            return PackRefusal(
                pack=name,
                code="block_pack_collision",
                reason=f"block pack {name!r} is both the install's and this account's; "
                "one must be renamed",
            )
        try:
            return BlockPack.model_validate_json(held[0].read_bytes())
        except ValidationError as exc:
            return unreadable_pack(name, exc)

    def shelved(self) -> Shelved:
        """Every directory under either root holding a `pack.json`, by name."""
        found = sorted(
            {
                p.name
                for root in self._roots()
                if root.is_dir()
                for p in root.iterdir()
                if (p / _PACK_FILE).is_file()
            }
        )
        shelved = Shelved({}, [])
        for name in found:
            match self._load(name):
                case BlockPack() as pack:
                    shelved.packs[name] = pack
                case PackRefusal() as refusal:
                    shelved.refused.append(refusal)
        return shelved

    def names(self) -> list[str]:
        return list(self.shelved().packs)

    def unread(self, names: Sequence[str]) -> dict[str, str]:
        """Each of *names* a run cannot draw, by why."""
        unread: dict[str, str] = {}
        for name in names:
            match self._load(name):
                case None:
                    unread[name] = f"{name!r}, which neither the install nor this account holds"
                case PackRefusal(reason=reason):
                    unread[name] = reason
        return unread

    def read(self, name: str) -> BlockPack:
        match self._load(name):
            case None:
                raise NotFoundError(f"no block pack {name!r}", code="block_pack_not_found")
            case PackRefusal(code=code, reason=reason):
                raise ConflictError(reason, code=code)
            case pack:
                return pack

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
