"""Typed paths + projection protocol for the run ledger.

Two newtype guards for projection write targets:

* ``RootCycleDir`` — the family-root campaign dir. Telemetry projections
  bind here. Constructed from ``stores.root_dir_for(...)``.
* ``CycleDir`` — the per-cycle dir (root or fork). Audit projections
  bind here. Constructed from ``stores.campaign_dir_for(...)``.

A projection that takes ``RootCycleDir`` cannot accidentally write per-fork
audit data — its constructor refuses the wrong newtype (mypy) and
asserts at construction time (runtime).

These types live in :mod:`promptpotter.domain` so both ``application``
(``RunLedger``-side) and ``infrastructure`` (projection-side) can import
them without crossing hexagonal layers.
"""

from __future__ import annotations

from pathlib import Path
from typing import NewType, Protocol

from promptpotter.domain.run_records import RunRecord

__all__ = ["CycleDir", "Projection", "RootCycleDir"]


RootCycleDir = NewType("RootCycleDir", Path)
CycleDir = NewType("CycleDir", Path)


class Projection(Protocol):
    """A subscriber to the ledger's record stream.

    Projections receive every appended record via ``on_record``. They
    persist whatever projection-specific view they own (a JSON dashboard,
    a markdown log, an in-memory index). Projections never call
    ``append`` — the ledger is single-writer from the campaign loop.
    """

    def on_record(self, record: RunRecord, offset: int) -> None: ...
