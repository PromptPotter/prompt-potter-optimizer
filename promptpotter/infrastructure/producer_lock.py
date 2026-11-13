"""An OS lock, never an ``O_EXCL`` marker: the kernel drops it whenever the holder dies; and the
one producer token a process holds per jobs dir, which its jobs name and its containers carry."""

from __future__ import annotations

import atexit
import contextlib
import os
import secrets
import threading
from pathlib import Path

from promptpotter.config.paths import PRODUCERS_DIR
from promptpotter.infrastructure.store.io import (
    HeldLock,
    probe_lock,
    take_lock,
    validate_path_component,
)
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import ConflictError

# How long a taker rides out a hold of an instant: a POSIX probe's shared lock, a stop declared.
_PROBE_GRACE_S = 1.0

_guard = threading.Lock()
_mine: dict[Path, HeldLock] = {}
_tokens: dict[Path, str] = {}
_minting = threading.Lock()


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


def producer_path(jobs_dir: Path, producer_id: str) -> Path:
    validate_path_component(producer_id)
    return jobs_dir / PRODUCERS_DIR / f"{producer_id}.lock"


def this_producer(jobs_dir: Path) -> str:
    """This process's token for *jobs_dir*'s home, minted on first ask and held for its life: what
    admits a job (`Job.producer_id`) and labels a container (`docker_host.py::claim_machine`)."""
    key = jobs_dir.resolve()
    with _minting:
        if (producer_id := minted_producer(jobs_dir)) is not None:
            return producer_id
        producer_id = f"{os.getpid()}-{secrets.token_hex(4)}"
        path = producer_path(jobs_dir, producer_id)
        if not take(path):
            raise RuntimeError(f"producer file {path} is held before this process minted it")
        # A clean exit retires its own file: its jobs are terminal, so no probe would reclaim it.
        atexit.register(_retire, path)
        with _guard:
            _tokens[key] = producer_id
        return producer_id


def minted_producer(jobs_dir: Path) -> str | None:
    """This process's token if it ever minted one here; reading mints nothing."""
    with _guard:
        return _tokens.get(jobs_dir.resolve())


def other_producers(jobs_dir: Path) -> list[Path]:
    """Every token in *jobs_dir*'s home but this process's own: each a process that may write."""
    own = minted_producer(jobs_dir)
    tokens = sorted((jobs_dir / PRODUCERS_DIR).glob("*.lock"))
    return [path for path in tokens if path.stem != own]


def reclaim_producer(path: Path) -> None:
    """A dead producer's file, unlinked once nothing holds it."""
    if not held(path):
        with contextlib.suppress(OSError):
            path.unlink()


def _retire(path: Path) -> None:
    release(path)
    with contextlib.suppress(OSError):
        path.unlink()
