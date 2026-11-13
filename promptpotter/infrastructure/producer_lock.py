"""An OS lock, never an ``O_EXCL`` marker: the kernel drops it whenever the holder dies."""

from __future__ import annotations

import threading
from pathlib import Path

from promptpotter.infrastructure.store.io import HeldLock, probe_lock, take_lock
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import ConflictError

# How long a taker rides out a hold of an instant: a POSIX probe's shared lock, a stop declared.
_PROBE_GRACE_S = 1.0

_guard = threading.Lock()
_mine: dict[Path, HeldLock] = {}


def take(path: Path) -> bool:
    key = path.resolve()
    with _guard:
        if key in _mine:
            return False
    lock = take_lock(path, wait_s=_PROBE_GRACE_S)
    if lock is None:
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
    with probe_lock(path) as producer:
        return producer


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


def hold_pass(cycle_dir: Path) -> None:
    """A scoring pass is a producer too: it holds the cycle against a launch, a delete and a rewind."""
    hold_cycle(cycle_dir)
    if not take(CycleLayout(cycle_dir).pass_lock):
        release_cycle(cycle_dir)
        raise ConflictError(
            f"cycle {cycle_dir.name} has a scoring pass in flight. Pause it or let it end.",
            code="producer_live",
        )


def release_pass(cycle_dir: Path) -> None:
    release(CycleLayout(cycle_dir).pass_lock)
    release_cycle(cycle_dir)


def pass_held(cycle_dir: Path) -> bool:
    return held(CycleLayout(cycle_dir).pass_lock)
