"""PreToolUse on Read: refuse a WHOLE read of a large file nobody edits, and point at /digest.

Only pages, logs and scratch dumps are in scope — source, docs and rule files always pass, because
those are read to be worked in. A targeted read (`offset` / `limit`) always passes. Any failure in
here lets the read through: a hook that cannot decide must not block.
"""

import json
import os
import sys
from pathlib import Path

MIN_LINES = int(os.environ.get("DIGEST_MIN_LINES", "400"))
NEVER_EDITED = {".html", ".htm", ".log", ".out", ".txt"}
SCRATCH_DUMPS = {".json", ".jsonl", ""}


def in_scope(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in NEVER_EDITED or ("/scratchpad/" in path.as_posix() and suffix in SCRATCH_DUMPS)


def main() -> None:
    call = json.load(sys.stdin).get("tool_input", {})
    if call.get("offset") or call.get("limit"):
        return
    path = Path(call.get("file_path") or "")
    if not in_scope(path) or not path.is_file():
        return
    with path.open("rb") as handle:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    if lines < MIN_LINES:
        return
    print(
        f"Read refused: {path.name} is {lines} lines and is not a file you edit. Ask the /digest skill "
        f'instead (.venv/Scripts/python.exe .claude/digest.py --extract --question "…" --paths "{path.as_posix()}"), '
        "Grep it for the one thing you need, or Read a slice with offset and limit.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
