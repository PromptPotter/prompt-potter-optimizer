"""Reset the browser walk's throwaway workspace, KEEPING the paid caches.

Why keeping them makes the second pass free — and how to tell whether it did — is ``README.md``
beside this file, § The second pass is free. Don't restate it here; it is one argument and it
drifts the moment it has two homes.

Python rather than a few lines of ``fs.rmSync`` in ``serve.mjs``, for two reasons specific to this
tree. The cache NAMES have one author (``layout.py::SHARED_CACHE_DIRS``) and restating them in
another language is how one gets added there and destroyed here — silently, and it is the single
loss with no way back, because those directories are paid LLM spend. And an L4 inner sandbox nests
observation directories past Windows' ``MAX_PATH`` (measured at 668 chars), which ``shutil.rmtree``
cannot remove and ``rmtree_robust`` can.

**It is not the ``reset`` VERB and must not be folded into it.** ``reset`` keeps the operator's
account and drops their campaigns, which is what an operator wants. This keeps the MONEY and drops
everything else, the account included, because ``cold/first-run.spec.ts`` asserts the consent gate
a brand-new account meets and a surviving ``user.json`` has already agreed to it. Two different
answers to "what survives", each right for its own caller.

    python webapp/e2e/reset_world.py <home>                 # keep the caches — the default
    python webapp/e2e/reset_world.py <home> --drop-caches   # re-record the tape from nothing
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from promptpotter.infrastructure.store.io import rmtree_robust, unlink_robust
from promptpotter.infrastructure.store.layout import SHARED_CACHE_DIRS


def prune(directory: Path, keep: frozenset[str]) -> bool:
    """Delete everything under *directory* except the *keep* directories, at any depth.

    Returns whether anything survived, so an ancestor emptied on the way out is removed rather
    than left as a skeleton of directories holding nothing.
    """
    survived = False
    for entry in sorted(directory.iterdir()):
        if entry.is_dir() and not entry.is_symlink():
            if entry.name in keep:
                survived = True
                continue
            if prune(entry, keep):
                survived = True
            else:
                rmtree_robust(entry)
            continue
        unlink_robust(entry)
    return survived


def _kept_summary(home: Path, keep: frozenset[str]) -> list[str]:
    """One line per surviving cache, with a file count — the tape has to be VISIBLE.

    A warm cache and a cold one are byte-identical from the outside, and the whole failure mode
    this guards against is a tape that silently went missing and left the tier quietly paying full
    price again while still passing.
    """
    lines: list[str] = []
    for path in sorted(p for p in home.rglob("*") if p.is_dir() and p.name in keep):
        n = sum(1 for f in path.rglob("*") if f.is_file())
        lines.append(f"[e2e] kept {path.relative_to(home)} — {n:,} entries")
    return lines


def main(argv: list[str]) -> int:
    if not argv:
        print("[e2e] usage: reset_world.py <home> [--drop-caches]", file=sys.stderr)
        return 2
    home = Path(argv[0]).resolve()
    drop_caches = "--drop-caches" in argv[1:]

    # THE GUARD IS THE PATH, NEVER A FLAG, and it lives here because this is where the deleting
    # happens — `serve.mjs` decides WHETHER to reset and this decides whether the target may be.
    # `PP_E2E_COLD_HOME` is a documented operator knob, so pointing it at a real workspace, or at
    # `.` (which resolves to the caller's cwd), must not be a way to delete that instead. A
    # refusal is loud and fatal rather than a skipped reset: a cold tier running against a
    # populated workspace passes its assertions for the wrong reason.
    tmp = Path(tempfile.gettempdir()).resolve()
    if home == tmp or not home.is_relative_to(tmp):
        print(
            f"[e2e] REFUSING to reset {home} — a workspace this harness wipes must live under "
            f"{tmp}. Point PP_E2E_COLD_HOME somewhere disposable, or pass PP_E2E_KEEP=1.",
            file=sys.stderr,
        )
        return 2

    if not home.is_dir():
        print(f"[e2e] {home} does not exist yet — nothing to reset")
        return 0

    if drop_caches:
        rmtree_robust(home)
        print(f"[e2e] dropped {home} WHOLE, paid caches included — the next run re-records")
        return 0

    keep = frozenset(SHARED_CACHE_DIRS)
    print(f"[e2e] resetting {home}, keeping {', '.join(sorted(keep))}")
    prune(home, keep)
    for line in _kept_summary(home, keep) or ["[e2e] no cache survived — this run records one"]:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
