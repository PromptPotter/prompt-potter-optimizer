"""CycleEventLog — the append-only spine of facts about one cycle, at ``.runtime/ledger.jsonl``.
Forks are first-class: ``inherit_from(parent, offset)``, cut recorded as a ``FORK_CUT``."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.run_records import CycleRecord
from promptpotter.infrastructure.store.layout import CycleLayout

logger = logging.getLogger(__name__)

__all__ = ["CycleEventLog", "Projection", "branch_offset", "open_with_history"]


def _fork_link(cycle_dir: CycleDir) -> tuple[str, int] | None:
    """This cycle's branch point as ``(parent_cycle_id, offset)``, or ``None`` for a root.

    One manifest read for both halves — they are one fact, and the two readers below wanted
    different sides of it."""
    index = CycleLayout(Path(cycle_dir)).manifest
    if not index.is_file():
        return None
    data = json.loads(index.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    parent = data.get("parent_cycle_id")
    if not isinstance(parent, str) or not parent:
        return None
    offset = data.get("forked_at_offset")
    if not isinstance(offset, int) or isinstance(offset, bool):
        raise ValueError(
            f"{index}: a fork carries no `forked_at_offset`, so where its history begins on "
            "its parent's ledger is unknown and cannot be guessed. Re-mint the fork, or drop "
            "the cycle — campaign state is disposable and the caches it reads are not."
        )
    return parent, offset


def branch_offset(cycle_dir: CycleDir) -> int | None:
    """Where this cycle's history begins on its PARENT's ledger — ``index.json::forked_at_offset``,
    stamped at the cut by ``campaign_store``. ``None`` for a root, which inherits nothing.

    The one copy of a number that used to be derived twice and stored never, and the reason a
    fork's history could not be walked from disk: ``forked_from_round`` is a round and
    ``forked_at`` a wall clock, so neither addresses the ray. A FORK whose manifest predates the
    stamp raises rather than defaulting — inheriting ``0`` would silently serve a fork as though
    it began from nothing, which reads as a real (and much shorter) history."""
    link = _fork_link(cycle_dir)
    return None if link is None else link[1]


def open_with_history(cycle_dir: CycleDir) -> CycleEventLog:
    """This cycle's ledger with its fork chain already bound, so ``iter()`` off disk walks the
    same prefix a live run walks in process.

    The binding existed only inside the forking process until the cut was stamped, so a reader
    that had not run the fork saw a history beginning at the branch — real, and much shorter than
    the truth. Cycles are FLAT under one ``cycles/`` (``store/layout.py``), so a parent resolves
    as a sibling directory and no store is needed to find it. A parent naming a cycle already on
    the chain is refused rather than followed: a cyclic manifest is corrupt data, and walking it
    would hang the reader that asked an ordinary question."""
    log = CycleEventLog.open(cycle_dir)
    seen = {Path(cycle_dir).name}
    child_dir, child_log = Path(cycle_dir), log
    while (link := _fork_link(CycleDir(child_dir))) is not None:
        parent_id, cut = link
        if parent_id in seen:
            raise ValueError(
                f"{child_dir}: parent_cycle_id {parent_id!r} is already on this fork chain — "
                "the manifests describe a cycle, which no walk can resolve."
            )
        seen.add(parent_id)
        parent_dir = child_dir.parent / parent_id
        parent_log = CycleEventLog.open(CycleDir(parent_dir))
        child_log.inherit_from(parent_log, cut)
        child_dir, child_log = parent_dir, parent_log
    return log


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

    def iter(self, own_limit: int | None = None) -> Iterator[tuple[int, CycleRecord]]:
        """The whole chain — a fork's parent prefix, then this ledger's own records — as
        ``(offset, record)``, cut after ``own_limit`` of THIS ledger's own records.

        The offset is the record's PHYSICAL line index in the file it lives in, the same space as
        ``ProjectionEnvelope.sequence``, ``RayItem.offset`` and ``DerivedView.at_offset``. It was
        dropped, so the one caller that wanted an address recovered it with ``enumerate`` and got a
        VIRTUAL position instead — a number naming no line of any file, reported to clients as the
        offset a record was appended at.

        The cut is on OWN records because that is what ``forked_at_offset`` counts
        (``campaign_store::_branch_offset`` reads the parent's ``next_offset``), and because that
        is the address a caller holds: ``at_offset``, a ray item's, an SSE ``sequence``. The
        parent bound was applied to the parent's whole CHAIN before, so a fork OF A FORK took the
        first N records of its grandparent and dropped the parent entirely."""
        if self._inherit_parent is not None:
            yield from self._inherit_parent.iter(self._inherit_offset)
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as fh:
            for offset, line in enumerate(fh):
                if own_limit is not None and offset >= own_limit:
                    return
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    yield offset, _RECORD_ADAPTER.validate_json(stripped)
                except (ValidationError, ValueError):
                    # A torn final line (append is not crash-atomic) or a version-skewed
                    # record. Skip-and-continue the way the sibling readers do
                    # (ledger_scan.py, event_stream.py) — the ledger is the SoT, so one bad
                    # line must not abort the whole read and blind every projection rebuild
                    # / fork lookup. The offset is still CONSUMED, exactly as `append`
                    # assigned it, so a skipped line never shifts its successors' addresses.
                    logger.warning(
                        "skipping unparseable ledger line at offset %d in %s",
                        offset,
                        self._path,
                    )

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
