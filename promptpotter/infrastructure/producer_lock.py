"""An OS lock, never an ``O_EXCL`` marker: the kernel drops it whenever the holder dies."""

from __future__ import annotations

import threading
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout

from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import ConflictError

# How long a taker rides out a reader's probe, which holds the lock for the instant it asks.
_PROBE_GRACE_S = 1.0

_guard = threading.Lock()
_mine: dict[Path, BaseFileLock] = {}


def take(path: Path) -> bool:
    key = path.resolve()
    with _guard:
        if key in _mine:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Released by whichever thread ends the run, so the lock is the process's and no one thread's.
    lock = FileLock(str(path), thread_local=False)
    try:
        lock.acquire(timeout=_PROBE_GRACE_S)
    except Timeout:
        return False
    with _guard:
        if key in _mine:
            lock.release()
            return False
        _mine[key] = lock
    return True


def release(path: Path) -> None:
    with _guard:
        lock = _mine.pop(path.resolve(), None)
    if lock is not None:
        lock.release()


def held(path: Path) -> bool:
    with _guard:
        if path.resolve() in _mine:
            return True
    # Asking must not create the lock file.
    if not path.is_file():
        return False
    probe = FileLock(str(path), thread_local=False)
    try:
        probe.acquire(timeout=0)
    except Timeout:
        return True
    probe.release()
    return False


def hold_cycle(cycle_dir: Path) -> None:
    if not take(CycleLayout(cycle_dir).producer_lock):
        raise ConflictError(
            f"cycle {cycle_dir.name} has a run in flight. Pause it or let it end, then ask again.",
            code="producer_live",
        )


def release_cycle(cycle_dir: Path) -> None:
    release(CycleLayout(cycle_dir).producer_lock)


def cycle_held(cycle_dir: Path) -> bool:
    return held(CycleLayout(cycle_dir).producer_lock)


__all__ = ["cycle_held", "held", "hold_cycle", "release", "release_cycle", "take"]
