"""Typed addresses + projection protocol for the run ledger.

Two newtype guards for ledger / projection write targets:

* ``CycleDir`` — the per-cycle dir (``campaigns/{campaign_id}/cycles/{cycle_id}``).
  Every cycle (root, fork, sweep, diag) owns its own ``dashboard.json`` +
  audit tree here; telemetry and audit projections both bind to it.
  Constructed from ``cycle_dir_for(...)``.
* ``WorkspaceDir`` — the tenant root (``projects/{tenant}/``). Used by
  the workspace-scoped ``CycleEventLog`` (the Persistence sibling at
  ``projects/{tenant}/.workspace/events.jsonl`` per ``docs/architecture.md``
  §0) — the ledger backend-scoped commands (``register-backend``,
  ``mint-campaign``) ride. Construct from ``Stores.base_dir``.

…and the one ADDRESS a cycle answers to at any depth:

* ``CycleHop`` / ``CyclePath`` — root → leaf. A bare ``cycle_id`` is ambiguous
  across campaigns, and a ``(campaign, cycle)`` pair is ambiguous across stores
  once an L4 inner forest is open (``.inner/<cycle_id>`` is a whole projects tree
  of its own, and inner cycle ids repeat across sibling sandboxes). The path is
  what disambiguates: every hop but the last is a DESCENT into that hop's
  sandbox, and the last names the entity. This is the wire's ``descend`` chain
  and the webapp's ``CyclePath`` as one type, so no reader re-derives it.

These types live in :mod:`promptpotter.domain` so both ``application``
(``CycleEventLog``-side) and ``infrastructure`` (projection-side) can import
them without crossing hexagonal layers.
"""

from __future__ import annotations

from pathlib import Path
from typing import NewType, Protocol

from pydantic import ConfigDict

from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.strict_model import StrictModel

__all__ = ["CycleDir", "CycleHop", "CyclePath", "Projection", "WorkspaceDir"]


CycleDir = NewType("CycleDir", Path)
WorkspaceDir = NewType("WorkspaceDir", Path)


class CycleHop(StrictModel):
    """One ``(campaign, cycle)`` step of a :data:`CyclePath`.

    The campaign component is carried to name the leaf and for symmetry with the
    webapp; it plays no part in descent — a sandbox is keyed on the cycle id alone.

    A model rather than a tuple so it serializes as ``{campaign_id, cycle_id}``: a
    served path is read by a client that already speaks this shape, and a positional
    pair on the wire is one more thing for a reader to decode wrongly.
    """

    model_config = ConfigDict(frozen=True)

    campaign_id: str
    cycle_id: str


CyclePath = tuple[CycleHop, ...]


class Projection(Protocol):
    """A subscriber to the ledger's record stream.

    Projections receive every appended record via ``on_record``. They
    persist whatever projection-specific view they own (a JSON dashboard,
    a markdown log, an in-memory index). Projections never call
    ``append`` — the ledger is single-writer from the campaign loop.
    """

    def on_record(self, record: CycleRecord, offset: int) -> None: ...
