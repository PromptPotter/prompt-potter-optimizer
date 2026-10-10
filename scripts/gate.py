from __future__ import annotations

import argparse
import ast
import ctypes
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import tests_owning
from kept_verdict import ENGINE_RUNTIME, OFFLINE_RUN, OFFLINE_RUN_READS, KeptVerdicts, reads

_REPO = Path(__file__).resolve().parents[1]
_WEBAPP = _REPO / "webapp"
_PINNED = _REPO / ".venv"
_REEXEC = "PROMPTPOTTER_GATE_REEXEC"


def _free_memory_mb() -> int:
    if sys.platform == "win32":

        class Status(ctypes.Structure):
            _fields_ = (
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("free_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("free_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("free_virtual", ctypes.c_ulonglong),
                ("free_extended_virtual", ctypes.c_ulonglong),
            )

        status = Status(length=ctypes.sizeof(Status))
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.free_physical) >> 20
    else:
        try:
            meminfo = Path("/proc/meminfo").read_text(encoding="ascii")
            return int(re.search(r"MemAvailable:\s+(\d+) kB", meminfo)[1]) >> 10  # type: ignore[index]
        except (OSError, TypeError):  # no procfs (macOS): half of what is installed
            return (os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")) >> 21


_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_LIMIT_INFORMATION = 9
_job: int | None = None  # held open for the life of the process: closing it is what ends the tree


def end_children_with_this_process() -> None:
    """Windows only: POSIX reaps a foreground tree through its process group."""
    global _job
    if sys.platform != "win32":
        return

    class Basic(ctypes.Structure):
        _fields_ = (
            ("per_process_user_time", ctypes.c_longlong),
            ("per_job_user_time", ctypes.c_longlong),
            ("limit_flags", ctypes.c_ulong),
            ("minimum_working_set", ctypes.c_size_t),
            ("maximum_working_set", ctypes.c_size_t),
            ("active_process_limit", ctypes.c_ulong),
            ("affinity", ctypes.c_size_t),
            ("priority_class", ctypes.c_ulong),
            ("scheduling_class", ctypes.c_ulong),
        )

    class Extended(ctypes.Structure):
        _fields_ = (
            ("basic", Basic),
            ("io_counters", ctypes.c_ulonglong * 6),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
    )
    kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    limits = Extended()
    limits.basic.limit_flags = _KILL_ON_JOB_CLOSE
    job = kernel32.CreateJobObjectW(None, None)
    if not (
        job
        and kernel32.SetInformationJobObject(
            job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        and kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    _job = job


def memory_budget_mb() -> int:
    return max(_free_memory_mb() - 1024, 512)


_CORES = os.cpu_count() or 2
_MEMORY_MB = memory_budget_mb()
# Peak commit of one campaign process `offline_run.py::main` forks, rounded up.
OFFLINE_CHILD_MB = 120
OFFLINE_CHILDREN = min(8, _CORES)

Outcome = tuple[int, str]

_LINTED = (".ts", ".tsx", ".js", ".jsx", ".mjs")
# Anything else under `webapp/` is in no module graph, so it narrows nothing.
_WEB_MODULES = ("webapp/app/", "webapp/components/", "webapp/lib/")


@dataclass(frozen=True)
class Sel:
    staged: bool
    py_files: tuple[str, ...]
    web_files: tuple[str, ...]
    changed: tuple[str, ...] | None = None
    # ``None`` is the whole check.
    narrowed: tuple[str, ...] | None = None

    @classmethod
    def build(cls, staged: bool, changed: bool) -> Sel:
        if changed:
            _, tracked = _run(["git", "diff", "--name-only", "HEAD"], _REPO)
            _, untracked = _run(["git", "ls-files", "-o", "--exclude-standard"], _REPO)
            return cls(False, (), (), tuple(filter(None, (tracked + "\n" + untracked).split("\n"))))
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
                if n.startswith("webapp/") and n.endswith(_LINTED)
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


def _generated(script: str, *paths: str) -> Callable[[Sel], Outcome]:
    def check(_sel: Sel) -> Outcome:
        targets = [_REPO / path for path in paths]
        # Prior CONTENT, never the index: a model edit leaves the regenerated file right but unstaged.
        before = [t.read_bytes() if t.exists() else None for t in targets]
        rc, out = _run([sys.executable, f"scripts/{script}"], _REPO)
        if rc:
            return rc, out
        stale = [p for p, t, b in zip(paths, targets, before, strict=True) if t.read_bytes() != b]
        if stale:
            return 1, f"{', '.join(stale)} stale — regenerated in place; re-run to confirm."
        return 0, ""

    return check


def _scan(files: Sequence[Path], needle: re.Pattern[str]) -> list[str]:
    hits = []
    for path in files:
        rel = path.relative_to(_REPO).as_posix()
        # Not `splitlines()`: it breaks on five of the characters `_CONTROL_CHAR` hunts.
        for num, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if needle.search(line):
                hits.append(f"{rel}:{num}: {line.strip()}")
    return hits


def _sources(root: Path, *patterns: str) -> list[Path]:
    return sorted(p for pattern in patterns for p in root.rglob(pattern))


_IMPORTS_POTTER = ("", ("application/optimizers/potter/",), "application/optimizers/")
# `auth.py` is exempt: the Identity adapter, whose subject is the infrastructure it wraps.
_ROUTER_IMPORTS_INFRASTRUCTURE = (
    "presentation/api/routers/",
    ("infrastructure/",),
    "presentation/api/routers/auth.py",
)
_LAYERING: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("application/", ("presentation/",), ""),
    ("shared/", ("!shared/",), ""),
    ("domain/", ("infrastructure/", "connectors/", "application/", "presentation/"), ""),
    ("infrastructure/", ("application/", "presentation/"), ""),
    # `infrastructure/` reads the connector table, so a connector importing `application/` is a cycle.
    ("connectors/", ("application/", "presentation/"), ""),
    _ROUTER_IMPORTS_INFRASTRUCTURE,
    _IMPORTS_POTTER,
)


def _layering(_: Sel) -> Outcome:
    package = "promptpotter/"
    imports: dict[str, list[str]] = {}
    for path in _sources(_REPO / package, "*.py"):
        rel = path.relative_to(_REPO).as_posix()
        reached = tests_owning._imported(
            ast.parse(path.read_text(encoding="utf-8")),
            tests_owning._module_name(rel),
            path.name == "__init__.py",
        )
        imports[rel[len(package) :]] = sorted(target[len(package) :] for target in reached)
    hits = [
        f"{who or package}* must not import {' or '.join(banned)} — {source} imports {target}"
        for who, banned, exempt in _LAYERING
        for source, targets in imports.items()
        if source.startswith(who) and not (exempt and source.startswith(exempt))
        for target in targets
        if target != "__init__.py" and reads(target, banned)
    ]
    return (1, "\n".join(hits)) if hits else (0, "")


# Verbs name their handlers as strings (`campaign_runner.py::COMMANDS`) so the parser loads no use case.
_LOADED = "loaded: "
_HELP_PROBE = f"""
import runpy, sys
sys.argv = ["promptpotter", "--help"]
try:
    runpy.run_module("promptpotter", run_name="__main__")
except SystemExit:
    pass
for name in sorted(sys.modules):
    if name.startswith("promptpotter.application"):
        print({_LOADED!r} + name)
"""


def _help_imports(_: Sel) -> Outcome:
    rc, out = _run([sys.executable, "-X", "utf8", "-c", _HELP_PROBE], _REPO)
    if rc:
        return rc, out
    loaded = [line[len(_LOADED) :] for line in out.splitlines() if line.startswith(_LOADED)]
    if loaded:
        return 1, "`python -m promptpotter --help` imports the use-case layer:\n" + "\n".join(
            f"  {name}" for name in loaded
        )
    return 0, ""


# A handler named as a string is imported only on dispatch, so a dangling row parses and type-checks.
_HANDLER_PROBE = """
import importlib
from promptpotter.application.commands import dispatcher
from promptpotter.presentation.cli import campaign_runner
for table, package, rows in (
    ("COMMANDS", campaign_runner._COMMANDS_PACKAGE, campaign_runner.COMMANDS),
    ("HANDLER_FOR_KIND", dispatcher._HANDLER_PACKAGE, dispatcher.HANDLER_FOR_KIND),
):
    for key, target in rows.items():
        module, _, name = target.partition(":")
        try:
            resolved = callable(getattr(importlib.import_module(f"{package}.{module}"), name))
        except (ImportError, AttributeError) as exc:
            print(f"  {table}[{key!r}] -> {target}: {exc}")
        else:
            if not resolved:
                print(f"  {table}[{key!r}] -> {target}: not callable")
"""


def _handler_tables(_: Sel) -> Outcome:
    rc, out = _run([sys.executable, "-X", "utf8", "-c", _HANDLER_PROBE], _REPO)
    if rc:
        return rc, out
    return (1, "a handler table names what does not resolve:\n" + out) if out.strip() else (0, "")


# CR is absent deliberately: `read_text` translates line endings.
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _undiffable(_: Sel) -> Outcome:
    files = [
        *_sources(_REPO / "promptpotter", "*.py"),
        *_sources(_REPO / "scripts", "*.py"),
        *_sources(_REPO / "tests", "*.py"),
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


_INSTRUCTION_MAX_WORDS = 7000
_INSTRUCTION_FILES = ("*CLAUDE.md", ".claude/skills/*.md")

_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.DOTALL | re.MULTILINE)
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
# Slash-separated segments ending in an extension or a slash; a glob, URL, route or placeholder is none.
_PATH_CLAIM = re.compile(r"(?:\.{1,2}/)*[\w.@-]+(?:/[\w.@-]+)*(?:/|\.[A-Za-z]\w{0,4})")
_PATH_TAIL = re.compile(r"(::|#|:\d).*$")
_NUMBER_PLACEHOLDER = re.compile(r"N{3,}")


def _path_claims(text: str) -> set[str]:
    prose = _FENCE.sub("", text)
    spans = _BACKTICKED.findall(prose) + _LINK_TARGET.findall(prose)
    claims = {_PATH_TAIL.sub("", span) for span in spans}
    return {
        c
        for c in claims
        if "/" in c and _PATH_CLAIM.fullmatch(c) and not _NUMBER_PLACEHOLDER.search(c)
    }


def _instruction_files(_: Sel) -> Outcome:
    code, listed = _run(["git", "ls-files", "-z", *_INSTRUCTION_FILES], _REPO)
    if code:
        return code, listed
    code, tracked = _run(["git", "ls-files", "-z"], _REPO)
    if code:
        return code, tracked
    tails: set[str] = set()
    for path in tracked.split("\0"):
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            tails.update("/".join(parts[start:end]) for start in range(end))

    over: list[str] = []
    # Both spellings git may know a claim by: from the root, and from the file's own directory.
    unresolved: dict[tuple[str, str], set[str]] = {}
    for rel in filter(None, listed.split("\0")):
        text = (_REPO / rel).read_text(encoding="utf-8")
        if (words := len(text.split())) > _INSTRUCTION_MAX_WORDS:
            over.append(f"{rel}: {words} words")
        here = (_REPO / rel).parent
        for claim in _path_claims(text):
            if claim.startswith("../"):
                # One that climbs out of the repo names a sibling checkout no clone can vouch for.
                target = Path(os.path.normpath(here / claim))
                if target.is_relative_to(_REPO) and not target.exists():
                    unresolved[rel, claim] = set()
                continue
            on_disk = any((base / claim).exists() for base in (here, _REPO))
            if not on_disk and claim.strip("/") not in tails:
                beside = f"{here.relative_to(_REPO).as_posix()}/{claim}".removeprefix("./")
                unresolved[rel, claim] = {claim, beside}
    if asked := sorted(set().union(*unresolved.values())):
        answer = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z", "--verbose"],
            cwd=_REPO,
            input="\0".join(asked).encode(),
            capture_output=True,
        )
        if answer.returncode not in (0, 1):  # 1 is "none of them is ignored"
            return answer.returncode, answer.stderr.decode(errors="replace")
        # `--verbose` for the PATTERN: git on Windows reports a missing directory as matched by an empty one.
        fields = answer.stdout.decode().split("\0")  # source, line, pattern, path — per path
        ignored = {
            path for pattern, path in zip(fields[2::4], fields[3::4], strict=False) if pattern
        }
        unresolved = {key: paths for key, paths in unresolved.items() if not paths & ignored}
    failures = []
    if over:
        failures.append(f"over {_INSTRUCTION_MAX_WORDS} words:\n" + "\n".join(over))
    if unresolved:
        failures.append(
            "names a path that resolves nowhere in the repo:\n"
            + "\n".join(f"  {rel}: {claim}" for rel, claim in sorted(unresolved))
        )
    return (1, "\n".join(failures)) if failures else (0, "")


# `--staged` lints staged paths verbatim, so a directory missing here is linted by the hook only.
_RUFF_TARGETS = ("promptpotter/", "scripts/", "tests/", "examples/")


def _ruff(*argv: str) -> Callable[[Sel], Outcome]:
    def check(sel: Sel) -> Outcome:
        paths = sel.py_files if sel.staged else _RUFF_TARGETS
        return _run(_py("ruff", *argv, *paths), _REPO)

    return check


def _tsc(_sel: Sel) -> Outcome:
    # `next build` checks neither this nor eslint: next.config sets `typescript.ignoreBuildErrors`.
    return _run(_node("npm", "run", "typecheck"), _WEBAPP)


def _eslint(sel: Sel) -> Outcome:
    # `web_files` is empty outside --staged, where bare `eslint` means the whole tree.
    return _run(_node("npm", "run", "lint", "--", *sel.web_files, *(sel.narrowed or ())), _WEBAPP)


def _playwright(_sel: Sel) -> Outcome:
    # Only `cold`: `walk` reads the operator's own `.promptpotter/` and `spend` costs real money.
    return _run(_node("npx", "playwright", "test", "--project=cold"), _WEBAPP)


def _lock_satisfies(requirer: str, dep: str, entries: frozenset[str]) -> bool:
    # npm walks node_modules UP from the requirer: a copy nested under an unrelated package is no match.
    prefix = requirer
    while True:
        if (f"{prefix}/node_modules/{dep}" if prefix else f"node_modules/{dep}") in entries:
            return True
        if not prefix:
            return False
        cut = prefix.rfind("/node_modules/")
        prefix = prefix[:cut] if cut != -1 else ""


def _lockfile(_: Sel) -> Outcome:
    # `npm ci` accepts a Windows-resolved lock on Windows and rejects it on Linux; this is platform-free.
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


# No release check may SKIP when it cannot answer: a published version cannot be recalled.
_SEVERITY_ORDER = ("critical", "high", "medium", "low")
_ALERT_FIELDS = (
    r'.[] | "\(.security_advisory.severity)\t\(.dependency.package.ecosystem)/'
    r'\(.dependency.package.name)\t\(.security_advisory.ghsa_id)\t\(.dependency.manifest_path)"'
)


def _npm_audit(_: Sel) -> Outcome:
    # `--audit-level` sets the exit code only; `--omit=dev` because the wheel carries no dev tooling.
    audit = ("audit", "--package-lock-only", "--omit=dev", "--audit-level=high")
    return _run(_node("npm", *audit), _WEBAPP)


# No fixed release exists to bump to; each goes the day its fix ships.
_PIP_AUDIT_UNPATCHED = (
    "PYSEC-2026-2447",  # diskcache — reached only through the `dspy` extra
)
_PIP_AUDIT = "pip-audit@2.10.1"


def _pip_audit(_: Sel) -> Outcome:
    uv = shutil.which("uv")
    if not uv:
        return 1, "`uv` is not on PATH, so the Python lock cannot be exported to audit."
    with tempfile.TemporaryDirectory() as tmp:
        frozen = str(Path(tmp) / "requirements.txt")
        export = [uv, "export", "--frozen", "--all-extras", "--no-emit-project", "--quiet"]
        rc, out = _run([*export, "--output-file", frozen], _REPO)
        if rc:
            return rc, out
        ignored = [arg for vuln in _PIP_AUDIT_UNPATCHED for arg in ("--ignore-vuln", vuln)]
        # A `uv` tool, so the scanner is never a dependency of the lock it audits.
        audit = [uv, "tool", "run", "--quiet", _PIP_AUDIT, "--requirement", frozen]
        return _run([*audit, "--no-deps", "--disable-pip", *ignored], _REPO)


def _advisories(_: Sel) -> Outcome:
    # `publish.yml` runs the two scans above instead: its GITHUB_TOKEN cannot read Dependabot alerts.
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


# Too narrow is the one way `_Green` can lie, so widen on any doubt: `_TREE` costs only a rerun.
_TREE = ("",)
_ENGINE = ("!webapp/",)
_GATE = ("scripts/gate.py", "scripts/kept_verdict.py")
_WEBAPP_READS = ("webapp/", "tests/fixtures/", *_GATE)
_ENGINE_RUNTIME = (*ENGINE_RUNTIME, *_GATE)


@dataclass(frozen=True)
class Check:
    name: str
    kind: str  # "py" — CI's `check` job; "web" — its `webapp` job; "release" — publish.yml
    run: Callable[[Sel], Outcome]
    staged: bool = False  # in the pre-commit fast set
    landing: bool = False  # proves a landing rather than an edit, so `--changed` leaves it out
    # Starts only once that check has ended — also how two checks sharing `webapp/.next` stay apart.
    after: str = ""
    # Empty is its kind's prefixes; `!prefix` is everything outside one.
    reads: tuple[str, ...] = ()
    # A file it BUILDS: its verdict is kept only while that file stands.
    builds: str = ""
    # Directories it reads of what its `after` built; their content is in its key.
    product: tuple[str, ...] = ()
    # MEASURED alone, warm, over its whole process tree: busiest cores and peak memory.
    cores: int = 1
    memory_mb: int = 150
    # Returns `None` where ownership cannot be told, and then the whole check runs.
    narrow: Callable[[Sequence[str]], tuple[str, ...] | None] | None = None

    def inputs(self) -> tuple[str, ...] | None:
        if self.kind == "release":  # the answer lives on the network
            return None
        return self.reads or (_ENGINE if self.kind == "py" else _WEBAPP_READS)


def _web_modules(changed: Sequence[str]) -> tuple[str, ...] | None:
    if not all(
        path.startswith(_WEB_MODULES) and path.endswith((".ts", ".tsx")) for path in changed
    ):
        return None
    return tuple(path[len("webapp/") :] for path in changed if (_REPO / path).is_file())


def _vitest(sel: Sel) -> Outcome:
    if sel.narrowed:
        related = ("vitest", "related", "--run", "--passWithNoTests", *sel.narrowed)
        return _run(_node("npx", *related), _WEBAPP)
    return _run(_node("npm", "run", "test"), _WEBAPP)


def _pytest(sel: Sel) -> Outcome:
    # `tests_owning.py` owns the fallback: where it cannot say what owns a change, it runs every test.
    owning = [sys.executable, "scripts/tests_owning.py"]
    if sel.narrowed is not None:
        return _run([*owning, "--run", *sel.narrowed], _REPO)
    return _run([*owning, "--record"], _REPO)


_HASHED_BY_THE_RECORD = re.compile(r"(promptpotter/.*|tests/[^/]*)\.py")


def _moved_under_test(changed: Sequence[str]) -> tuple[str, ...] | None:
    # The record hashes package and suite modules itself; `_GATE` files own no test's verdict.
    return tuple(
        path for path in changed if path not in _GATE and not _HASHED_BY_THE_RECORD.fullmatch(path)
    )


_BBEH_DIR = "docs/research/bbeh-comparison"


def _mypy(_sel: Sel) -> Outcome:
    targets = ("promptpotter/", "scripts/", f"{_BBEH_DIR}/bbeh_potter_runner.py")
    # mypy checks only the `sys.platform` branch it is told; a shared cache would evict per pass.
    # The warm `.mypy_cache/` is TRUSTED: delete it only on a suspected false green against CI.
    for platform in _PLATFORMS:
        foreign = () if platform == sys.platform else ("--cache-dir", f".mypy_cache/{platform}")
        rc, out = _run(
            _py("mypy", "--platform", platform, *foreign, *targets),
            _REPO,
            MYPYPATH=str(_REPO / _BBEH_DIR),
        )
        if rc:
            return rc, f"as {platform}:\n{out}"
    return 0, ""


_PLATFORMS = ("linux", "win32")
_EXTRA_ARG = re.compile(r"--extra[ =]([\w-]+)")


def _extras(_: Sel) -> Outcome:
    declared = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    known = set(declared["project"]["optional-dependencies"])
    callers = [
        *(_REPO / ".github" / "workflows").glob("*.yml"),
        *(_REPO / "deploy-linux").glob("*.sh"),
    ]
    asked = {
        (path.name, name)
        for path in callers
        for name in _EXTRA_ARG.findall(path.read_text(encoding="utf-8"))
    }
    asked |= {("gate.py", name) for name in _PINNED_EXTRAS}
    gone = sorted(f"  {caller}: --extra {name}" for caller, name in asked if name not in known)
    if gone:
        return 1, "not an extra in pyproject.toml:\n" + "\n".join(gone)
    return 0, ""


CHECKS: tuple[Check, ...] = (
    Check("ruff-format", "py", _ruff("format", "--check"), staged=True),
    Check("ruff-check", "py", _ruff("check"), staged=True),
    Check("deptry", "py", lambda _: _run(_py("deptry", "."), _REPO), cores=2),
    Check("mypy", "py", _mypy, memory_mb=300),
    Check("extras", "py", _extras, staged=True),
    Check("layering", "py", _layering, staged=True),
    Check("help-imports", "py", _help_imports, reads=_ENGINE_RUNTIME),
    Check("handler-tables", "py", _handler_tables, reads=_ENGINE_RUNTIME),
    Check("instruction-files", "py", _instruction_files, staged=True, reads=_TREE),
    Check(
        "surface-ledger",
        "py",
        lambda _: _run([sys.executable, "scripts/complexity_ledger.py", "--check"], _REPO),
        staged=True,
        reads=(
            "promptpotter/",
            "tests/",
            "examples/",
            "scripts/complexity_ledger.py",
            "docs/specs/openapi.generated.json",
            "pyproject.toml",
        ),
    ),
    # "py" so it runs without `webapp/node_modules`, which is routinely absent.
    Check("undiffable", "py", _undiffable, staged=True, reads=_TREE),
    Check(
        "ts-types",
        "py",
        _generated("build_ts_types.py", "webapp/lib/api/types.generated.ts"),
        staged=True,
        reads=(*_ENGINE, "webapp/lib/api/types.generated.ts"),
    ),
    # "web", unlike its siblings: it reads `simple-icons` out of `webapp/node_modules`.
    Check(
        "vendor-marks",
        "web",
        _generated("build_vendor_marks.py", "webapp/components/ui/vendor-marks.generated.ts"),
        staged=True,
        reads=(*_WEBAPP_READS, "scripts/"),
    ),
    Check(
        "optimizer-schemas",
        "py",
        _generated(
            "build_optimizer_schemas.py",
            "promptpotter/assets/optimizers/potter/resolved_schemas.json",
            "promptpotter/assets/checkin/resolved_schemas.json",
        ),
        staged=True,
    ),
    Check(
        "openapi",
        "py",
        _generated("build_openapi.py", "docs/specs/openapi.generated.json"),
        staged=True,
    ),
    Check(
        "pytest",
        "py",
        _pytest,
        reads=(*_ENGINE_RUNTIME, "tests/", "scripts/tests_owning.py"),
        memory_mb=300,
        narrow=_moved_under_test,
    ),
    # The script keeps its own green under this name and these inputs, so a run by hand answers for it.
    Check(
        OFFLINE_RUN,
        "py",
        lambda _: _run(
            [sys.executable, "scripts/offline_run.py"],
            _REPO,
            PROMPTPOTTER_HOME=str(_REPO / ".gate_cache" / OFFLINE_RUN),
        ),
        landing=True,
        reads=OFFLINE_RUN_READS,
        cores=OFFLINE_CHILDREN,
        memory_mb=min(OFFLINE_CHILDREN * OFFLINE_CHILD_MB, _MEMORY_MB),
    ),
    Check("lockfile", "web", _lockfile, staged=True),
    Check("eslint", "web", _eslint, staged=True, cores=2, memory_mb=400, narrow=_web_modules),
    # tsconfig includes `.next/types`, which `next build` rewrites: concurrent, tsc reads it mid-rewrite.
    Check(
        "tsc",
        "web",
        _tsc,
        staged=True,
        after="next-build",
        product=("webapp/.next/types",),
        cores=2,
        memory_mb=500,
    ),
    Check("vitest", "web", _vitest, memory_mb=500, narrow=_web_modules),
    # Next exposes no core cap that binds Turbopack, so it is weighed at the whole box and runs alone.
    Check(
        "next-build",
        "web",
        lambda _: _run(_node("npm", "run", "build"), _WEBAPP),
        builds="webapp/out/index.html",
        cores=_CORES,
        memory_mb=1100,
    ),
    Check(
        "playwright",
        "web",
        _playwright,
        landing=True,
        after="next-build",
        product=("webapp/out",),
        reads=(*_WEBAPP_READS, *_ENGINE_RUNTIME),
        cores=8,
        memory_mb=1100,
    ),
    Check("npm-audit", "release", _npm_audit),
    Check("pip-audit", "release", _pip_audit),
    Check("advisories", "release", _advisories),
)


@dataclass(frozen=True)
class Result:
    check: Check
    rc: int
    out: str
    secs: float
    key: str | None
    kept: bool = False  # green on these inputs already, so not run
    at: float = 0.0  # seconds into the gate it started, so the table shows what overlapped


class _Green:
    """A scoped run (`--staged`, or a narrowed check) keeps nothing: it checked less than its key names."""

    def __init__(self, kept: KeptVerdicts) -> None:
        self._kept = kept

    def key(self, check: Check) -> str | None:
        prefixes = check.inputs()
        if prefixes is None:
            return None
        return self._kept.key(prefixes, check.product)

    def holds(self, check: Check, key: str | None) -> bool:
        standing = not check.builds or (_REPO / check.builds).exists()
        return key is not None and standing and self._kept.holds(check.name, key)

    def record(self, results: Sequence[Result]) -> None:
        self._kept.record({r.check.name: None if r.rc else r.key for r in results})


def _execute(check: Check, sel: Sel, green: _Green | None, started: float) -> Result:
    key = None if green is None else green.key(check)
    if green is not None and green.holds(check, key):
        return Result(check, 0, "", 0.0, key, kept=True)
    if sel.changed is not None and check.narrow is not None:
        mine = [path for path in sel.changed if reads(path, check.inputs() or ())]
        sel = dataclasses.replace(sel, narrowed=check.narrow(mine))
    began = time.monotonic()
    try:
        rc, out = check.run(sel)
    except Exception as exc:  # a broken check is a red check, never a silent skip
        rc, out = 1, f"{type(exc).__name__}: {exc}"
    if sel.narrowed is not None or (green is not None and green.key(check) != key):
        key = None  # narrowed, or edited while it ran: the verdict is for no tree anyone can name
    return Result(check, rc, out, time.monotonic() - began, key, at=began - started)


def _schedule(checks: Sequence[Check], sel: Sel, green: _Green | None) -> list[Result]:
    names = {c.name for c in checks}
    waiting = sorted(checks, key=lambda c: (c.cores, c.memory_mb), reverse=True)
    running: dict[str, Check] = {}
    done: dict[str, Result] = {}
    turn = threading.Condition()
    started = time.monotonic()

    def work(check: Check) -> None:
        result = _execute(check, sel, green, started)
        with turn:
            del running[check.name]
            done[check.name] = result
            turn.notify()

    with turn:
        while waiting or running:
            for check in [c for c in waiting if c.after not in names or c.after in done]:
                cores = sum(c.cores for c in running.values()) + check.cores
                memory = sum(c.memory_mb for c in running.values()) + check.memory_mb
                # `running and`: a check heavier than the whole budget is still admitted, alone.
                if running and (cores > _CORES or memory > _MEMORY_MB):
                    continue
                waiting.remove(check)
                running[check.name] = check
                threading.Thread(target=work, args=(check,), daemon=True).start()
            if not running:
                raise SystemExit(
                    f"gate: {len(waiting)} check(s) wait on each other — `after` describes a cycle."
                )
            turn.wait()
    return [done[c.name] for c in checks]


def _widest(results: Sequence[Result]) -> tuple[int, int, list[str]]:
    ran = [r for r in results if not r.kept]
    widest: tuple[int, int, list[str]] = (0, 0, [])
    for moment in ran:  # the peak begins where some check does
        beside = [r for r in ran if r.at <= moment.at < r.at + r.secs]
        memory = sum(r.check.memory_mb for r in beside)
        if memory > widest[1]:
            widest = (sum(r.check.cores for r in beside), memory, [r.check.name for r in beside])
    return widest


# `api`: mypy resolves fastapi. `harbor`, `dspy`: a test skipped for a missing extra never ran.
_PINNED_EXTRAS = ("dev", "api", "harbor", "dspy")


def _reexec_pinned() -> None:
    # From another interpreter, `mypy` resolves different stubs than `uv.lock` pins.
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
    # Tools print ✖ and →, which raise on a cp1252 console while REPORTING the failure.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    end_children_with_this_process()
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
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--staged", action="store_true", help="the pre-commit fast set, scoped to staged files"
    )
    scope.add_argument(
        "--changed",
        action="store_true",
        help="while iterating: the checks that read a file changed since HEAD, and inside "
        "pytest, vitest and eslint only what owns one",
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

    sel = Sel.build(args.staged, args.changed)
    if args.staged:
        empty = {"py"} if not sel.py_files else set()
        empty |= {"web"} if not sel.web_files else set()
        checks = [c for c in checks if c.kind not in empty]
    if sel.changed is not None and not args.only:
        checks = [
            c
            for c in checks
            if not c.landing and any(reads(path, c.inputs() or ()) for path in sel.changed)
        ]
    if not args.staged:
        # `--only tsc` brings `next-build` along, so it never reads a build older than its sources.
        named = {c.name: c for c in CHECKS}
        for check in list(checks):
            while check.after and named[check.after] not in checks:
                check = named[check.after]
                checks.append(check)
    if any(c.kind == "py" for c in checks):
        _reexec_pinned()

    kept_verdicts = None if args.staged else KeptVerdicts.open()
    green = None if kept_verdicts is None else _Green(kept_verdicts)
    started = time.monotonic()
    results = _schedule(checks, sel, green)
    total = time.monotonic() - started
    if green is not None:
        green.record([r for r in results if not r.kept])
    failed = [r for r in results if r.rc]
    kept = sum(r.kept for r in results)
    unchanged = f", {kept} unchanged since green" if kept else ""
    cores, memory, together = _widest(results)
    width = (
        f"; widest {'+'.join(together)} at {cores} of {_CORES} cores, "
        f"{memory / 1024:.1f} of a {_MEMORY_MB / 1024:.1f} GB budget"
        if together
        else ""
    )

    if failed or os.environ.get("GITHUB_ACTIONS"):
        for r in results:
            verdict = "FAIL" if r.rc else "kept" if r.kept else "ok"
            span = "" if r.kept else f"  from {r.at:>6.1f}s"
            print(f"{r.check.name:<20}{verdict:>5}{r.secs:>8.1f}s{span}")
    for r in failed:
        print(f"\n--- {r.check.name} ---\n{r.out}")
    label = "staged" if args.staged else "+".join(sorted(kinds)) + ("/changed" * args.changed)
    if failed:
        print(
            f"\ngate[{label}]: {len(results)} checks, {len(failed)} failed{unchanged}, "
            f"{total:.1f}s{width}"
        )
        return 1
    slowest = max(results, key=lambda r: r.secs, default=None)
    wall = (
        f" (slowest {slowest.check.name} {slowest.secs:.1f}s)" if slowest and slowest.secs else ""
    )
    print(f"gate[{label}]: {len(results)} checks green in {total:.1f}s{wall}{unchanged}{width}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
