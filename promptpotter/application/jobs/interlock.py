"""Who may admit, and whether a job's producer is alive: both OS file locks over the jobs dir."""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import secrets
import threading
from pathlib import Path

from filelock import BaseFileLock, FileLock

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.infrastructure import producer_lock
from promptpotter.infrastructure.store.io import validate_path_component

logger = logging.getLogger(__name__)

# Both live INSIDE the jobs dir: it is globbed for `*.json` only, so neither reads as a job.
_ADMISSION_LOCK = ".admission.lock"
_PRODUCERS_DIR = "producers"

_token_lock = threading.Lock()
_tokens: dict[Path, str] = {}


def admission_lock(jobs_dir: Path) -> BaseFileLock:
    """ONE instance per registry: ``filelock`` counts acquisitions per object and per thread."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(str(jobs_dir / _ADMISSION_LOCK), timeout=LOCK_TIMEOUT)


def this_producer(jobs_dir: Path) -> str:
    key = jobs_dir.resolve()
    with _token_lock:
        producer_id = _tokens.get(key)
        if producer_id is not None:
            return producer_id
        producer_id = f"{os.getpid()}-{secrets.token_hex(4)}"
        path = _producer_path(jobs_dir, producer_id)
        # A clean exit retires its own file: its jobs are terminal, so no probe would reclaim it.
        if not producer_lock.take(path):
            raise RuntimeError(f"producer file {path} is held before this process minted it")
        atexit.register(_retire, path)
        _tokens[key] = producer_id
        logger.debug("producer %s holds %s", producer_id, path)
        return producer_id


def this_producer_lock(jobs_dir: Path) -> Path:
    return _producer_path(jobs_dir, this_producer(jobs_dir))


def producer_alive(jobs_dir: Path, producer_id: str) -> bool:
    """Answering RECLAIMS a dead producer's file; an unstamped id reads as gone."""
    if not producer_id:
        return False
    try:
        path = _producer_path(jobs_dir, producer_id)
    except ValueError:
        return False
    if producer_lock.held(path):
        return True
    with contextlib.suppress(OSError):
        path.unlink()
    return False


def _retire(path: Path) -> None:
    producer_lock.release(path)
    with contextlib.suppress(OSError):
        path.unlink()


def _producer_path(jobs_dir: Path, producer_id: str) -> Path:
    validate_path_component(producer_id)
    return jobs_dir / _PRODUCERS_DIR / f"{producer_id}.lock"


__all__ = ["admission_lock", "producer_alive", "this_producer", "this_producer_lock"]
