from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from promptpotter.domain.activity import ActivityFeed, ActivityState
from promptpotter.domain.projection_envelope import ProjectionEnvelope, ProjectionKind
from promptpotter.infrastructure.store.io import complete_lines
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerIndex, ledger_record, line_dict


class CycleLedgerTail:
    """Synchronous file I/O: a caller on an event loop uses ``asyncio.to_thread``. Its activity is the
    ledger index's: the index validated every line, so the tail re-validates none."""

    def __init__(self, cycle_dir: Path, cycle_id: str) -> None:
        self._cycle_id = cycle_id
        self._ledger_path = CycleLayout(cycle_dir).ledger
        self._byte_pos = 0
        self._line_index = 0

    def _activity(self) -> ActivityState:
        return LedgerIndex.of(self._ledger_path, ()).view(ActivityFeed, self._line_index).state()

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
            activity=self._activity(),
        )

    def read_new(self) -> list[ProjectionEnvelope]:
        """Every frame of one batch carries the activity at the batch's end."""
        if not self._ledger_path.exists():
            return []
        with self._ledger_path.open("rb") as fh:
            fh.seek(self._byte_pos)
            lines = list(complete_lines(fh))
        read: list[tuple[int, dict[str, Any]]] = []
        for raw in lines:
            self._byte_pos += len(raw)
            rec = ledger_record(line_dict, raw, ledger=self._ledger_path, offset=self._line_index)
            if rec is not None:
                read.append((self._line_index, rec))
            self._line_index += 1
        if not read:
            return []
        activity = self._activity()
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
