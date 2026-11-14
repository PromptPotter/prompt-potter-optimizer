from __future__ import annotations

from itertools import islice
from pathlib import Path
from typing import Any, cast

from promptpotter.domain.activity import ActivityFeed
from promptpotter.domain.projection_envelope import ProjectionEnvelope, ProjectionKind
from promptpotter.infrastructure.store.io import complete_lines
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerIndex, ledger_record, line_dict

LedgerIndex.readers(ActivityFeed)


class CycleLedgerTail:
    """Synchronous file I/O: a caller on an event loop uses ``asyncio.to_thread``. Its activity is the
    ledger index's: the index validated every line, so the tail re-validates none."""

    def __init__(self, cycle_dir: Path, cycle_id: str) -> None:
        self._cycle_id = cycle_id
        self._ledger_path = CycleLayout(cycle_dir).ledger
        self._byte_pos = 0
        self._line_index = 0

    def _index(self) -> LedgerIndex:
        return LedgerIndex.of(self._ledger_path)

    def snapshot_frame(self, body: dict[str, Any]) -> ProjectionEnvelope:
        """Picks up one PAST ``at_offset``, not at EOF: records land after the debounced dashboard fold."""
        folded = body.get("at_offset")
        offset = self._park(
            folded + 1
            if isinstance(folded, int) and not isinstance(folded, bool) and folded >= -1
            else None
        )
        return ProjectionEnvelope(
            kind="stream_snapshot",
            cycle_id=self._cycle_id,
            sequence=offset,
            payload=body,
            activity=self._index().view(ActivityFeed, offset).state,
        )

    def read_new(self) -> list[ProjectionEnvelope]:
        """A batch ends at the index's head, so its frames carry the activity there and no batch
        refolds: a line landing after the view waits for the next batch."""
        try:
            if self._ledger_path.stat().st_size <= self._byte_pos:
                return []
        except FileNotFoundError:
            return []
        feed = self._index().view(ActivityFeed)
        # The feeder stamps every line it reads on a `Positioned` fold, so this is the head's count.
        head = feed.at_offset + 1
        if head <= self._line_index:
            return []
        with self._ledger_path.open("rb") as fh:
            fh.seek(self._byte_pos)
            lines = list(islice(complete_lines(fh), head - self._line_index))
        read: list[tuple[int, dict[str, Any]]] = []
        for raw in lines:
            self._byte_pos += len(raw)
            rec = ledger_record(line_dict, raw, ledger=self._ledger_path, offset=self._line_index)
            if rec is not None:
                read.append((self._line_index, rec))
            self._line_index += 1
        if not read:
            return []
        activity = feed.state
        return [
            ProjectionEnvelope(
                kind=cast(ProjectionKind, rec["record_type"]),
                cycle_id=self._cycle_id,
                sequence=sequence,
                payload=rec,
                activity=activity,
            )
            for sequence, rec in read
        ]

    def _park(self, line: int | None) -> int:
        """Walks the bytes, not the number: a rewind or a fork leaves a ledger shorter than ``line``."""
        self._byte_pos = 0
        self._line_index = 0
        if self._ledger_path.exists():
            data = self._ledger_path.read_bytes()
            while line is None or self._line_index < line:
                nl = data.find(b"\n", self._byte_pos)
                if nl == -1:
                    break
                self._byte_pos = nl + 1
                self._line_index += 1
        return self._line_index
