"""Keeps the paid caches and drops the account; not the ``reset`` verb, which does the opposite."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from promptpotter.infrastructure.store.io import rmtree_robust, unlink_robust
from promptpotter.infrastructure.store.layout import SHARED_CACHE_DIRS


def prune(directory: Path, keep: frozenset[str]) -> bool:
    survived = False
    for entry in sorted(directory.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            if entry.name in keep:
                survived = True
                continue
            if prune(entry, keep):
                survived = True
            else:
                rmtree_robust(entry)
            continue
        unlink_robust(entry)
    return survived


def _kept_summary(home: Path, keep: frozenset[str]) -> list[str]:
    lines: list[str] = []
    for path in sorted(p for p in home.rglob("*") if p.is_dir() and p.name in keep):
        n = sum(1 for f in path.rglob("*") if f.is_file())
        lines.append(f"[e2e] kept {path.relative_to(home)} — {n:,} entries")
    return lines


def main(argv: list[str]) -> int:
    if not argv:
        print("[e2e] usage: reset_world.py <home> [--drop-caches]", file=sys.stderr)
        return 2
    home = Path(argv[0]).resolve()
    drop_caches = "--drop-caches" in argv[1:]

    # The guard is the PATH, never a flag: `PP_E2E_COLD_HOME` may name a real workspace.
    tmp = Path(tempfile.gettempdir()).resolve()
    if home == tmp or not home.is_relative_to(tmp):
        print(
            f"[e2e] REFUSING to reset {home} — a workspace this harness wipes must live under "
            f"{tmp}. Point PP_E2E_COLD_HOME somewhere disposable, or pass PP_E2E_KEEP=1.",
            file=sys.stderr,
        )
        return 2

    if not home.is_dir():
        print(f"[e2e] {home} does not exist yet — nothing to reset")
        return 0

    if drop_caches:
        rmtree_robust(home)
        print(f"[e2e] dropped {home} WHOLE, paid caches included — the next run re-records")
        return 0

    keep = frozenset(SHARED_CACHE_DIRS)
    print(f"[e2e] resetting {home}, keeping {', '.join(sorted(keep))}")
    prune(home, keep)
    for line in _kept_summary(home, keep) or ["[e2e] no cache survived — this run records one"]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
