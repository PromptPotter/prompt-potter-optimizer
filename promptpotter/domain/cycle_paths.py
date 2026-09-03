"""A cycle's address is a ``CyclePath``, never an id: ids repeat across sibling ``.inner`` sandboxes, so only the
root→leaf hop chain names one entity. ``CycleDir`` / ``WorkspaceDir`` are newtypes so a write target is never bare."""

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
    "encode_cycle_path",
]


CycleDir = NewType("CycleDir", Path)
WorkspaceDir = NewType("WorkspaceDir", Path)

# THE grammar of a cycle address — one author for both languages. The browser re-declared all
# four by hand until `scripts/build_ts_types.py::_emit_cycle_path_grammar` started emitting
# them; the same hand-mirror shape `ABORT_LENS_LABELS` was retired for.
#
# It lives in `domain/` because that is the only direction the layers allow: `io.py` needs the
# charset and `infrastructure/` may import `domain/`, never the reverse.
HOP_SEP = "~"
UNIT_SEP = "::"
# Spelled `[a-zA-Z0-9_.-]` rather than `[a-zA-Z0-9_\-\.]`: identical (a trailing `-` in a class
# is a literal, an unescaped `.` inside one is), and byte-identical to the regex the browser
# already carries — which is what lets the emitter wrap it in `/…/` with no translation step.
ID_COMPONENT_PATTERN = r"^[a-zA-Z0-9_.-]+$"
# Paired with the charset, never alone: `.` / `..` / `...` all match it and are traversal
# segments. Its own pattern rather than a `set(name) == {"."}` test so both languages read the
# same rule from the same string.
ALL_DOTS_PATTERN = r"^\.+$"

ID_COMPONENT_RE = re.compile(ID_COMPONENT_PATTERN)
ALL_DOTS_RE = re.compile(ALL_DOTS_PATTERN)

# The round-trip precondition, asserted rather than promised. Both codecs stated it in prose
# and nothing checked it: admit `~` or `:` into the charset and a deep path stops failing —
# it splits into a DIFFERENT well-formed address. No raise, no log line, the wrong cycle.
if any(ID_COMPONENT_RE.match(ch) for ch in HOP_SEP + UNIT_SEP):
    raise RuntimeError(
        f"cycle-path grammar is not round-trippable: a separator in {HOP_SEP + UNIT_SEP!r} "
        f"matches the id charset {ID_COMPONENT_PATTERN!r}, so encode/decode would silently "
        "resolve one address as another."
    )


class CycleHop(StrictModel):
    """One ``(campaign, cycle)`` step of a :data:`CyclePath`.

    BOTH components are load-bearing, in descent as well as at the leaf: a cycle_id is
    content-addressed on the origin, so every campaign minted from one declaration shares
    it and a sandbox keyed on the cycle alone serves another campaign's inner fan-out
    (``stores.py::inner_sandbox_store``). Anything indexing nodes of a served tree keys on
    the pair for the same reason.

    A model rather than a tuple so it serializes as ``{campaign_id, cycle_id}``: a
    served path is read by a client that already speaks this shape, and a positional
    pair on the wire is one more thing for a reader to decode wrongly.
    """

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    cycle_id: str


CyclePath = tuple[CycleHop, ...]


@dataclass(frozen=True, slots=True)
class Cut:
    """WHERE a fold stops: a cycle, and a line index in its OWN records — an inherited prefix is
    walked in front of that space, never inside it. ``None`` = the head. Model: `stable-api.md` §6."""

    cycle: CycleDir
    hop: CycleHop
    offset: int | None = None


def encode_cycle_path(path: CyclePath) -> str:
    """A path as its one string form: :data:`HOP_SEP`-joined ``campaign``:data:`UNIT_SEP`\\ ``cycle``,
    root → leaf. The SAME codec as the wire's ``descend`` tail and the webapp's encoder — only the
    slice encoded differs, never the grammar."""
    return HOP_SEP.join(f"{hop.campaign_id}{UNIT_SEP}{hop.cycle_id}" for hop in path)


def decode_cycle_path(encoded: str) -> CyclePath:
    """The inverse, and it lives here so the two halves of one grammar cannot drift apart. Raises
    ``ValueError`` on a malformed hop, for each entry point to turn into its own refusal — a 400 on
    the route, a printed line on the terminal. Component VALIDATION is ``descend_store``'s, which
    also owns the descent itself; this is the codec and nothing more.

    ``""`` decodes to ``()`` — a real value here, since ``deps.py::decode_descend`` reads it as
    "no hops below the root", i.e. depth 1. The browser's twin answers ``null`` instead, and that
    is not drift: its ``CyclePath`` is documented non-empty by construction, so it has no empty
    value to return. One grammar, two types."""
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
