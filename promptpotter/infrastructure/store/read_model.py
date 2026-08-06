"""Append-only JSONL read model — a save is one ``O_APPEND`` write, a read one last-wins fold. These
are the ONLY primitives for derived-index persistence; a second mechanism doing this job is a bug."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from filelock import FileLock

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.infrastructure.store.io import append_jsonl, ensure_parent_dir, write_jsonl


def iter_jsonl(path: Path, *, record_types: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """Every JSON object in *path*, in file order; a blank, corrupt or half-written trailing line degrades
    to "not there". *record_types* screens a line WITHOUT parsing it — a substring probe never misses."""
    probes = tuple(f'"{t}"' for t in record_types) if record_types else ()
    rows: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                # Probe the raw line BEFORE stripping or parsing: on a ledger the skipped
                # lines are the overwhelming majority, so anything spent per line before the
                # test is spent on rows nobody wants.
                if probes:
                    for probe in probes:
                        if probe in raw:
                            break
                    else:
                        continue
                line = raw.strip()
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
    """Fold *path* into ``{row[key]: row}``, last line wins, first-seen order preserved."""
    out: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        k = row.get(key)
        if isinstance(k, str):
            out[k] = row
    return out


def fold_jsonl_from(path: Path, key: str, offset: int) -> tuple[dict[str, dict[str, Any]], int]:
    """Fold only the bytes from *offset* on, returning the next offset. Reading stops at the last COMPLETE
    line, so the offset never lands mid-record and a crash-truncated tail is re-read, never dropped."""
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
    """Append one upsert row — one ``O_APPEND`` write, no read, no rewrite. Held under the log's lock only
    to serialise against a concurrent :func:`compact`, which truncates and replaces."""
    with _lock_for(path):
        append_jsonl(path, row)


def compact(path: Path, key: str, *, factor: int = 2) -> bool:
    """Rewrite *path* keeping only the live row per *key*, once it has grown past *factor*× the live set;
    no-op when absent or already tight. Under the lock, so a concurrent append cannot be lost."""
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
