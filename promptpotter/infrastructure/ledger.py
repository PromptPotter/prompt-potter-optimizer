from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.run_records import (
    FORK_DIRECTION,
    RECORD_ADAPTER,
    CycleMintedRecord,
    CycleRecord,
    ForkDirection,
)
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_cycle_facts
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment

logger = logging.getLogger(__name__)

__all__ = ["CycleEventLog", "continued_chain", "ledger_chain", "open_with_history"]


def _fork_link(cycle_dir: Path) -> CycleMintedRecord | None:
    minted = scan_cycle_facts(CycleLayout(cycle_dir).ledger).minted
    return None if minted is None or minted.parent_cycle_id is None else minted


def _fork_prefix(cycle_dir: CycleDir, *, continued: bool = False) -> list[tuple[Path, int]]:
    prefix: list[tuple[Path, int]] = []
    child_dir = Path(cycle_dir)
    seen = {child_dir.name}
    while (link := _fork_link(child_dir)) is not None:
        parent_id, cut, spec = link.parent_cycle_id, link.forked_at_offset, link.fork
        assert parent_id is not None and cut is not None and spec is not None
        if continued and FORK_DIRECTION[spec.trigger] is ForkDirection.OFFSHOOT:
            break
        if parent_id in seen:
            raise ValueError(
                f"{child_dir}: parent_cycle_id {parent_id!r} is already on this fork chain — "
                "the mints describe a cycle, which no walk can resolve."
            )
        seen.add(parent_id)
        child_dir = child_dir.parent / parent_id
        prefix.append((CycleLayout(child_dir).ledger, cut))
    return prefix[::-1]


def ledger_chain(cycle_dir: CycleDir, moment: Moment | None = None) -> list[LedgerSpan]:
    chain = [
        *(LedgerSpan(path, cut) for path, cut in _fork_prefix(cycle_dir)),
        LedgerSpan(CycleLayout(Path(cycle_dir)).ledger),
    ]
    return chain if moment is None else moment.spans(chain)


def continued_chain(cycle_dir: CycleDir) -> list[LedgerSpan]:
    """Stops at an OFFSHOOT cut: what only a continuation inherits (the δ scale) is read over it."""
    return [
        *(LedgerSpan(path, cut) for path, cut in _fork_prefix(cycle_dir, continued=True)),
        LedgerSpan(CycleLayout(Path(cycle_dir)).ledger),
    ]


def open_with_history(cycle_dir: CycleDir) -> CycleEventLog:
    log = child = CycleEventLog.open(cycle_dir)
    for path, cut in reversed(_fork_prefix(cycle_dir)):
        parent = CycleEventLog(path)
        child.inherit_from(parent, cut)
        child = parent
    return log


class CycleEventLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._subscribers: list[Projection] = []
        self._next_offset = 0
        self._end = 0
        if path.exists():
            with path.open("rb") as fh:
                for line in fh:
                    self._next_offset += 1
                    self._end += len(line)
        self._inherit_parent: CycleEventLog | None = None
        self._inherit_offset: int = 0

    @classmethod
    def open(cls, cycle_dir: CycleDir) -> CycleEventLog:
        ledger_path = CycleLayout(Path(cycle_dir)).ledger
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(ledger_path)

    @staticmethod
    def workspace_path(workspace_dir: WorkspaceDir) -> Path:
        """Never creates it: a read must not mint a `.workspace/` in a tenant that wrote none."""
        return Path(workspace_dir) / ".workspace" / "events.jsonl"

    @classmethod
    def open_workspace(cls, workspace_dir: WorkspaceDir) -> CycleEventLog:
        path = cls.workspace_path(workspace_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: CycleRecord) -> int:
        line = RECORD_ADAPTER.dump_json(record, fallback=str) + b"\n"
        try:
            fh = self._path.open("ab")
        except FileNotFoundError:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fh = self._path.open("ab")
        with fh:
            fh.write(line)
            fh.flush()
            end = fh.tell()
        # The offset is the record's LINE, so what another writer appended since is fanned out first.
        self._catch_up(end - len(line))
        offset = self._next_offset
        self._next_offset, self._end = offset + 1, end
        self._fan_out(record, offset)
        return offset

    def _catch_up(self, upto: int) -> None:
        if upto <= self._end:
            return
        with self._path.open("rb") as fh:
            fh.seek(self._end)
            theirs = fh.read(upto - self._end)
        for raw in theirs.splitlines():
            offset = self._next_offset
            self._next_offset += 1
            # A line no arm reads still CONSUMES its offset, as ``iter`` counts it.
            with suppress(ValidationError, ValueError):
                self._fan_out(RECORD_ADAPTER.validate_json(raw), offset)
        self._end = upto

    def _fan_out(self, record: CycleRecord, offset: int) -> None:
        for sub in self._subscribers:
            try:
                sub.on_record(record, offset)
            except Exception:
                logger.exception(
                    "projection %s failed on offset %d",
                    type(sub).__name__,
                    offset,
                )

    def iter(self, own_limit: int | None = None) -> Iterator[tuple[int, CycleRecord]]:
        """`own_limit` counts OWN records, as `forked_at_offset` does, never a position over the chain."""
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
                    yield offset, RECORD_ADAPTER.validate_json(stripped)
                except (ValidationError, ValueError):
                    logger.warning(
                        "skipping unparseable ledger line at offset %d in %s",
                        offset,
                        self._path,
                    )

    def inherit_from(self, parent: CycleEventLog, offset: int) -> None:
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
