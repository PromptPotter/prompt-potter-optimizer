"""Typed paths + projection protocol for the run ledger.

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

These types live in :mod:`promptpotter.domain` so both ``application``
(``CycleEventLog``-side) and ``infrastructure`` (projection-side) can import
them without crossing hexagonal layers.
"""

from __future__ import annotations

from pathlib import Path
from typing import NewType, Protocol

from promptpotter.domain.run_records import CycleRecord

__all__ = ["CycleDir", "Projection", "WorkspaceDir"]


CycleDir = NewType("CycleDir", Path)
WorkspaceDir = NewType("WorkspaceDir", Path)


class Projection(Protocol):
    """A subscriber to the ledger's record stream.

    Projections receive every appended record via ``on_record``. They
    persist whatever projection-specific view they own (a JSON dashboard,
    a markdown log, an in-memory index). Projections never call
    ``append`` — the ledger is single-writer from the campaign loop.
    """

    def on_record(self, record: CycleRecord, offset: int) -> None: ...
