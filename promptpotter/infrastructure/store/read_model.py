"""Append-only JSONL read model — the stdlib primitives that retire the
read-whole / O(n)-scan / rewrite-whole persistence pattern.

A writer appends one line (`append_row`); a reader folds the file into a
last-wins `dict` keyed on one field (`fold_jsonl`); the log is rewritten without
superseded lines only when it has grown past a factor of the live set
(`compact`). A save is one `O_APPEND` write — no read, no rewrite. Readers do a
single fold instead of hand-rolling upsert semantics over a whole-file scan.

These are the ONLY primitives for derived-index persistence. A second mechanism
doing the same job is a bug — fold it into these. (The registry read model in
Arc 4 layers a generation-counter memo on top of the same three functions.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from filelock import FileLock

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.infrastructure.store.io import append_jsonl, ensure_parent_dir, write_jsonl


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON object in *path*, in file order; missing file → ``[]``.

    Blank and corrupt lines and non-object rows are skipped — a half-written
    trailing line (crash mid-append) degrades to "not there", never an error.
    """
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        return []
    return rows


def fold_jsonl(path: Path, key: str) -> dict[str, dict[str, Any]]:
    """Fold *path* into ``{row[key]: row}``, last line wins (the upsert semantics
    the old O(n) scan hand-rolled). First-seen order is preserved; a later line
    updates the value in place. Rows lacking a string *key* are skipped."""
    out: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        k = row.get(key)
        if isinstance(k, str):
            out[k] = row
    return out


def _lock_for(path: Path) -> FileLock:
    """The append/compact interlock for one log: ``<path>.lock``, parent ensured."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    ensure_parent_dir(lock_path)
    return FileLock(str(lock_path), timeout=LOCK_TIMEOUT)


def append_row(path: Path, row: dict[str, Any]) -> None:
    """Append one upsert *row* (last-wins by the reader's key). One `O_APPEND`
    write — no read, no rewrite. Held under the log's lock only to serialise
    against a concurrent :func:`compact` (which truncates-and-replaces)."""
    with _lock_for(path):
        append_jsonl(path, row)


def compact(path: Path, key: str, *, factor: int = 2) -> bool:
    """Rewrite *path* keeping only the live (last-wins) row per *key*, in
    first-seen order, when it has grown past *factor*× the live set. Returns
    whether it rewrote. No-op when the file is absent or already tight. Held
    under the log's lock so a concurrent :func:`append_row` can't be lost."""
    with _lock_for(path):
        rows = iter_jsonl(path)
        live: dict[str, dict[str, Any]] = {}
        for row in rows:
            k = row.get(key)
            if isinstance(k, str):
                live[k] = row
        if len(rows) <= factor * len(live):
            return False
        write_jsonl(path, live.values())
        return True


__all__ = ["append_row", "compact", "fold_jsonl", "iter_jsonl"]
