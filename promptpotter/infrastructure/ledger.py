from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from promptpotter.domain.cycle_paths import CycleDir, WorkspaceDir
from promptpotter.domain.run_records import (
    RECORD_ADAPTER,
    CycleMintedRecord,
    CycleRecord,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_cycle_facts
from promptpotter.infrastructure.store.io import append_line, complete_lines
from promptpotter.infrastructure.store.layout import WORKSPACE_STATE_DIR, CycleLayout
from promptpotter.infrastructure.store.read_model import (
    LedgerIndex,
    LedgerSpan,
    Moment,
    ledger_record,
)


def _fork_link(cycle_dir: Path) -> tuple[CycleMintedRecord, Path] | None:
    minted = scan_cycle_facts(CycleLayout(cycle_dir).ledger).minted
    parent = None if minted is None else minted.parent_cycle_dir(cycle_dir)
    return None if minted is None or parent is None else (minted, parent)


def _fork_prefix(cycle_dir: CycleDir) -> list[tuple[Path, int]]:
    prefix: list[tuple[Path, int]] = []
    child_dir = Path(cycle_dir)
    # By dir, never id: two trees can hold the same cycle id. A mint names its parent off the
    # child's own path, so the spelling alone is compared, with no filesystem call per hop.
    seen = {os.path.normcase(os.path.abspath(child_dir))}
    while (link := _fork_link(child_dir)) is not None:
        minted, parent_dir = link
        cut = minted.forked_at_offset
        assert cut is not None
        if (spelled := os.path.normcase(os.path.abspath(parent_dir))) in seen:
            raise ValueError(
                f"{child_dir}: parent {parent_dir} is already on this fork chain — "
                "the mints describe a cycle, which no walk can resolve."
            )
        seen.add(spelled)
        child_dir = parent_dir
        prefix.append((CycleLayout(child_dir).ledger, cut))
    return prefix[::-1]


def ledger_chain(cycle_dir: CycleDir, *, moment: Moment | None) -> list[LedgerSpan]:
    chain = [
        *(LedgerSpan(path, cut) for path, cut in _fork_prefix(cycle_dir)),
        LedgerSpan(CycleLayout(Path(cycle_dir)).ledger),
    ]
    return chain if moment is None else moment.spans(chain)


class CycleEventLog:
    """A ledger's appender: its offsets and its projections are its `LedgerIndex`'s."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def open(cls, cycle_dir: CycleDir) -> CycleEventLog:
        ledger_path = CycleLayout(Path(cycle_dir)).ledger
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        return cls(ledger_path)

    @staticmethod
    def workspace_path(workspace_dir: WorkspaceDir) -> Path:
        """Never creates it: a read must not mint a `.workspace/` in a tenant that wrote none."""
        return Path(workspace_dir) / WORKSPACE_STATE_DIR / "events.jsonl"

    @classmethod
    def open_workspace(cls, workspace_dir: WorkspaceDir) -> CycleEventLog:
        path = cls.workspace_path(workspace_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: CycleRecord) -> int:
        # A scored cell's five appends are five moments, never one record: the hold before its send
        # (decisions.md § 0003), a bill per send, the start as the live marker, the priced key, the score.
        raw = RECORD_ADAPTER.dump_json(record, fallback=str)
        # The offset is the record's LINE, as the index counts it, another writer's lines included.
        return LedgerIndex.of(self._path, ()).land(raw, append_line(self._path, raw), record)

    def iter(self) -> Iterator[tuple[int, CycleRecord]]:
        """This file's own records, each at its line: a fork's prefix is `ledger_chain`'s."""
        if not self._path.exists():
            return
        with self._path.open("rb") as fh:
            for offset, line in enumerate(complete_lines(fh)):
                record = ledger_record(
                    RECORD_ADAPTER.validate_json, line, ledger=self._path, offset=offset
                )
                if record is not None:
                    yield offset, record

    @property
    def next_offset(self) -> int:
        """Complete lines alone, as every reader counts them: a torn tail is no offset, and the next
        append cuts it away before it lands (`io.py::append_line`)."""
        return LedgerIndex.of(self._path, ()).line_count()
