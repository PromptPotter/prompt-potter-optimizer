"""A run's own process: started with its spec on stdin, and taken as started only once it says it
holds its job, so a spawner gone before then leaves a job the sweep releases, never one held twice."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import IO, Any

# Out of the spawner's process group, so its Ctrl+C or a service stop signal is the spawner's alone.
_DETACHED: dict[str, Any]
if sys.platform == "win32":
    _DETACHED = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
else:
    _DETACHED = {"start_new_session": True}

# The one line a run's process says on its stdout: it holds the job.
_ADOPTED = b"adopted\n"


def start(module: str, spec: bytes, log: IO[bytes] | None) -> subprocess.Popen[bytes]:
    """*module* run with *spec* on stdin; stdout is `adopted`'s, stderr *log* (none: discarded)."""
    child = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL if log is None else log,
        **_DETACHED,
    )
    assert child.stdin is not None
    child.stdin.write(spec)
    child.stdin.close()
    return child


def adopted(child: subprocess.Popen[bytes]) -> bool:
    """Whether *child* took its job; ``False`` once it exits without saying so. Blocks until then."""
    assert child.stdout is not None
    with child.stdout:
        return any(line == _ADOPTED for line in child.stdout)


def tell_spawner() -> None:
    """The child's half of `adopted`; stdout then joins its log, which closes the pipe."""
    sys.stdout.flush()
    sys.stdout.buffer.write(_ADOPTED)
    sys.stdout.buffer.flush()
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
