"""Who may admit: an OS file lock over the jobs dir."""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from pathlib import Path

from promptpotter.config.paths import ADMISSION_LOCK, LANDING_FILE
from promptpotter.config.settings import CODE_REVISION, LOCK_TIMEOUT
from promptpotter.infrastructure.store.io import read_text_optional, take_lock, write_text
from promptpotter.shared.errors import ServiceUnavailableError


class AdmissionGate:
    """ONE per registry, reentrant in its holding thread: an admission nests through `request_slot`."""

    def __init__(self, jobs_dir: Path) -> None:
        self._path = jobs_dir / ADMISSION_LOCK
        # The code a landing admits alone; never removed, since the restarted API runs that code.
        self._landing = jobs_dir / LANDING_FILE
        self._nest = threading.RLock()
        self._depth = 0

    def landing(self) -> str | None:
        """The code a landing admits alone, while THIS process runs other code; ``None`` admits."""
        target = read_text_optional(self._landing).strip()
        return target if target and target != CODE_REVISION else None

    def close_for(self, target: str) -> None:
        write_text(self._landing, target)

    @contextlib.contextmanager
    def held(self) -> Iterator[None]:
        with self._nest:
            lock = None
            if self._depth == 0 and (lock := take_lock(self._path, wait_s=LOCK_TIMEOUT)) is None:
                raise ServiceUnavailableError(
                    "The machine's admission gate is held by another process that has not "
                    "released it. Nothing was started; retry shortly.",
                    code="admission_gate_stuck",
                )
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if lock is not None:
                    lock.release()
