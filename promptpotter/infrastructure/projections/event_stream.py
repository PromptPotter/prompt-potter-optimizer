"""Cross-process TAIL of a cycle's on-disk ledger into SSE frames — tailing rather than subscribing is what makes the
stream work whichever process runs the campaign. ``sequence`` is the writer's own 0-based line index."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast, get_args

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.projection_envelope import ProjectionEnvelope, ProjectionKind
from promptpotter.infrastructure.projections.live_dashboard.state import warming_payload
from promptpotter.infrastructure.runtime_flags import derive_run_phase, overlay_armed_controls
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["CycleLedgerTail"]

_VALID_KINDS: frozenset[str] = frozenset(get_args(ProjectionKind))


class CycleLedgerTail:
    """Incremental reader over one cycle's ledger, tracking a byte position alongside the line index it stamps as
    ``sequence``. The reads are synchronous file I/O — callers on an event loop use ``asyncio.to_thread``."""

    def __init__(self, cycle_dir: Path, hop: CycleHop) -> None:
        self._layout = CycleLayout(cycle_dir)
        self._hop = hop
        self._cycle_id = hop.cycle_id
        self._ledger_path = self._layout.ledger
        self._byte_pos = 0
        self._line_index = 0

    def snapshot_frame(self) -> ProjectionEnvelope:
        """The leading frame: the cycle's dashboard (or a warming shape) plus where the tail picks up.

        It picks up one PAST the offset the dashboard is a fold OF (``at_offset``), not at
        end-of-file. The dashboard write is debounced, so records can land between the fold and the
        file's mtime; parking at EOF meant the client never received those — the snapshot did not
        carry them and the tail began after them. A body with no ``at_offset`` (a warming shape, or
        a file written before the fold stamped one) still parks at EOF, which is what it did all
        along."""
        body = self._read_dashboard()
        folded = body.get("at_offset")
        offset = (
            self._seek_to_line(folded + 1)
            if isinstance(folded, int) and not isinstance(folded, bool) and folded >= -1
            else self._seek_to_eof()
        )
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
        """The snapshot body, with ``run_phase`` DERIVED and the armed controls RE-READ rather than
        served as stored — the same single authority the dashboard route serves, so the first chat
        frame and the first poll cannot disagree about whether the run is alive or about which
        ceiling and look-ahead depth are in force."""
        dashboard = self._layout.dashboard
        body: Any = None
        reason = ""
        try:
            body = read_json_optional(dashboard)
        except json.JSONDecodeError:
            logger.warning("dashboard.json malformed at %s; warming_up snapshot", dashboard)
            reason = "dashboard_unreadable"
        declared = str(body.get("declared_phase", "")) if isinstance(body, dict) else None
        run_phase = str(derive_run_phase(self._layout.cycle_dir, declared=declared))
        if isinstance(body, dict):
            body["run_phase"] = run_phase
            overlay_armed_controls(body, self._layout.cycle_dir)
            return body
        warming = warming_payload(self._hop, run_phase=run_phase)
        if reason:
            warming["reason"] = reason
        return warming

    def _seek_to_line(self, line: int) -> int:
        """Park the cursor so the next read STARTS at ``line``; returns where it parked, clamped to
        end-of-file. Counts bytes rather than trusting the number: a dashboard can name an offset
        this file no longer has (a rewind, a fork's shorter ledger), and a cursor past the end
        delivers nothing forever."""
        if line <= 0 or not self._ledger_path.exists():
            self._byte_pos = 0
            self._line_index = 0
            return 0
        data = self._ledger_path.read_bytes()
        pos = seen = 0
        while seen < line:
            nl = data.find(b"\n", pos)
            if nl == -1:
                break
            pos, seen = nl + 1, seen + 1
        self._byte_pos = pos
        self._line_index = seen
        return seen

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
