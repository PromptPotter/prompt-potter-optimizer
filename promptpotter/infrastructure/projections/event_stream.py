from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from promptpotter.domain.activity import ActivityFeed
from promptpotter.domain.projection_envelope import (
    RECORD_KINDS,
    ProjectionEnvelope,
    ProjectionKind,
    bearing_record,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    feed_fold,
    ledger_record,
    line_dict,
)


class CycleLedgerTail:
    """Synchronous file I/O: a caller on an event loop uses ``asyncio.to_thread``."""

    def __init__(self, cycle_dir: Path, cycle_id: str) -> None:
        self._cycle_id = cycle_id
        self._ledger_path = CycleLayout(cycle_dir).ledger
        self._byte_pos = 0
        self._line_index = 0
        self._feed = ActivityFeed()

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
            activity=self._feed.state(),
        )

    def read_new(self) -> list[ProjectionEnvelope]:
        """A trailing PARTIAL line, a write still in flight, is left for the next call."""
        if not self._ledger_path.exists():
            return []
        with self._ledger_path.open("rb") as fh:
            fh.seek(self._byte_pos)
            chunk = fh.read()
        nl = chunk.rfind(b"\n")
        if nl == -1:
            return []
        self._byte_pos += nl + 1
        out: list[ProjectionEnvelope] = []
        for raw in chunk[: nl + 1].split(b"\n"):
            if not raw.strip():
                continue
            envelope = self._to_envelope(raw)
            self._line_index += 1
            if envelope is not None:
                out.append(envelope)
        return out

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
        self._feed = LedgerIndex.of(self._ledger_path, ()).view(ActivityFeed, self._line_index)
        return self._line_index

    def _read(self, raw: bytes) -> tuple[ProjectionKind, dict[str, Any]] | None:
        offset = self._feed.at_offset = self._line_index
        rec = ledger_record(line_dict, raw, ledger=self._ledger_path, offset=offset)
        if rec is None:
            return None
        kind = rec.get("record_type")
        if not isinstance(kind, str) or kind not in RECORD_KINDS:
            return None
        if (record := bearing_record(kind, rec)) is not None:
            feed_fold(self._feed, record, ledger=self._ledger_path, offset=offset)
        return cast(ProjectionKind, kind), rec

    def _to_envelope(self, raw: bytes) -> ProjectionEnvelope | None:
        if (read := self._read(raw)) is None:
            return None
        kind, rec = read
        return ProjectionEnvelope(
            kind=kind,
            cycle_id=self._cycle_id,
            sequence=self._line_index,
            payload=rec,
            activity=self._feed.state(),
        )
