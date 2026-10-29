"""Stop hook: block a stop where code ran ahead of its docs, or where docs grew.

Counts only the files THIS session wrote, read off the transcript's Write/Edit calls: the tree is
shared with other sessions, and an unreadable transcript keeps the hook silent. Growth counts NET
added lines and new pages, untracked ones included, because the page is what costs maintenance.
Each arm re-fires only on new work past its baseline, and a page is named once.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

MIN_CODE_LINES = 40  # ignore small changesets — not worth a docs pass
MAX_RATIO = 20  # code:doc churn past this (or docs == 0) -> stale-docs nudge
MIN_DOC_GROWTH = 150  # NET added doc lines before the growth nudge fires

# Under `git rev-parse --git-path`: in a worktree `.git` is a file. A shrunk count means a commit
# or revert landed, so that arm's baseline restarts at zero.
_BASELINE_NAME = "doc_drift_baseline"

_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
_CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".css",
    ".scss",
    ".sh",
    ".toml",
}


def _bucket(path: str) -> str:
    p = PurePosixPath(path)
    name = p.name.lower()
    if "generated" in name or name.endswith("lock.json"):
        return "skip"
    if "node_modules" in p.parts:
        return "skip"
    suffix = p.suffix.lower()
    if suffix in _DOC_SUFFIXES or "docs" in p.parts:
        return "docs"
    if suffix in _CODE_SUFFIXES:
        return "code"
    return "skip"


# No `Bash`: a shell write cannot be attributed without parsing arbitrary commands.
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _session_files(transcript_path: str | None) -> set[str] | None:
    """``None`` is unattributable, never the whole tree; ``set()`` is a session that wrote nothing."""
    if not transcript_path:
        return None
    try:
        raw = Path(transcript_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    root = Path.cwd().resolve()
    out: set[str] = set()
    for line in raw.splitlines():
        if '"tool_use"' not in line:  # cheap prefilter; most lines are not tool calls
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "tool_use":
                continue
            if item.get("name") not in _WRITE_TOOLS:
                continue
            fp = (item.get("input") or {}).get("file_path")
            if not isinstance(fp, str) or not fp:
                continue
            with contextlib.suppress(OSError, ValueError):  # outside the repo
                out.add(Path(fp).resolve().relative_to(root).as_posix())
    return out


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return out.stdout if out.returncode == 0 else ""


def _read_baseline(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_baseline(path: Path, base: dict[str, int]) -> None:
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(base), encoding="utf-8")


def _counts(numstat: str, keep: set[str]) -> tuple[int, int, int]:
    """(code churn, doc churn, NET doc lines added), over ``keep`` only."""
    code = docs = doc_net = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if added == "-":  # binary file
            continue
        if path not in keep:  # a co-editor's churn is not this session's to answer for
            continue
        bucket = _bucket(path)
        if bucket == "code":
            code += int(added) + int(removed)
        elif bucket == "docs":
            docs += int(added) + int(removed)
            doc_net += int(added) - int(removed)
    return code, docs, doc_net


def _new_pages(keep: set[str]) -> list[str]:
    added = _git("diff", "HEAD", "--name-only", "--diff-filter=A").split()
    untracked = _git("ls-files", "--others", "--exclude-standard").split()
    seen = {p for p in (*added, *untracked) if p in keep and _bucket(p) == "docs"}
    return sorted(seen)


_STALE = (
    "Doc-ratio check: this session changed ~{code} code lines but only ~{docs} doc lines. "
    "Delete what the change made untrue, per docs/CLAUDE.md § Editing a doc; if the docs "
    "are already accurate, say so in one line and stop."
)

_GROWTH = (
    "Doc-growth check: this session ADDED ~{net} net doc lines{pages}. One fact, one owner, "
    "ride an existing page, delete something — docs/CLAUDE.md § Editing a doc. If the growth "
    "is warranted, say so in one line and stop."
)


def _blocked(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("stop_hook_active") is True:  # this stop already answers a nudge
        return 0
    mine = _session_files(payload.get("transcript_path"))
    if mine is None:
        return 0

    try:
        code, docs, doc_net = _counts(_git("diff", "HEAD", "--numstat"), mine)
        pages = _new_pages(mine)
        baseline = Path(_git("rev-parse", "--git-path", _BASELINE_NAME).strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        return 0
    if baseline == Path():
        return 0

    base = _read_baseline(baseline)

    if code >= MIN_CODE_LINES and (docs == 0 or code > docs * MAX_RATIO):
        prior = base.get("code", 0)
        if code < prior:
            prior = 0
        if code - prior >= MIN_CODE_LINES:
            _write_baseline(baseline, {**base, "code": code})
            return _blocked(_STALE.format(code=code, docs=docs))

    nudged = base.get("pages", [])
    fresh = [p for p in pages if p not in (nudged if isinstance(nudged, list) else [])]
    prior = base.get("docs", 0)
    if doc_net < prior:
        prior = 0
    if doc_net - prior >= MIN_DOC_GROWTH or fresh:
        _write_baseline(baseline, {**base, "docs": doc_net, "pages": sorted({*nudged, *pages})})
        shown = ", ".join(fresh[:5]) + ("…" if len(fresh) > 5 else "")
        clause = f" across {len(fresh)} new page(s): {shown}" if fresh else ""
        return _blocked(_GROWTH.format(net=doc_net, pages=clause))

    return 0


if __name__ == "__main__":
    sys.exit(main())
