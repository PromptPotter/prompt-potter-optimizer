"""Typed addresses + the projection protocol for the run ledger.

A cycle's address is a ``CyclePath``, never an id: cycle ids repeat across
sibling ``.inner`` sandboxes, so only the root → leaf hop chain (every hop but
the last a DESCENT) names one entity. ``CycleDir`` / ``WorkspaceDir`` are
newtypes so a write target can never be an unmarked ``Path``.
"""

from __future__ import annotations

from pathlib import Path
from typing import NewType, Protocol

from pydantic import ConfigDict

from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "CycleDir",
    "CycleHop",
    "CyclePath",
    "Projection",
    "WorkspaceDir",
    "encode_cycle_path",
]


CycleDir = NewType("CycleDir", Path)
WorkspaceDir = NewType("WorkspaceDir", Path)


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


def encode_cycle_path(path: CyclePath) -> str:
    """A path as its one string form: ``~``-joined ``campaign::cycle``, root → leaf.

    The same codec as the wire's ``descend`` tail (:func:`presentation.api.deps.decode_descend`)
    and the webapp's ``encodeCyclePath`` — the difference is only which slice is encoded, never
    the grammar. Used wherever a path has to be a dict key or a cursor component, because a
    ``list[CycleHop]`` is not hashable and a bare ``cycle_id`` is not unique across sandboxes.
    """
    return "~".join(f"{hop.campaign_id}::{hop.cycle_id}" for hop in path)


class Projection(Protocol):
    """A subscriber to the ledger's record stream.

    Projections receive every appended record via ``on_record``. They
    persist whatever projection-specific view they own (a JSON dashboard,
    a markdown log, an in-memory index). Projections never call
    ``append`` — the ledger is single-writer from the campaign loop.
    """

    def on_record(self, record: CycleRecord, offset: int) -> None: ...
