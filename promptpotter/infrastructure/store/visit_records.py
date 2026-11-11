"""What reached this app from outside without a session, and the digests told of it
(`application/app_visits.py`): one append-only file per table.

Nothing is rewritten but by `drop_before`, which is how the retention window is kept true.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

from promptpotter.config.paths import user_data_root
from promptpotter.infrastructure.store.io import append_jsonl, rewrite_lines
from promptpotter.infrastructure.store.read_model import parse_rows, read_rows
from promptpotter.shared.clock import epoch_seconds


class VisitRecord(NamedTuple):
    at: str
    row: dict[str, Any]


def visit_record(line: bytes) -> VisitRecord:
    return VisitRecord(**json.loads(line))


class VisitRecordStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, table: str) -> Path:
        return self._root / f"{table}.jsonl"

    def keep(self, table: str, record: VisitRecord) -> None:
        append_jsonl(self._path(table), record._asdict())

    def kept(self, table: str) -> list[VisitRecord]:
        return read_rows(self._path(table), visit_record)

    def drop_before(self, table: str, before: float) -> int:
        """Every row kept before *before* goes, in whichever table."""
        path = self._path(table)

        def dropped(lines: list[str]) -> tuple[Iterator[str] | None, int]:
            records = parse_rows(path, (ln.encode("utf-8") for ln in lines), visit_record)
            staying = [r for r in records if (epoch_seconds(r.at) or 0.0) >= before]
            gone = len(records) - len(staying)
            out = (json.dumps(r._asdict(), ensure_ascii=False) + "\n" for r in staying)
            return (out if gone else None), gone

        return rewrite_lines(path, dropped)


VISIT_RECORDS_DIR = "app_visits"


def default_visit_record_store() -> VisitRecordStore:
    return VisitRecordStore(user_data_root() / VISIT_RECORDS_DIR)
