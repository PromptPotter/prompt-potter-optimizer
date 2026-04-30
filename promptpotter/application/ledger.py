"""RunLedger — the single append-only spine for facts about a campaign cycle.

Every fact (decision, phase boundary, live snapshot) is appended as a typed
``RunRecord`` to one ``events.jsonl`` per cycle. Projections (live dashboard,
audit trail, measurement archive, langfuse mirror) subscribe to the spine and
rebuild their views deterministically — they never write to disk on their
own initiative.

Two newtypes (``RootCycleDir`` / ``CycleDir``) gate the projection writers'
output paths at construction. The fork-telemetry leak in the prior
``CampaignPersistenceEmitter`` (per-round audit blocks landing in the shared
root ``dashboard.json``) is impossible by construction once a projection
takes one newtype but not the other.

Forks become first-class via ``RunLedger.inherit_from(parent, offset)`` —
Phase 4 work; the placeholder hook is here so Phase 2/3 projections can be
written against the final shape.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import IO, NewType, Protocol

from pydantic import TypeAdapter

from promptpotter.domain.run_records import RunRecord

logger = logging.getLogger(__name__)

__all__ = [
    "CycleDir",
    "Projection",
    "RootCycleDir",
    "RunLedger",
]


# Newtype guards for projection write targets.
#
# - ``RootCycleDir`` is the family-root campaign dir. Telemetry projections
#   (live dashboard, output.log) bind here. Constructed only via
#   ``stores.root_dir_for(...)`` (Phase 2 will enforce this with a builder).
# - ``CycleDir`` is the per-cycle dir (root or fork). Audit projections
#   (index.json, log.md, trials/) bind here. Constructed only via
#   ``stores.campaign_dir_for(...)``.
#
# A projection that takes ``RootCycleDir`` cannot accidentally write per-fork
# audit data — its constructor refuses the wrong newtype.
RootCycleDir = NewType("RootCycleDir", Path)
CycleDir = NewType("CycleDir", Path)


class Projection(Protocol):
    """A subscriber to the ledger's record stream.

    Projections receive every appended record via ``on_record``. They may
    persist whatever projection-specific view they own (a JSON dashboard, a
    markdown log, an in-memory index). Projections never call ``append`` —
    the ledger is single-writer from the campaign loop.
    """

    def on_record(self, record: RunRecord, offset: int) -> None: ...


_RECORD_ADAPTER: TypeAdapter[RunRecord] = TypeAdapter(RunRecord)


class RunLedger:
    """Append-only ``events.jsonl`` ledger plus an in-memory subscriber list.

    One ledger per cycle. The on-disk file is the source of truth; the
    subscriber list is a transient runtime convenience for live projections.
    Replay (``replay_into``) is independent of subscribers — it walks the
    file from offset 0.

    Forks: Phase 4 will add ``inherit_from(parent, offset)`` so a fork's
    ``iter()`` walks the parent's records up to the cut point before its own.
    The current implementation is the per-cycle base case.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._subscribers: list[Projection] = []
        self._next_offset = self._scan_existing_offset()

    @classmethod
    def open(cls, cycle_dir: CycleDir) -> RunLedger:
        """Open (creating if absent) the ledger for a cycle dir."""
        cycle_dir.mkdir(parents=True, exist_ok=True)
        return cls(cycle_dir / "ledger.jsonl")

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: RunRecord) -> int:
        """Write one record, fan out to subscribers, return its offset."""
        offset = self._next_offset
        line = _RECORD_ADAPTER.dump_json(record).decode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_append() as fh:
            fh.write(line + "\n")
        self._next_offset += 1
        for sub in self._subscribers:
            try:
                sub.on_record(record, offset)
            except Exception:
                logger.exception(
                    "projection %s failed on offset %d",
                    type(sub).__name__,
                    offset,
                )
        return offset

    def iter(self, since: int = 0) -> Iterator[RunRecord]:
        """Yield records from disk starting at offset ``since``.

        Walks the file every call — projections that need streaming should
        ``bind`` instead. Records past the file's end are silently absent.
        """
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i < since:
                    continue
                line = line.strip()
                if not line:
                    continue
                yield _RECORD_ADAPTER.validate_json(line)

    def bind(self, projection: Projection) -> None:
        """Subscribe a projection to subsequent appends.

        Does NOT replay history; call ``replay_into`` first if the projection
        needs to catch up from offset 0.
        """
        self._subscribers.append(projection)

    def replay_into(self, projection: Projection) -> None:
        """Walk the ledger from offset 0 and feed each record to ``projection``.

        Does NOT bind for future appends — call ``bind`` separately if live
        updates are also wanted.
        """
        for offset, record in enumerate(self.iter()):
            projection.on_record(record, offset)

    @property
    def next_offset(self) -> int:
        """Offset that the next ``append`` will receive."""
        return self._next_offset

    # -- internals -------------------------------------------------------------

    def _scan_existing_offset(self) -> int:
        if not self._path.exists():
            return 0
        count = 0
        with self._path.open("rb") as fh:
            for _ in fh:
                count += 1
        return count

    def _open_append(self) -> IO[str]:
        return self._path.open("a", encoding="utf-8")
