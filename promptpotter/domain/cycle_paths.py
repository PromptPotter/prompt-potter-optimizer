"""A cycle's address is a ``CyclePath``, never an id: ids repeat across sibling ``.inner`` sandboxes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NewType

from pydantic import ConfigDict

from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "ALL_DOTS_PATTERN",
    "HOP_SEP",
    "ID_COMPONENT_PATTERN",
    "UNIT_SEP",
    "Cut",
    "CycleDir",
    "CycleHop",
    "CyclePath",
    "WorkspaceDir",
    "command_address",
    "encode_cycle_path",
]


CycleDir = NewType("CycleDir", Path)
WorkspaceDir = NewType("WorkspaceDir", Path)

# One author for both languages: `scripts/build_ts_types.py::_emit_cycle_path_grammar` emits all four.
HOP_SEP = "~"
UNIT_SEP = "::"
# Spelled without escapes: the emitter wraps it in `/…/` untranslated, so the browser's is byte-identical.
ID_COMPONENT_PATTERN = r"^[a-zA-Z0-9_.-]+$"
# Paired with the charset, never alone: `.` / `..` / `...` match the charset and are traversal segments.
ALL_DOTS_PATTERN = r"^\.+$"

ID_COMPONENT_RE = re.compile(ID_COMPONENT_PATTERN)
ALL_DOTS_RE = re.compile(ALL_DOTS_PATTERN)

# Admit `~` or `:` into the charset and a deep path splits into a DIFFERENT well-formed address.
if any(ID_COMPONENT_RE.match(ch) for ch in HOP_SEP + UNIT_SEP):
    raise RuntimeError(
        f"cycle-path grammar is not round-trippable: a separator in {HOP_SEP + UNIT_SEP!r} "
        f"matches the id charset {ID_COMPONENT_PATTERN!r}, so encode/decode would silently "
        "resolve one address as another."
    )


class CycleHop(StrictModel):
    """One ``(campaign, cycle)`` step of a cycle path; neither id names a cycle alone."""

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    cycle_id: str


CyclePath = tuple[CycleHop, ...]


@dataclass(frozen=True, slots=True)
class Cut:
    """``offset`` indexes the cycle's OWN records, never an inherited prefix; ``None`` is the head."""

    cycle: CycleDir
    hop: CycleHop
    offset: int | None = None


def encode_cycle_path(path: CyclePath) -> str:
    """The SAME codec as the wire's ``descend`` tail and the webapp's encoder."""
    return HOP_SEP.join(f"{hop.campaign_id}{UNIT_SEP}{hop.cycle_id}" for hop in path)


def command_address(path: CyclePath) -> dict[str, str]:
    root, *tail = path
    address = {"campaign_id": root.campaign_id, "cycle_id": root.cycle_id}
    if tail:
        address["descend"] = encode_cycle_path(tuple(tail))
    return address


def decode_cycle_path(encoded: str) -> CyclePath:
    """``""`` decodes to ``()`` (no hops below the root); component VALIDATION is ``descend_store``'s."""
    if not encoded:
        return ()
    hops: list[CycleHop] = []
    for seg in encoded.split(HOP_SEP):
        campaign, sep, cycle = seg.partition(UNIT_SEP)
        if not sep or not campaign or not cycle:
            raise ValueError(
                f"Malformed cycle-path hop: {seg!r} (expected 'campaign{UNIT_SEP}cycle')."
            )
        hops.append(CycleHop(campaign_id=campaign, cycle_id=cycle))
    return tuple(hops)
