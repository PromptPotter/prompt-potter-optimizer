"""Append-only JSONL read model — the stdlib primitives that retire the
read-whole / O(n)-scan / rewrite-whole persistence pattern.

A writer appends one line (`append_row`); a reader folds the file into a
last-wins `dict` keyed on one field (`fold_jsonl`); the log is rewritten without
superseded lines only when it has grown past a factor of the live set
(`compact`). A save is one `O_APPEND` write — no read, no rewrite. Readers do a
single fold instead of hand-rolling upsert semantics over a whole-file scan.

These are the ONLY primitives for derived-index persistence. A second mechanism
doing the same job is a bug — fold it into these.

A reader that folds the same log many times per round tails it instead
(`fold_jsonl_from`): re-fold only the bytes appended since the last read, keyed
off the file's `(mtime_ns, size)`. Two `MeasurementArchive` instances share one
file — an L4 inner cycle runs in-process with its own `Stores` over the same
`measurements/` dir — so a memo that trusts its own writes alone would go blind
to the sibling's appends. The stat check is what makes the memo safe.
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


def fold_jsonl_from(path: Path, key: str, offset: int) -> tuple[dict[str, dict[str, Any]], int]:
    """Fold only the bytes of *path* from *offset* on: ``({row[key]: row}, new_offset)``.

    The caller merges the returned rows onto its memo (last-wins, same semantics as
    :func:`fold_jsonl`) and keeps *new_offset* for the next tail. Reading stops at the
    last complete line, so *new_offset* never lands mid-record and a crash-truncated
    trailing line is simply re-read next tick rather than silently dropped — the same
    tolerance :func:`iter_jsonl` gives a whole-file fold.

    A newline cannot occur inside a multi-byte UTF-8 sequence, so cutting the byte
    buffer at the last ``\\n`` is always a safe decode boundary.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            buf = fh.read()
    except FileNotFoundError:
        return out, offset
    cut = buf.rfind(b"\n")
    if cut < 0:
        return out, offset
    for line in buf[: cut + 1].decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        k = row.get(key)
        if isinstance(row, dict) and isinstance(k, str):
            out[k] = row
    return out, offset + cut + 1


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


__all__ = ["append_row", "compact", "fold_jsonl", "fold_jsonl_from", "iter_jsonl"]
