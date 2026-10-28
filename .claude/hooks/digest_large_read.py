"""PreToolUse on Read: refuse a WHOLE read of a large file, and hand back the next step.

Pages, logs and scratch dumps point at /digest. A long Markdown doc gets its heading map and a long
Python module its class/def map, so the next call reads one section. `CLAUDE.md` always passes. A
targeted read (`offset` / `limit`) always passes. Any failure in here lets the read through: a hook that cannot decide must
not block.
"""

import json
import os
import sys
from pathlib import Path

MIN_LINES = int(os.environ.get("DIGEST_MIN_LINES", "400"))
DOC_MIN_LINES = int(os.environ.get("DOC_MAP_MIN_LINES", "300"))
SOURCE_MIN_LINES = int(os.environ.get("SOURCE_MAP_MIN_LINES", "800"))
SOURCE_ANCHORS = ("class ", "def ", "async def ", "    def ", "    async def ")
NEVER_EDITED = {".html", ".htm", ".log", ".out", ".txt"}
SCRATCH_DUMPS = {".json", ".jsonl", ""}


def in_scope(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in NEVER_EDITED or ("/scratchpad/" in path.as_posix() and suffix in SCRATCH_DUMPS)


def outline(path: Path) -> list[str]:
    rows, fenced = [], False
    for number, line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        if path.suffix == ".py":
            if line.startswith(SOURCE_ANCHORS):
                rows.append(f"{number:>5}  {line.rstrip(':')}")
        elif line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            rows.append(f"{number:>5}  {line}")
    return rows


def refuse_whole_read(path: Path, lines: int) -> None:
    print(
        f"Read refused: {path.name} is {lines} lines. Read the one section you need with offset and "
        "limit — offset = its anchor's line, limit = up to the next anchor. Map:\n"
        + "\n".join(outline(path)),
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    call = json.load(sys.stdin).get("tool_input", {})
    if call.get("offset") or call.get("limit"):
        return
    path = Path(call.get("file_path") or "")
    mapped_min = {".md": DOC_MIN_LINES, ".py": SOURCE_MIN_LINES}.get(path.suffix.lower())
    if path.name == "CLAUDE.md":
        mapped_min = None
    if not (mapped_min or in_scope(path)) or not path.is_file():
        return
    with path.open("rb") as handle:
        lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    if mapped_min:
        if lines >= mapped_min:
            refuse_whole_read(path, lines)
        return
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
    main()
