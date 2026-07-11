"""CycleEventLog — append-only spine for facts about a campaign cycle.

Every fact is appended as a typed ``CycleRecord`` to one
``.runtime/ledger.jsonl`` per cycle. Projections (live dashboard, audit trail, archive, langfuse)
subscribe and rebuild views deterministically — they never write on
their own initiative.

Forks are first-class via ``inherit_from(parent, offset)``: the fork's
``iter()`` walks parent records up to ``offset`` then its own appends;
the parent records the cut as ``ResumeCheckpointRecord(kind=FORK_CUT)``."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from promptpotter.domain.cycle_paths import CycleDir, Projection, WorkspaceDir
from promptpotter.domain.run_records import CycleRecord
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["CycleEventLog"]


_RECORD_ADAPTER: TypeAdapter[CycleRecord] = TypeAdapter(CycleRecord)


class CycleEventLog:
    """Append-only ``.runtime/ledger.jsonl`` plus an in-memory subscriber list.

    One ledger per cycle. The on-disk file is the source of truth;
    subscribers are a runtime convenience for live projections.
    Forks via ``inherit_from(parent, offset)``.

    A workspace-scoped variant (per ``docs/architecture.md`` §0
    Persistence) lives at ``{workspace_dir}/.workspace/events.jsonl`` and
    is opened via :meth:`open_workspace`. Workspace ledgers carry
    workspace-scoped command audit (`register-backend`,
    `sync-backend-experiments`); per-cycle ledgers remain canonical for
    cycle-targeted records."""

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
        """Open (creating if absent) the ledger at ``{cycle_dir}/.runtime/ledger.jsonl``."""
        ledger_path = CycleLayout(Path(cycle_dir)).ledger
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(ledger_path)

    @classmethod
    def open_workspace(cls, workspace_dir: WorkspaceDir) -> CycleEventLog:
        """Open the workspace-scoped ledger at ``{workspace_dir}/.workspace/events.jsonl``.

        Same shape as the per-cycle ledger, same single-writer discipline;
        ``inherit_from`` is not used at this scope (the workspace has no fork
        tree). Per ``docs/architecture.md`` §0 the workspace ledger is the
        target for commands without any cycle to address."""
        workspace_subdir = Path(workspace_dir) / ".workspace"
        workspace_subdir.mkdir(parents=True, exist_ok=True)
        return cls(workspace_subdir / "events.jsonl")

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: CycleRecord) -> int:
        """Write one record, fan out to subscribers, return its offset.
        ``fallback=str`` lets payload dicts carry Pydantic submodels / dataclasses / enums;
        non-JSON values stringify on disk, subscribers see the original record in memory."""
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
        """Yield records from disk starting at offset ``since``.
        Inherited ledger: walks parent up to the inherit offset first, then own appends;
        ``since`` indexes the combined space (offset 0 = parent's first record).
        Walks the file every call — projections that need streaming should ``bind`` instead."""
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
                        # sibling readers do (ledger_scan.py, event_stream/tail.py) —
                        # the ledger is the SoT, so one bad line must not abort the
                        # whole read and blind every projection rebuild / fork lookup.
                        logger.warning(
                            "skipping unparseable ledger line at offset %d in %s",
                            produced,
                            self._path,
                        )
                produced += 1

    def inherit_from(self, parent: CycleEventLog, offset: int) -> None:
        """Mark as a fork of *parent*; ``iter()`` walks parent's first ``offset``
        records before own appends. Subscribers see only own appends (parent
        already broadcast when those happened). Idempotent with same args."""
        if self._inherit_parent is parent and self._inherit_offset == offset:
            return
        if self._inherit_parent is not None:
            raise ValueError("CycleEventLog.inherit_from: already inheriting; cannot rebind")
        if offset < 0:
            raise ValueError(f"CycleEventLog.inherit_from: offset must be >= 0, got {offset}")
        self._inherit_parent = parent
        self._inherit_offset = offset

    def bind(self, projection: Projection) -> None:
        """Subscribe a projection to subsequent appends."""
        self._subscribers.append(projection)

    @property
    def next_offset(self) -> int:
        """Offset that the next ``append`` will receive."""
        return self._next_offset
