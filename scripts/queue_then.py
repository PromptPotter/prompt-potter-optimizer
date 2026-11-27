"""`queue_then.py LABEL COMMAND...`: COMMAND once this clone's gate lock is ours, as the gate takes it.

`webapp/package.json`'s `test` names it, so a builder's `npm test` queues behind a running gate
instead of racing it. Stdlib only, as `processes.py` is."""

from __future__ import annotations

import shutil
import subprocess
import sys

from processes import queue_behind_siblings


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("usage: queue_then.py LABEL COMMAND...")
    queue_behind_siblings(argv[0])  # held to this process's end; the gate's own run passes through
    # Resolved as a shell would: on Windows an npm bin is `vitest.cmd`, which only PATHEXT finds.
    command, *args = argv[1:]
    return subprocess.run([shutil.which(command) or command, *args]).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
