"""Cross-process TAIL of a cycle's on-disk ledger into SSE frames — tailing rather than subscribing is what makes the
stream work whichever process runs the campaign. ``sequence`` is the writer's own 0-based line index."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast, get_args

from promptpotter.domain.projection_envelope import ProjectionEnvelope, ProjectionKind
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["CycleLedgerTail"]

_VALID_KINDS: frozenset[str] = frozenset(get_args(ProjectionKind))


class CycleLedgerTail:
    """Incremental reader over one cycle's ledger, tracking a byte position alongside the line index it stamps as
    ``sequence``. The reads are synchronous file I/O — callers on an event loop use ``asyncio.to_thread``."""

    def __init__(self, cycle_dir: Path, cycle_id: str) -> None:
        self._layout = CycleLayout(cycle_dir)
        self._cycle_id = cycle_id
        self._ledger_path = self._layout.ledger
        self._byte_pos = 0
        self._line_index = 0

    def snapshot_frame(self) -> ProjectionEnvelope:
        """The leading frame: the cycle's dashboard (or a warming shape) plus its offset. It also POSITIONS the tail cursor at
        end-of-file, so the live tail begins exactly where the snapshot left off — no gap, no duplicate."""
        offset = self._seek_to_eof()
        body = self._read_dashboard()
        body["snapshot_at_offset"] = offset
        return ProjectionEnvelope(
            kind="stream_snapshot",
            cycle_id=self._cycle_id,
            sequence=offset,
            payload=body,
        )

    def read_new(self) -> list[ProjectionEnvelope]:
        """Every complete line appended since the last read, one envelope each. A trailing PARTIAL line — a write still in
        flight — is left for the next call."""
        if not self._ledger_path.exists():
            return []
        with self._ledger_path.open("rb") as fh:
            fh.seek(self._byte_pos)
            chunk = fh.read()
        nl = chunk.rfind(b"\n")
        if nl == -1:
            return []  # only a partial line so far
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

    # ---- internals ----

    def _read_dashboard(self) -> dict[str, Any]:
        dashboard = self._layout.dashboard
        try:
            body = read_json_optional(dashboard)
        except json.JSONDecodeError:
            logger.warning("dashboard.json malformed at %s; warming_up snapshot", dashboard)
            return {"warming_up": True, "reason": "dashboard_unreadable"}
        if isinstance(body, dict):
            return body
        return {"warming_up": True}

    def _seek_to_eof(self) -> int:
        """Count complete lines and park the byte cursor after the last one.
        Returns the line count (= ``snapshot_at_offset`` = where the tail begins)."""
        if not self._ledger_path.exists():
            self._byte_pos = 0
            self._line_index = 0
            return 0
        data = self._ledger_path.read_bytes()
        nl = data.rfind(b"\n")
        if nl == -1:
            self._byte_pos = 0
            self._line_index = 0
            return 0
        self._byte_pos = nl + 1
        self._line_index = data[: nl + 1].count(b"\n")
        return self._line_index

    def _to_envelope(self, raw: bytes) -> ProjectionEnvelope | None:
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("skipping malformed ledger line at offset %d", self._line_index)
            return None
        if not isinstance(rec, dict):
            return None
        kind = rec.get("record_type")
        if not isinstance(kind, str) or kind not in _VALID_KINDS:
            return None
        return ProjectionEnvelope(
            kind=cast(ProjectionKind, kind),
            cycle_id=self._cycle_id,
            sequence=self._line_index,
            payload=rec,
        )
