"""A cycle's address is a ``CyclePath``, never an id: ids repeat across sibling ``.inner`` sandboxes, so only the
root→leaf hop chain names one entity. ``CycleDir`` / ``WorkspaceDir`` are newtypes so a write target is never bare."""

from __future__ import annotations

from pathlib import Path
from typing import NewType

from pydantic import ConfigDict

from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "CycleDir",
    "CycleHop",
    "CyclePath",
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
    """A path as its one string form: ``~``-joined ``campaign::cycle``, root → leaf. The SAME codec as the wire's
    ``descend`` tail and the webapp's encoder — only the slice encoded differs, never the grammar."""
    return "~".join(f"{hop.campaign_id}::{hop.cycle_id}" for hop in path)
