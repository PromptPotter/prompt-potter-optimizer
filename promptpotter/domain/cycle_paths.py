"""Typed paths + projection protocol for the run ledger.

Two newtype guards for projection write targets:

* ``SessionFamilyDir`` — a session-family root cycle dir. Telemetry
  projections bind here: one ``dashboard.json`` per session (the session
  root + its forks share it). It is itself a cycle dir — the root cycle's
  — so it is constructed from ``cycle_dir_for(...)`` against the session
  root id (``root_cycle_id(cycle_id)``).
* ``CycleDir`` — the per-cycle dir (``campaigns/{campaign_id}/cycles/{cycle_id}``).
  Audit projections bind here. Constructed from ``cycle_dir_for(...)``.

A projection that takes ``SessionFamilyDir`` cannot accidentally write
per-cycle audit data — its constructor refuses the wrong newtype (mypy)
and asserts at construction time (runtime).

These types live in :mod:`promptpotter.domain` so both ``application``
(``CycleEventLog``-side) and ``infrastructure`` (projection-side) can import
them without crossing hexagonal layers.
"""

from __future__ import annotations

from pathlib import Path
from typing import NewType, Protocol

from promptpotter.domain.run_records import CycleRecord

__all__ = ["CycleDir", "Projection", "SessionFamilyDir"]


SessionFamilyDir = NewType("SessionFamilyDir", Path)
CycleDir = NewType("CycleDir", Path)


class Projection(Protocol):
    """A subscriber to the ledger's record stream.

    Projections receive every appended record via ``on_record``. They
    persist whatever projection-specific view they own (a JSON dashboard,
    a markdown log, an in-memory index). Projections never call
    ``append`` — the ledger is single-writer from the campaign loop.
    """

    def on_record(self, record: CycleRecord, offset: int) -> None: ...
