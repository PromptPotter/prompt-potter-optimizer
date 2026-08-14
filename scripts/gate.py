"""Every check CI runs, in one invocation — the single declaration of the gate.

The list used to exist four times and agree nowhere: root ``CLAUDE.md`` named
five tools, the PR template a sixth combination, ``.githooks/pre-commit`` a
different five, and ``ci.yml`` fourteen. The layering guard, the three
generated-surface diffs, the webapp tests, the anti-rot scan and the deploy
build appeared in no local command at all, so "green locally, red in `main`"
was structural rather than careless. All four are callers of this file now.

Two properties the shape buys, neither of them speed:

- **Nothing masks anything.** GitHub steps fail-fast, so a red ``ruff`` hid the
  other eight results and each fix cost another full push. Every selected check
  runs here, always, and the verdict names all of them.
- **The tools run from the already-resolved interpreter** (``sys.executable -m``),
  so the ~3.5s ``uv run`` toll is paid once for the whole gate instead of once
  per tool, and the independent checks run concurrently.

Success is one line — a check prints only when it fails (or under
``GITHUB_ACTIONS``, where the per-check timing is the point).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_WEBAPP = _REPO / "webapp"
_PINNED = _REPO / ".venv"
_REEXEC = "PROMPTPOTTER_GATE_REEXEC"

# (returncode, output to show if it failed).
Outcome = tuple[int, str]


@dataclass(frozen=True)
class Sel:
    """What the run is scoped to. Empty file lists outside ``--staged`` mode."""

    staged: bool
    py_files: tuple[str, ...]
    web_files: tuple[str, ...]

    @classmethod
    def build(cls, staged: bool) -> Sel:
        if not staged:
            return cls(False, (), ())
        _, out = _run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], _REPO)
        names = [n for n in out.splitlines() if n]
        return cls(
            True,
            tuple(n for n in names if n.endswith(".py")),
            # eslint and tsc both want to run from webapp/, so the prefix goes.
            tuple(
                n[len("webapp/") :]
                for n in names
                if n.startswith("webapp/") and n.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs"))
            ),
        )


def _run(argv: Sequence[str], cwd: Path, **extra_env: str) -> Outcome:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **extra_env}
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def _node(exe: str, *args: str) -> list[str]:
    # Windows ships npm/npx as .cmd shims, which subprocess will not find bare.
    return [shutil.which(exe) or exe, *args]


def _generated(script: str, path: str) -> Callable[[Sel], Outcome]:
    """Regenerate a surface, then fail if the worktree moved.

    All three come off the Pydantic models. Ungated, a drifted model ships a stale
    schema the webapp typechecks against and the LLM is asked to fill. The generators
    read the worktree rather than the index, so this stays whole-tree even under
    ``--staged``.
    """

    def check(_sel: Sel) -> Outcome:
        rc, out = _run([sys.executable, f"scripts/{script}"], _REPO)
        if rc:
            return rc, out
        rc, _ = _run(["git", "diff", "--quiet", "--", path], _REPO)
        if rc:
            _, stat = _run(["git", "diff", "--stat", "--", path], _REPO)
            return 1, f"{path} was stale — regenerated in place; `git add` it.\n{stat}"
        return 0, ""

    return check


def _scan(
    files: Sequence[Path],
    needle: re.Pattern[str],
    *,
    allow_line: re.Pattern[str] | None = None,
    allow_path: re.Pattern[str] | None = None,
) -> list[str]:
    """Grep with an exemption, and the two exemptions are not interchangeable.

    An allowed LINE is a sanctioned use (the CLI-seam imports); an allowed PATH is a
    file exempt whatever it says (the migration-debt components). Honouring a path
    pattern against line text would exempt any line that merely names one of those
    files — a comment pointing at the spine would hide a real violation beside it.
    """
    hits = []
    for path in files:
        rel = path.relative_to(_REPO).as_posix()
        if allow_path is not None and allow_path.search(rel):
            continue
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if needle.search(line) and not (allow_line is not None and allow_line.search(line)):
                hits.append(f"{rel}:{num}: {line.strip()}")
    return hits


def _sources(root: Path, *patterns: str) -> list[Path]:
    return sorted(p for pattern in patterns for p in root.rglob(pattern))


_IMPORTS_PRESENTATION = re.compile(r"(?:from|import) promptpotter\.presentation")
# CLI-seam debt; shrink it to zero. The fix is to move the shared piece into
# application/ — presentation imports upward.
_LAYERING_ALLOW = re.compile(
    r"presentation\.(?:views\.live\.display import LiveDisplay"
    r"|cli\.session import (?:SessionCtx|load_session))"
)


def _layering(_: Sel) -> Outcome:
    hits = _scan(
        _sources(_REPO / "promptpotter" / "application", "*.py"),
        _IMPORTS_PRESENTATION,
        allow_line=_LAYERING_ALLOW,
    )
    return (1, "application must not import presentation:\n" + "\n".join(hits)) if hits else (0, "")


_LIVE_L1 = re.compile(r"liveL1Candidates")
# Two surfaces re-deriving the candidate list is what produced the
# lineage/fitness alignment bug. New consumers go through useRoundCandidates() /
# lib/derivations/round-candidates.ts; the named components are migration debt,
# each owing its own derivation.
_ANTI_ROT_ALLOW = re.compile(
    r"__tests__/|FreqChart\.tsx|HardSamplesHeatmap\.tsx|CandidatesCard\.tsx"
    r"|lib/poll\.tsx|lib/derivations/round-candidates\.ts"
)


def _anti_rot(_: Sel) -> Outcome:
    files = [
        p
        for root in ("components", "lib", "app")
        for p in _sources(_WEBAPP / root, "*.ts", "*.tsx")
    ]
    hits = _scan(files, _LIVE_L1, allow_path=_ANTI_ROT_ALLOW)
    return (1, "liveL1Candidates outside the spine:\n" + "\n".join(hits)) if hits else (0, "")


def _ruff_format(sel: Sel) -> Outcome:
    if sel.staged:
        return _run(_py("ruff", "format", "--check", *sel.py_files), _REPO)
    return _run(_py("ruff", "format", "--check", "promptpotter/", "tests/"), _REPO)


def _ruff_check(sel: Sel) -> Outcome:
    if sel.staged:
        return _run(_py("ruff", "check", *sel.py_files), _REPO)
    return _run(_py("ruff", "check", "promptpotter/", "tests/"), _REPO)


def _tsc(sel: Sel) -> Outcome:
    # Whole-program either way: a staged file can break a type in a file nobody
    # staged. `next build` checks neither this nor eslint — next.config sets
    # `typescript.ignoreBuildErrors` and Next 16 dropped build-time linting.
    if sel.staged:
        argv = _node("npx", "tsc", "--noEmit", "--incremental", "--tsBuildInfoFile", ".tsbuildinfo")
    else:
        argv = _node("npx", "tsc", "--noEmit")
    return _run(argv, _WEBAPP)


def _eslint(sel: Sel) -> Outcome:
    # A full-tree lint is ~73s against ~21s staged-only, so the hook pays the
    # narrow one and CI pays the whole.
    if sel.staged:
        return _run(_node("npx", "eslint", *sel.web_files), _WEBAPP)
    return _run(_node("npm", "run", "lint"), _WEBAPP)


@dataclass(frozen=True)
class Check:
    name: str
    kind: str  # "py" — CI's `check` job; "web" — its `webapp` job
    run: Callable[[Sel], Outcome]
    staged: bool = False  # in the pre-commit fast set


_BBEH_DIR = "docs/research/bbeh-comparison"


def _mypy(_sel: Sel) -> Outcome:
    """Every tracked module that imports ``promptpotter``, not just the package.

    The harnesses outside it were unchecked and each had rotted against a rename: the BBEH
    runner passed a kwarg no signature accepted and read a field off a NamedTuple that has
    none, ``render_review.py`` named ``RoundResult.opt_search_point`` after it became
    ``opt_sp``. None of them raised until someone ran them, and ruff cannot see an attribute
    that is not there. ``shared_config`` is followed but not a target: it stays import-safe
    for Colab, so it imports nothing of ours.
    """
    return _run(
        _py("mypy", "promptpotter/", "scripts/", f"{_BBEH_DIR}/bbeh_potter_runner.py"),
        _REPO,
        MYPYPATH=str(_REPO / _BBEH_DIR),
    )


CHECKS: tuple[Check, ...] = (
    Check("ruff-format", "py", _ruff_format, staged=True),
    Check("ruff-check", "py", _ruff_check, staged=True),
    Check("deptry", "py", lambda _: _run(_py("deptry", "."), _REPO)),
    Check("mypy", "py", _mypy),
    Check("layering", "py", _layering, staged=True),
    Check(
        "ts-types",
        "py",
        _generated("build_ts_types.py", "webapp/lib/api/types.generated.ts"),
        staged=True,
    ),
    Check(
        "optimizer-schemas",
        "py",
        _generated(
            "build_optimizer_schemas.py", "promptpotter/assets/optimizer/resolved_schemas.json"
        ),
        staged=True,
    ),
    Check(
        "openapi",
        "py",
        _generated("build_openapi.py", "docs/specs/openapi.generated.json"),
        staged=True,
    ),
    # No `--cov`: `fail_under = 0`, so the coverage table asserts nothing and is
    # pure display on every run. `pytest --cov` still works when the number is
    # wanted deliberately.
    Check("pytest", "py", lambda _: _run(_py("pytest", "tests/"), _REPO)),
    Check("eslint", "web", _eslint, staged=True),
    Check("tsc", "web", _tsc, staged=True),
    Check("anti-rot", "web", _anti_rot, staged=True),
    Check("vitest", "web", lambda _: _run(_node("npm", "run", "test"), _WEBAPP)),
    # DEPLOY_BUILD=1 validates the shipped artifact: React Compiler pass + source
    # maps. Bare `npm run build` is the local preview path, and skips both. The env
    # var is set here rather than through the `build:deploy` script, whose bash
    # inline-env prefix makes it unrunnable on Windows — so the check could not fire
    # on the one machine that runs it before CI does.
    Check(
        "next-build",
        "web",
        lambda _: _run(_node("npm", "run", "build"), _WEBAPP, DEPLOY_BUILD="1"),
    ),
)


@dataclass(frozen=True)
class Result:
    check: Check
    rc: int
    out: str
    secs: float


def _execute(check: Check, sel: Sel) -> Result:
    started = time.monotonic()
    try:
        rc, out = check.run(sel)
    except Exception as exc:  # a broken check is a red check, never a silent skip
        rc, out = 1, f"{type(exc).__name__}: {exc}"
    return Result(check, rc, out, time.monotonic() - started)


def _reexec_pinned() -> None:
    """The gate picks its own interpreter, because a verdict must not depend on the caller.

    Launched from the system Python instead of the locked environment, ``mypy`` resolved
    different stubs and reported two errors that do not exist under ``uv.lock`` — a green
    CI and a red desk, from the same commit and the same command. Only the Python half
    needs it: the webapp checks shell out to node, and CI's `webapp` job has no uv.
    """
    if Path(sys.prefix) == _PINNED or os.environ.get(_REEXEC):
        return
    uv = shutil.which("uv")
    pinned = "uv run --frozen --extra stats --extra dev python scripts/gate.py"
    if not uv:
        raise SystemExit(
            f"gate: `uv` is not on PATH, so the locked env is unreachable.\nRun: {pinned}"
        )
    proc = subprocess.run(
        [
            uv,
            "run",
            "--frozen",
            "--extra",
            "stats",
            "--extra",
            "dev",
            "python",
            __file__,
            *sys.argv[1:],
        ],
        cwd=_REPO,
        env={**os.environ, _REEXEC: "1"},
    )
    raise SystemExit(proc.returncode)


def main() -> int:
    # A failing check's output is whatever the tool prints, and tools print ✖ and →.
    # On a cp1252 console that raised while REPORTING the failure, losing the verdict.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="Run every check CI runs.")
    parser.add_argument("--py", action="store_true", help="only the Python half (CI's `check` job)")
    parser.add_argument(
        "--web", action="store_true", help="only the webapp half (CI's `webapp` job)"
    )
    parser.add_argument(
        "--staged", action="store_true", help="the pre-commit fast set, scoped to staged files"
    )
    parser.add_argument("--only", metavar="NAME", help="one check by name")
    args = parser.parse_args()

    kinds = {k for k, on in (("py", args.py), ("web", args.web)) if on} or {"py", "web"}
    checks = [c for c in CHECKS if c.kind in kinds and (c.staged or not args.staged)]
    if args.only:
        checks = [c for c in CHECKS if c.name == args.only]
        if not checks:
            parser.error(f"no such check: {args.only} (have: {', '.join(c.name for c in CHECKS)})")

    # Staged mode pays for what was staged: a webapp-only commit runs no Python half
    # (and so never waits on uv), a Python-only commit runs no node half.
    sel = Sel.build(args.staged)
    if args.staged:
        empty = {"py"} if not sel.py_files else set()
        empty |= {"web"} if not sel.web_files else set()
        checks = [c for c in checks if c.kind not in empty]
    if any(c.kind == "py" for c in checks):
        _reexec_pinned()

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(4, (os.cpu_count() or 2))) as pool:
        results = list(pool.map(lambda c: _execute(c, sel), checks))
    total = time.monotonic() - started
    failed = [r for r in results if r.rc]

    # The table is the evidence, so it prints when something needs explaining —
    # and in CI, where the per-check timing is what the log is for.
    if failed or os.environ.get("GITHUB_ACTIONS"):
        for r in results:
            print(f"{r.check.name:<20}{'FAIL' if r.rc else 'ok':>5}{r.secs:>8.1f}s")
    for r in failed:
        print(f"\n--- {r.check.name} ---\n{r.out}")
    scope = "staged" if args.staged else "+".join(sorted(kinds))
    if failed:
        print(f"\ngate[{scope}]: {len(results)} checks, {len(failed)} failed, {total:.1f}s")
        return 1
    print(f"gate[{scope}]: {len(results)} checks green in {total:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
