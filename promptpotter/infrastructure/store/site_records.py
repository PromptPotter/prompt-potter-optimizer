"""What the operator's own website asked this box to keep: one append-only file per table.

A row with a key stands in for the row kept under that key before it, AT READ. Nothing is rewritten
but by `drop_before`, which is how a published retention window is kept true.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, NamedTuple

from promptpotter.config.paths import user_data_root
from promptpotter.infrastructure.store.io import append_jsonl, read_text_optional, write_jsonl
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.errors import NotFoundError

_TABLE = re.compile(r"[a-z][a-z0-9_]{0,31}")


class SiteRecord(NamedTuple):
    at: str
    # Empty on a row that is only ever added, such as one page view.
    key: str
    row: dict[str, Any]


# One process serves the API, so one lock orders an append against a rewrite.
_WRITING = threading.Lock()


class SiteRecordStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, table: str) -> Path:
        """The sender names the table, so the name is held to one file directly under the root."""
        root = os.path.normpath(self._root)
        path = os.path.normpath(os.path.join(root, f"{table}.jsonl"))
        if not _TABLE.fullmatch(table) or not path.startswith(root + os.sep):
            raise NotFoundError(f"No table {table!r}")
        return Path(path)

    def keep(self, table: str, record: SiteRecord) -> None:
        with _WRITING:
            append_jsonl(self._path(table), record._asdict())

    def kept(self, table: str) -> list[SiteRecord]:
        latest: dict[str | int, SiteRecord] = {}
        for number, line in enumerate(read_text_optional(self._path(table)).splitlines()):
            try:
                record = SiteRecord(**json.loads(line))
            except (ValueError, TypeError):
                # A line torn by a crash costs itself and no other.
                continue
            latest[record.key or number] = record
        return list(latest.values())

    def drop_before(self, table: str, before: float) -> int:
        """A keyed row is the newest word on its subject and has no age: only added rows go."""
        with _WRITING:
            records = self.kept(table)
            staying = [r for r in records if r.key or (epoch_seconds(r.at) or 0.0) >= before]
            if len(staying) != len(records):
                write_jsonl(self._path(table), (r._asdict() for r in staying))
            return len(records) - len(staying)


def default_site_record_store() -> SiteRecordStore:
    return SiteRecordStore(user_data_root() / "site_records")


__all__ = ["SiteRecord", "SiteRecordStore", "default_site_record_store"]
