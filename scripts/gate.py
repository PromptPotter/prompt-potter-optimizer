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

One kind is not in the default run: ``--release`` selects the advisory checks, which
need the network and answer a question only a release asks. They live here anyway, so
the desk and ``publish.yml`` read one declaration rather than two.

Success is one line — a check prints only when it fails (or under
``GITHUB_ACTIONS``, where the per-check timing is the point).
"""

from __future__ import annotations

import argparse
import json
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

# One parallelism budget, divided: _POOL checks run at once and each check that
# parallelises internally takes _SLICE, so peak demand is bounded by construction.
# Sized independently, three of them each assumed the whole box — vitest forked
# `availableParallelism() - 1`, Turbopack claims every core — and the contention
# starved a vitest worker's own startup mid-gate. Every parallel check reads this.
_JOBS = os.cpu_count() or 2
_POOL = 4
_SLICE = max(1, _JOBS // _POOL)

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
    """Regenerate a surface, then fail if the generator moved it.

    All three come off the Pydantic models. Ungated, a drifted model ships a stale
    schema the webapp typechecks against and the LLM is asked to fill. The comparison is
    against the file's own prior CONTENT, never the index: a model edit leaves the
    regenerated file correct but unstaged, and asking git would then fail on a tree that
    is already right and only go green on `git add`. Snapshot-vs-output is the same
    verdict on CI's clean checkout and stops firing here once the file is regenerated.
    """

    def check(_sel: Sel) -> Outcome:
        target = _REPO / path
        before = target.read_bytes() if target.exists() else None
        rc, out = _run([sys.executable, f"scripts/{script}"], _REPO)
        if rc:
            return rc, out
        if target.read_bytes() != before:
            return 1, f"{path} was stale — regenerated in place; re-run to confirm."
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

    Split on ``\\n`` rather than ``splitlines()``, which also breaks on five of the characters
    ``_CONTROL_CHAR`` hunts — a needle matching one of those consumed it as a line terminator and
    never saw it. ``read_text`` already translates newlines, so the two agree everywhere else.
    """
    hits = []
    for path in files:
        rel = path.relative_to(_REPO).as_posix()
        if allow_path is not None and allow_path.search(rel):
            continue
        for num, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if needle.search(line) and not (allow_line is not None and allow_line.search(line)):
                hits.append(f"{rel}:{num}: {line.strip()}")
    return hits


def _sources(root: Path, *patterns: str) -> list[Path]:
    return sorted(p for pattern in patterns for p in root.rglob(pattern))


_IMPORTS_PRESENTATION = re.compile(r"(?:from|import) promptpotter\.presentation")
# CLI-seam debt; shrink it to zero. The fix is to move the shared piece into
# application/ — presentation imports upward.
_LAYERING_ALLOW = re.compile(
    r"presentation\.(?:terminal\.live\.display import LiveDisplay"
    r"|cli\.session import (?:SessionCtx|load_session))"
)


def _layering(_: Sel) -> Outcome:
    hits = _scan(
        _sources(_REPO / "promptpotter" / "application", "*.py"),
        _IMPORTS_PRESENTATION,
        allow_line=_LAYERING_ALLOW,
    )
    return (1, "application must not import presentation:\n" + "\n".join(hits)) if hits else (0, "")


# A control character makes git call the whole FILE binary — the stat line reads `Bin 13089 ->
# 14743` and no diff is rendered for it, in review or in `git show`. Not a style rule: one NUL used
# as a key separator shipped a permanently stale freshness gate, and the diff that would have shown
# it did not exist. CR is absent deliberately — `read_text` translates line endings.
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _undiffable(_: Sel) -> Outcome:
    files = [
        *_sources(_REPO / "promptpotter", "*.py"),
        *_sources(_REPO / "scripts", "*.py"),
        *_sources(_REPO / "tests", "*.py"),
        # `.css` too: the byte makes any FILE binary, and a stylesheet is authored beside the
        # component it dresses, so leaving it out would leave the same hole one directory over.
        *(
            p
            for root in ("components", "lib", "app")
            for p in _sources(_WEBAPP / root, "*.ts", "*.tsx", "*.css")
        ),
    ]
    hits = _scan(files, _CONTROL_CHAR)
    return (
        (1, "control character — git renders these binary, so no diff is read:\n" + "\n".join(hits))
        if hits
        else (0, "")
    )


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


# What ruff lints when the run is not scoped to staged files. ``scripts/`` is here for the
# reason ``_mypy`` states below — a tracked module outside the package is still shipped code —
# and it was absent, which made the two modes disagree: ``--staged`` passes the staged paths
# verbatim, so the hook lints a script the full run never looks at. An import-order violation
# in this very file passed `gate.py` and was then rejected by `gate.py --staged` seconds later.
_RUFF_TARGETS = ("promptpotter/", "scripts/", "tests/")


def _ruff(*argv: str) -> Callable[[Sel], Outcome]:
    """One ruff invocation over the staged paths, or over the whole target set.

    Both modes of both subcommands spelled once: as two near-identical functions the target
    lists were free to drift from each other as well as from the staged half.
    """

    def check(sel: Sel) -> Outcome:
        paths = sel.py_files if sel.staged else _RUFF_TARGETS
        return _run(_py("ruff", *argv, *paths), _REPO)

    return check


def _tsc(_sel: Sel) -> Outcome:
    # Whole-program either way: a staged file can break a type in a file nobody
    # staged. `next build` checks neither this nor eslint — next.config sets
    # `typescript.ignoreBuildErrors` and Next 16 dropped build-time linting.
    # The staged mode used to pass its own --tsBuildInfoFile, which bought a
    # SECOND incremental store neither mode ever warmed; tsconfig already sets
    # `incremental`, so one script serves both and `npx` stops re-resolving.
    return _run(_node("npm", "run", "typecheck"), _WEBAPP)


def _eslint(sel: Sel) -> Outcome:
    # Empty outside --staged, where bare `eslint` means the whole tree — so the
    # scoped and full runs are one call. `npm run lint` carries --cache: a warm
    # full-tree lint is ~11s against ~30s cold, and a staged run pays eslint's
    # ~10s startup either way, cache or none.
    return _run(_node("npm", "run", "lint", "--", *sel.web_files), _WEBAPP)


def _playwright(_sel: Sel) -> Outcome:
    """The browser, on the one world any machine can honestly walk: the COLD tier.

    ``walk`` reads the operator's own ``.promptpotter/``, and every spec needing a campaign
    skips itself when discovery finds none — so a ``walk`` where there is no workspace exits 0
    having asserted almost nothing. ``spend`` costs real money. ``cold`` mints its throwaway
    world at startup (``e2e/serve.mjs``), so it asserts the same thing here and on a runner:
    the zero-campaign path a brand-new account meets.

    Behind ``next-build`` because the browser only ever sees ``out/``. A missing Chromium is
    named by Playwright's own error, with the install command in it.
    """
    return _run(_node("npx", "playwright", "test", "--project=cold"), _WEBAPP)


def _lock_satisfies(requirer: str, dep: str, entries: frozenset[str]) -> bool:
    """npm walks node_modules up from the requirer, so a copy nested under an UNRELATED package
    does not satisfy it — which is exactly the shape that reaches CI looking present."""
    prefix = requirer
    while True:
        if (f"{prefix}/node_modules/{dep}" if prefix else f"node_modules/{dep}") in entries:
            return True
        if not prefix:
            return False
        cut = prefix.rfind("/node_modules/")
        prefix = prefix[:cut] if cut != -1 else ""


def _lockfile(_: Sel) -> Outcome:
    """Every requirement in the lock has a package satisfying it.

    A lock resolved on Windows keeps the win32 optional variants and prunes the rest, so the
    requirements survive and the packages that answer them do not. `npm ci` ACCEPTS that on
    Windows and rejects it on Linux, which makes CI the first thing to notice and leaves no local
    command able to. This is the platform-free equivalent, and it is why the repair must run on
    Linux (`npm install --package-lock-only`) rather than here.
    """
    packages = json.loads((_WEBAPP / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    entries = frozenset(packages)
    unsatisfied = sorted(
        f"  {requirer or '<root>'} requires {dep}@{rng}"
        for requirer, body in packages.items()
        # A nested package's devDependencies are never installed; the root's always are.
        for block in ("dependencies", "optionalDependencies", "devDependencies")
        if block != "devDependencies" or not requirer
        for dep, rng in (body.get(block) or {}).items()
        if not _lock_satisfies(requirer, dep, entries)
    )
    if unsatisfied:
        return 1, (
            "package-lock.json carries requirements no package satisfies — regenerate it on "
            "LINUX (`cd webapp && npm install --package-lock-only`), never on Windows:\n"
            + "\n".join(unsatisfied)
        )
    return 0, ""


# A release is where the dependency posture stops being ours: the wheel carries the dashboard
# bundle, and a published version can be neither recalled nor re-uploaded. Both checks below
# reach the network, which is why they are their own kind — but neither may SKIP when it cannot
# answer. A guard that reports nothing instead of nothing-to-report is how v0.8.14 shipped a day
# before six advisories surfaced against the lock it had already frozen.
_SEVERITY_ORDER = ("critical", "high", "medium", "low")
_ALERT_FIELDS = (
    r'.[] | "\(.security_advisory.severity)\t\(.dependency.package.ecosystem)/'
    r'\(.dependency.package.name)\t\(.security_advisory.ghsa_id)\t\(.dependency.manifest_path)"'
)


def _npm_audit(_: Sel) -> Outcome:
    """The dashboard lock against npm's advisory database.

    ``--package-lock-only`` reads the lock rather than an install, so this answers before
    ``npm ci`` and judges the file that is actually frozen into the release. ``--audit-level``
    sets the EXIT CODE only — the report still lists every severity, so a moderate is seen
    here and blocks nothing, while ``_advisories`` is the exhaustive half.
    """
    return _run(_node("npm", "audit", "--package-lock-only", "--audit-level=high"), _WEBAPP)


def _advisories(_: Sel) -> Outcome:
    """Every open Dependabot alert on the repository — both ecosystems, dismissals honoured.

    Dismissal is why this reads GitHub rather than scanning: ``diskcache``
    (GHSA-w8v5-vhqr-4h9v) is unpatched upstream and reachable only through the ``dspy`` extra,
    so it is dismissed rather than fixed, and a local scanner would need an allowlist free to
    drift from the one the Security tab already holds. ``publish.yml`` runs ``--only
    npm-audit`` instead of this: a workflow's GITHUB_TOKEN is not documented to read Dependabot
    alerts, so enforcing it there is a PAT away — never a silent pass.
    """
    gh = shutil.which("gh")
    if not gh:
        return 1, (
            "`gh` is not on PATH, so the open advisories cannot be read — and a guard that "
            "cannot answer is not a guard.\nInstall the GitHub CLI, or read them on the "
            "repository's Security tab before cutting the release."
        )
    rc, out = _run(
        [
            gh,
            "api",
            "repos/{owner}/{repo}/dependabot/alerts?state=open",
            "--paginate",
            "-q",
            _ALERT_FIELDS,
        ],
        _REPO,
    )
    if rc:
        return rc, out or "gh could not read this repository's Dependabot alerts."
    alerts = sorted(
        (line for line in out.splitlines() if line.strip()),
        key=lambda line: (
            _SEVERITY_ORDER.index(sev)
            if (sev := line.split("\t")[0]) in _SEVERITY_ORDER
            else len(_SEVERITY_ORDER)
        ),
    )
    if not alerts:
        return 0, ""
    rows = [line.split("\t") for line in alerts]
    # Trailing 0: the last column is the manifest path, and padding it only trails whitespace.
    widths = [*(max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)), 0]
    return 1, (
        f"{len(alerts)} open Dependabot alert(s) — fix or dismiss each before cutting a "
        "release:\n"
        + "\n".join(
            "  " + "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True))
            for row in rows
        )
    )


@dataclass(frozen=True)
class Check:
    name: str
    kind: str  # "py" — CI's `check` job; "web" — its `webapp` job; "release" — publish.yml
    run: Callable[[Sel], Outcome]
    staged: bool = False  # in the pre-commit fast set
    after: str = ""  # a check this one reads the output of — see `_chains`


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
    Check("ruff-format", "py", _ruff("format", "--check"), staged=True),
    Check("ruff-check", "py", _ruff("check"), staged=True),
    Check("deptry", "py", lambda _: _run(_py("deptry", "."), _REPO)),
    Check("mypy", "py", _mypy),
    Check("layering", "py", _layering, staged=True),
    # "py" so it runs without `webapp/node_modules`, which is routinely absent — a guard that
    # cannot run on the machine that would trip it is not a guard.
    Check("undiffable", "py", _undiffable, staged=True),
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
    # wanted deliberately. `-n` lives here rather than in `addopts` so an ad-hoc
    # single-test run does not pay worker startup for one assertion.
    Check("pytest", "py", lambda _: _run(_py("pytest", "tests/", "-n", str(_SLICE)), _REPO)),
    Check("lockfile", "web", _lockfile, staged=True),
    Check("eslint", "web", _eslint, staged=True),
    # tsconfig `include`s `.next/types`, which `next build` deletes and regenerates: run
    # concurrently, tsc either read that directory mid-rewrite (TS2307 on 3 of 6 runs) or won
    # the race and typechecked the PREVIOUS build's route signatures. Behind it, both ways.
    Check("tsc", "web", _tsc, staged=True, after="next-build"),
    Check("anti-rot", "web", _anti_rot, staged=True),
    # vitest.config.ts keeps its own `maxWorkers` for a standalone `npm run test`;
    # under the gate the budget decides, because here it shares the box.
    Check(
        "vitest",
        "web",
        lambda _: _run(_node("npm", "run", "test", "--", f"--maxWorkers={_SLICE}"), _WEBAPP),
    ),
    # DEPLOY_BUILD=1 validates the shipped artifact: React Compiler pass + source
    # maps. Bare `npm run build` is the local preview path, and skips both. The env
    # var is set here rather than through the `build:deploy` script, whose bash
    # inline-env prefix makes it unrunnable on Windows — so the check could not fire
    # on the one machine that runs it before CI does. GATE_JOBS rides the same
    # channel to cap Turbopack, which otherwise takes every core beside three
    # other checks; the operator's bare preview build sets neither and stays whole.
    Check(
        "next-build",
        "web",
        lambda _: _run(
            _node("npm", "run", "build"), _WEBAPP, DEPLOY_BUILD="1", GATE_JOBS=str(_SLICE)
        ),
    ),
    # Not `staged`: it opens a browser and boots two uvicorns, which no pre-commit should.
    Check("playwright", "web", _playwright, after="next-build"),
    # Neither is `staged`, and neither runs by default: the everyday gate stays offline and the
    # pre-commit hook pays for nothing it cannot use. `--release` is what asks for them.
    Check("npm-audit", "release", _npm_audit),
    Check("advisories", "release", _advisories),
)


@dataclass(frozen=True)
class Result:
    check: Check
    rc: int
    out: str
    secs: float


def _chains(checks: Sequence[Check]) -> list[list[Check]]:
    """The selection, grouped into serial chains that each occupy ONE pool slot.

    A check declaring ``after`` runs on its predecessor's worker, immediately behind it, so
    ordering costs no concurrency and needs no second budget; a predecessor the selection
    dropped (``--only``, ``--staged``) leaves the dependent standing alone.
    """
    names = {c.name for c in checks}
    chains = [[c] for c in checks if c.after not in names]
    for chain in chains:
        i = 0
        while i < len(chain):
            chain += [c for c in checks if c.after == chain[i].name]
            i += 1
    scheduled = sum(len(chain) for chain in chains)
    if scheduled != len(checks):
        # A cycle in `after` leaves every member headless, so it schedules NOTHING and the run
        # still prints green over the checks that did survive — a verdict short of the checks it
        # claims. Raise instead: the one failure mode of this function has no other symptom.
        raise SystemExit(
            f"gate: {len(checks) - scheduled} check(s) reachable from no chain head — "
            "`after` describes a cycle."
        )
    return chains


def _execute(check: Check, sel: Sel) -> Result:
    started = time.monotonic()
    try:
        rc, out = check.run(sel)
    except Exception as exc:  # a broken check is a red check, never a silent skip
        rc, out = 1, f"{type(exc).__name__}: {exc}"
    return Result(check, rc, out, time.monotonic() - started)


# Spelled once: the message a missing `uv` prints and the argv it would have run are the
# same list, and they drifted the moment one grew an extra the other did not. `api` is here
# because mypy type-checks `main.py` and the routers — from an engine-only install it cannot
# resolve fastapi, and reports it as a first-party error. `harbor` is here because this env is
# where the verdict is TAKEN: without it `import harbor` fails here while succeeding at the desk,
# which is the drift this function exists to stop, pointing the other way.
_PINNED_EXTRAS = ("stats", "dev", "api", "harbor")


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
    extras = [arg for extra in _PINNED_EXTRAS for arg in ("--extra", extra)]
    if not uv:
        raise SystemExit(
            "gate: `uv` is not on PATH, so the locked env is unreachable.\n"
            f"Run: uv run --frozen {' '.join(extras)} python scripts/gate.py"
        )
    proc = subprocess.run(
        [uv, "run", "--frozen", *extras, "python", __file__, *sys.argv[1:]],
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
        "--release",
        action="store_true",
        help="the pre-release advisory checks (network-bound; never in the default run)",
    )
    parser.add_argument(
        "--staged", action="store_true", help="the pre-commit fast set, scoped to staged files"
    )
    parser.add_argument("--only", metavar="NAME", help="one check by name")
    args = parser.parse_args()

    selected = (("py", args.py), ("web", args.web), ("release", args.release))
    kinds = {k for k, on in selected if on} or {"py", "web"}
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
    with ThreadPoolExecutor(max_workers=_POOL) as pool:
        runs = list(pool.map(lambda chain: [_execute(c, sel) for c in chain], _chains(checks)))
    results = [r for run in runs for r in run]
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
    # The total is max(slowest chain, sum of work / _POOL), so naming the slowest is naming
    # the wall — without it a green run reports a number nobody can act on. A chain holds one
    # slot, so the wall is a chain's total and never a single check inside it.
    slowest = max(runs, key=lambda run: sum(r.secs for r in run), default=[])
    named = "+".join(r.check.name for r in slowest)
    wall = f" (slowest {named} {sum(r.secs for r in slowest):.1f}s)" if slowest else ""
    print(f"gate[{scope}]: {len(results)} checks green in {total:.1f}s{wall}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
