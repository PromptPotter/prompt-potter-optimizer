"""CycleEventLog — the append-only spine of facts about one cycle, at ``.runtime/ledger.jsonl``.
Forks are first-class: ``inherit_from(parent, offset)``, cut recorded as a ``FORK_CUT``."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.run_records import CycleRecord
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["CycleEventLog", "Projection"]


_RECORD_ADAPTER: TypeAdapter[CycleRecord] = TypeAdapter(CycleRecord)


class Projection(Protocol):
    """A subscriber to the record stream. A projection rebuilds its own view deterministically and never
    calls ``append`` — the ledger is single-writer, from the campaign loop."""

    def on_record(self, record: CycleRecord, offset: int) -> None: ...


class CycleEventLog:
    """Append-only ``.runtime/ledger.jsonl`` plus an in-memory subscriber list; the FILE is the truth.
    One per cycle, plus one workspace-scoped variant for commands with no cycle to address."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._subscribers: list[Projection] = []
        # Next append's offset = count of existing lines (0 if the file is absent).
        if path.exists():
            with path.open("rb") as fh:
                self._next_offset = sum(1 for _ in fh)
        else:
            self._next_offset = 0
        self._inherit_parent: CycleEventLog | None = None
        self._inherit_offset: int = 0

    @classmethod
    def open(cls, cycle_dir: CycleDir) -> CycleEventLog:
        ledger_path = CycleLayout(Path(cycle_dir)).ledger
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(ledger_path)

    @staticmethod
    def workspace_path(workspace_dir: WorkspaceDir) -> Path:
        """Where the workspace ledger lives, resolved WITHOUT creating it — so a read (the account
        spend walk, the cross-tenant install report) never mints a ``.workspace/`` in a tenant that
        has yet to write one."""
        return Path(workspace_dir) / ".workspace" / "events.jsonl"

    @classmethod
    def open_workspace(cls, workspace_dir: WorkspaceDir) -> CycleEventLog:
        """Open the workspace ledger at ``{workspace_dir}/.workspace/events.jsonl`` — same single-writer
        discipline, no ``inherit_from`` (the workspace has no fork tree)."""
        path = cls.workspace_path(workspace_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: CycleRecord) -> int:
        """Write one record, fan out to subscribers, return its offset. ``fallback=str`` lets a payload carry
        submodels / dataclasses / enums: they stringify on disk, subscribers still see the original."""
        offset = self._next_offset
        line = _RECORD_ADAPTER.dump_json(record, fallback=str).decode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
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
        """Yield records from ``since``, walking a fork's parent prefix first. **Two offset spaces exist and
        MUST NOT be mixed** — ``since`` is virtual (parent + own); a tail's ``sequence`` is this file's line."""
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
                    try:
                        yield _RECORD_ADAPTER.validate_json(line)
                    except (ValidationError, ValueError):
                        # A torn final line (append is not crash-atomic, ll. 87-88)
                        # or a version-skewed record. Skip-and-continue the way the
                        # sibling readers do (ledger_scan.py, event_stream.py) —
                        # the ledger is the SoT, so one bad line must not abort the
                        # whole read and blind every projection rebuild / fork lookup.
                        logger.warning(
                            "skipping unparseable ledger line at offset %d in %s",
                            produced,
                            self._path,
                        )
                produced += 1

    def inherit_from(self, parent: CycleEventLog, offset: int) -> None:
        """Mark as a fork of *parent*; idempotent with the same args. Subscribers see only own appends —
        the parent already broadcast its own when they happened."""
        if self._inherit_parent is parent and self._inherit_offset == offset:
            return
        if self._inherit_parent is not None:
            raise ValueError("CycleEventLog.inherit_from: already inheriting; cannot rebind")
        if offset < 0:
            raise ValueError(f"CycleEventLog.inherit_from: offset must be >= 0, got {offset}")
        self._inherit_parent = parent
        self._inherit_offset = offset

    def bind(self, projection: Projection) -> None:
        self._subscribers.append(projection)

    @property
    def next_offset(self) -> int:
        return self._next_offset
