"""CycleLedger — the single append-only spine for facts about a campaign cycle.

Every fact (decision, phase boundary, live snapshot) is appended as a typed
``CycleRecord`` to one ``events.jsonl`` per cycle. Projections (live dashboard,
audit trail, measurement archive, langfuse mirror) subscribe to the spine and
rebuild their views deterministically — they never write to disk on their
own initiative.

Path-newtype guards (``RootCycleDir`` / ``CycleDir``) live in
:mod:`promptpotter.domain.cycle_paths` so both this module and the
projection writers in :mod:`promptpotter.infrastructure.projections` can
import them without crossing hexagonal layers.

Forks are first-class via ``CycleLedger.inherit_from(parent, offset)``: a
fork's ``iter()`` walks the parent's records up to ``offset``, then its
own appends. The parent records the cut as a
``DecisionRecord(kind=FORK_CUT, ...)`` so the divergence walker sees the
boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from pydantic import TypeAdapter

from promptpotter.domain.cycle_paths import CycleDir, Projection
from promptpotter.domain.run_records import CycleRecord

logger = logging.getLogger(__name__)

__all__ = ["CycleLedger"]


_RECORD_ADAPTER: TypeAdapter[CycleRecord] = TypeAdapter(CycleRecord)


class CycleLedger:
    """Append-only ``events.jsonl`` ledger plus an in-memory subscriber list.

    One ledger per cycle. The on-disk file is the source of truth; the
    subscriber list is a transient runtime convenience for live projections.
    Replay (``replay_into``) is independent of subscribers — it walks the
    file from offset 0.

    Forks: PhaseRecord 4 will add ``inherit_from(parent, offset)`` so a fork's
    ``iter()`` walks the parent's records up to the cut point before its own.
    The current implementation is the per-cycle base case.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._subscribers: list[Projection] = []
        self._next_offset = self._scan_existing_offset()
        self._inherit_parent: CycleLedger | None = None
        self._inherit_offset: int = 0

    @classmethod
    def open(cls, cycle_dir: CycleDir) -> CycleLedger:
        """Open (creating if absent) the ledger at ``{cycle_dir}/.runtime/ledger.jsonl``."""
        runtime_dir = Path(cycle_dir) / ".runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return cls(runtime_dir / "ledger.jsonl")

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: CycleRecord) -> int:
        """Write one record, fan out to subscribers, return its offset.

        ``fallback=str`` lets payload dicts carry arbitrary objects (e.g.
        Pydantic submodels, dataclasses, enums) without each call site
        having to project to scalars first; non-JSON values are stringified
        on disk while subscribers receive the original record in memory.
        """
        offset = self._next_offset
        line = _RECORD_ADAPTER.dump_json(record, fallback=str).decode("utf-8")
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

    def iter(self, since: int = 0) -> Iterator[CycleRecord]:
        """Yield records from disk starting at offset ``since``.

        For an inherited ledger (``inherit_from`` set), walks the parent's
        records up to the inherit offset first, then this ledger's own
        records. ``since`` is interpreted against the combined offset
        space — offset 0 is the parent's first record.

        Walks the file every call — projections that need streaming should
        ``bind`` instead. Records past the file's end are silently absent.
        """
        produced = 0
        if self._inherit_parent is not None:
            for rec in self._inherit_parent.iter():
                if produced >= self._inherit_offset:
                    break
                if produced >= since:
                    yield rec
                produced += 1
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if produced >= since:
                    yield _RECORD_ADAPTER.validate_json(line)
                produced += 1

    def inherit_from(self, parent: CycleLedger, offset: int) -> None:
        """Mark this ledger as a fork of *parent*, replaying parent records up to *offset*.

        Subsequent ``iter()`` calls walk the parent's first ``offset``
        records before this ledger's own. Subscribers see only this
        ledger's appended records (parent records aren't re-broadcast on
        subscribe — the parent already broadcast them when they happened).
        Idempotent: calling twice with the same args is a no-op.
        """
        if self._inherit_parent is parent and self._inherit_offset == offset:
            return
        if self._inherit_parent is not None:
            raise ValueError("CycleLedger.inherit_from: already inheriting; cannot rebind")
        if offset < 0:
            raise ValueError(f"CycleLedger.inherit_from: offset must be >= 0, got {offset}")
        self._inherit_parent = parent
        self._inherit_offset = offset

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
