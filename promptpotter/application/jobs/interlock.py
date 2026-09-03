"""Two cross-process facts about the machine-global jobs dir, both carried by OS file locks: who
may admit right now, and whether the process behind a job is still alive.

**An OS lock is the whole reason this is durable.** The kernel drops it when the holder exits —
crash, ``SIGKILL``, power cut alike — so neither fact needs a heartbeat to stay fresh nor a
staleness window during which it is simply wrong. A hand-rolled ``O_EXCL`` marker would need both,
and the window is exactly where the bug lives."""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import threading
from pathlib import Path

from filelock import BaseFileLock, FileLock, Timeout

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.infrastructure.store.io import validate_path_component

logger = logging.getLogger(__name__)

# Both live INSIDE the jobs dir, which is globbed for `*.json` — so neither is ever mistaken for a
# job, and a shared dir carries its own interlocks rather than needing a second agreed location.
_ADMISSION_LOCK = ".admission.lock"
_PRODUCERS_DIR = "producers"

# jobs dir -> (producer id, the lock proving this process is behind it). The lock is held for the
# life of the process and never released; the reference lives here so nothing collects it early.
_token_lock = threading.Lock()
_tokens: dict[Path, tuple[str, BaseFileLock]] = {}


def admission_lock(jobs_dir: Path) -> BaseFileLock:
    """The machine-wide mutex over an admission — count the live jobs, resolve capacity, write the
    reservation — held by whichever process is deciding.

    Within one process a ``threading.RLock`` closes that window; this closes it across processes,
    which the shared jobs dir makes reachable — a terminal run holds a slot beside the server's.
    Without it both readers see the same free slot and both take it, and the loser over-subscribes
    the box by one.

    ONE instance per registry, so the nesting is reentrant: ``filelock`` counts acquisitions per
    object and per thread."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(str(jobs_dir / _ADMISSION_LOCK), timeout=LOCK_TIMEOUT)


def this_producer(jobs_dir: Path) -> str:
    """The id every job this process admits is stamped with, minted once and held for the process's
    life. Callers ask for it at the moment they write a job, never at construction — a registry that
    only reads occupancy has no producer to be."""
    key = jobs_dir.resolve()
    with _token_lock:
        held = _tokens.get(key)
        if held is not None:
            return held[0]
        producer_id = f"{os.getpid()}-{secrets.token_hex(4)}"
        path = _producer_path(jobs_dir, producer_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(path), timeout=0)
        # A fresh unique path, so this cannot block; deliberately never released.
        lock.acquire()
        _tokens[key] = (producer_id, lock)
        logger.debug("producer %s holds %s", producer_id, path)
        return producer_id


def producer_alive(jobs_dir: Path, producer_id: str) -> bool:
    """Is the process that admitted this job still running?

    The cross-process half of liveness — its in-process twin is the job's ``asyncio.Task``, which
    answers only for jobs this process started. A job whose producer is gone holds a machine slot
    nothing else will release. Inferring it from how long a CYCLE has been quiet answers neither
    end of this: the staleness window is wrong in both directions inside it, and a reservation or a
    queue entry has no cycle to be quiet.

    An unstamped id reads as gone: no live process claims it, which is the same fact.

    Answering RECLAIMS a dead producer's file, or the dir keeps one per process that ever ran
    here."""
    if not producer_id:
        return False
    with _token_lock:
        held = _tokens.get(jobs_dir.resolve())
    if held is not None and held[0] == producer_id:
        return True
    try:
        path = _producer_path(jobs_dir, producer_id)
    except ValueError:
        return False
    if not path.is_file():
        return False
    probe = FileLock(str(path), timeout=0)
    try:
        probe.acquire()
    except Timeout:
        return True
    with contextlib.suppress(OSError):
        path.unlink()
    probe.release()
    return False


def _producer_path(jobs_dir: Path, producer_id: str) -> Path:
    validate_path_component(producer_id)
    return jobs_dir / _PRODUCERS_DIR / f"{producer_id}.lock"


__all__ = ["admission_lock", "producer_alive", "this_producer"]
