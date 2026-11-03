"""Removes side worktrees at a session's close-out: a dry run lists them, `--apply` removes them.

`python scripts/sweep_worktrees.py [NAME ...] [--apply]`, from the main tree; no NAME is every one.
Each worktree's changes against its HEAD, untracked files included, are saved first to
`.scratch/worktree-patches/<date>/<name>.patch` (`git apply` skips its header line).

The tree goes through `rmtree_robust`, which unlinks a junction and never walks into it. Never
`git worktree remove`: its recursive delete follows the `.venv` and `webapp/node_modules`
junctions into the main tree. A `worktree-*` branch, which the harness made, goes with its
worktree; any other branch stays.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from promptpotter.infrastructure.store.io import rmtree_robust

REPO = Path(__file__).resolve().parents[1]
_HARNESS_BRANCH = "worktree-"


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str
    branch: str | None
    locked: bool


def _git(*args: str, cwd: Path = REPO) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args)} in {cwd}: {done.stderr.strip()}")
    return done.stdout


def _registered() -> list[Worktree]:
    """`git worktree list` names the main tree first; a run from a side tree is refused."""
    found: list[Worktree] = []
    for block in _git("worktree", "list", "--porcelain").strip().split("\n\n"):
        fields = {k: v for k, _, v in (line.partition(" ") for line in block.splitlines())}
        branch = fields.get("branch", "").removeprefix("refs/heads/") or None
        found.append(
            Worktree(Path(fields["worktree"]).resolve(), fields["HEAD"], branch, "locked" in fields)
        )
    main, *side = found
    if main.path != REPO:
        raise SystemExit(f"run this from the main tree {main.path}, not {REPO}")
    return side


def _describe(tree: Worktree) -> str:
    changed = len(_git("status", "--porcelain", cwd=tree.path).splitlines())
    age = _git("log", "-1", "--format=%cr", tree.head).strip()
    ancestry = ["git", "merge-base", "--is-ancestor", tree.head, "main"]
    on_main = subprocess.run(ancestry, cwd=REPO, capture_output=True).returncode == 0
    return (
        f"{tree.path.name}: {tree.branch or 'detached'} at {tree.head[:9]} ({age}), "
        f"{changed} changed files, HEAD {'on' if on_main else 'not on'} main"
        + (", LOCKED (skipped)" if tree.locked else "")
    )


def _save_patch(tree: Worktree, into: Path) -> int:
    # Staging is harmless here (the tree is about to go) and is what brings untracked files in.
    _git("add", "--all", cwd=tree.path)
    patch = _git("diff", "--cached", "--binary", tree.head, cwd=tree.path)
    if patch:
        header = f"# worktree {tree.path.name} at {tree.head} on {tree.branch or 'detached'}\n"
        into.mkdir(parents=True, exist_ok=True)
        target = into / f"{tree.path.name}.patch"
        target.write_text(header + patch, encoding="utf-8", newline="\n")
    return len(patch)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("names", nargs="*", help="worktree directory names; none means all")
    parser.add_argument("--apply", action="store_true", help="remove them (default: list only)")
    args = parser.parse_args()

    side = _registered()
    unknown = set(args.names) - {t.path.name for t in side}
    if unknown:
        raise SystemExit(f"no such worktree: {', '.join(sorted(unknown))}")
    chosen = [t for t in side if not args.names or t.path.name in args.names]
    patches = REPO / ".scratch" / "worktree-patches" / datetime.date.today().isoformat()
    for tree in chosen:
        print(_describe(tree))
        if not args.apply or tree.locked:
            continue
        print(f"  patch {_save_patch(tree, patches):,} bytes; removing")
        rmtree_robust(tree.path)
    if args.apply:
        _git("worktree", "prune")
        for tree in chosen:
            if tree.branch and tree.branch.startswith(_HARNESS_BRANCH) and not tree.locked:
                _git("branch", "-D", tree.branch)
    # A directory left under the harness's root that git does not list is reported, never deleted.
    registered = {t.path for t in side}
    harness_root = REPO / ".claude" / "worktrees"
    for stray in sorted(harness_root.iterdir() if harness_root.is_dir() else ()):
        if stray.resolve() not in registered:
            print(f"not a worktree, left in place: {stray}")
    if not args.apply and chosen:
        print("dry run: re-run with --apply to remove these")
    return 0


if __name__ == "__main__":
    sys.exit(main())
