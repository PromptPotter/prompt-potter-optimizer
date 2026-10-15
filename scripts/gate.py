from __future__ import annotations

import argparse
import ast
import contextlib
import ctypes
import dataclasses
import hashlib
import json
import math
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
from typing import Literal, NamedTuple

import tests_owning
from last_green import ENGINE_RUNTIME, OFFLINE_RUN, OFFLINE_RUN_READS, LastGreen, reads

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
_SYSTEM_PROCESS_INFORMATION = 5
_INFO_LENGTH_MISMATCH = 0xC0000004
_SAMPLE_SECS = 0.05


class _Basic(ctypes.Structure):
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


class _Extended(ctypes.Structure):
    _fields_ = (
        ("basic", _Basic),
        ("io_counters", ctypes.c_ulonglong * 6),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    )


class _SystemProcess(ctypes.Structure):
    """The head of ntdll's SYSTEM_PROCESS_INFORMATION, through `working_set`."""

    _fields_ = (
        ("next", ctypes.c_ulong),
        ("threads", ctypes.c_ulong),
        ("private_working_set", ctypes.c_longlong),
        ("hard_faults", ctypes.c_ulong),
        ("threads_high_watermark", ctypes.c_ulong),
        ("cycle_time", ctypes.c_ulonglong),
        ("created", ctypes.c_longlong),
        ("user_time", ctypes.c_longlong),
        ("kernel_time", ctypes.c_longlong),
        ("name_length", ctypes.c_ushort),
        ("name_capacity", ctypes.c_ushort),
        ("name", ctypes.c_void_p),
        ("priority", ctypes.c_long),
        ("pid", ctypes.c_void_p),
        ("ppid", ctypes.c_void_p),
        ("handles", ctypes.c_ulong),
        ("session", ctypes.c_ulong),
        ("key", ctypes.c_size_t),
        ("peak_virtual", ctypes.c_size_t),
        ("virtual", ctypes.c_size_t),
        ("page_faults", ctypes.c_ulong),
        ("peak_working_set", ctypes.c_size_t),
        ("working_set", ctypes.c_size_t),
    )


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    _kernel32.SetInformationJobObject.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
    )
    _kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtQuerySystemInformation.restype = ctypes.c_ulong
    _ntdll.NtQuerySystemInformation.argtypes = (
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    )


def end_children_with_this_process() -> None:
    """Windows only: POSIX reaps a foreground tree through its process group."""
    if sys.platform == "win32":
        limits = _Extended()
        limits.basic.limit_flags = _KILL_ON_JOB_CLOSE
        job = _kernel32.CreateJobObjectW(None, None)
        if not (
            job
            and _kernel32.SetInformationJobObject(
                job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            and _kernel32.AssignProcessToJobObject(job, _kernel32.GetCurrentProcess())
        ):
            raise ctypes.WinError(ctypes.get_last_error())


class _Process(NamedTuple):
    ppid: int
    # Its creation time, which tells it apart from a later process given its pid.
    born: int
    resident_bytes: int
    cpu_s: float


class _ProcessTree:
    def __init__(self, root: int) -> None:
        self._root = root
        self._born: dict[int, int] = {}
        self._buffer = ctypes.create_string_buffer(1 << 20 if sys.platform == "win32" else 0)
        self.peak_mb: int | None = None
        # Per member as last seen, so a member that exits keeps the time it spent.
        self._cpu_s: dict[tuple[int, int], float] = {}

    @property
    def cpu_s(self) -> float:
        return sum(self._cpu_s.values())

    def _list(self) -> dict[int, _Process]:
        listed: dict[int, _Process] = {}
        if sys.platform == "win32":
            size = ctypes.c_ulong()
            while True:
                status = _ntdll.NtQuerySystemInformation(
                    _SYSTEM_PROCESS_INFORMATION, self._buffer, len(self._buffer), ctypes.byref(size)
                )
                if status != _INFO_LENGTH_MISMATCH:
                    break
                # Slack for the processes born since the size was reported.
                self._buffer = ctypes.create_string_buffer(size.value + (1 << 16))
            offset = 0
            while not status:
                entry = _SystemProcess.from_buffer(self._buffer, offset)
                cpu_s = (entry.user_time + entry.kernel_time) / 1e7  # 100 ns units
                listed[entry.pid or 0] = _Process(
                    entry.ppid or 0, entry.created, entry.working_set, cpu_s
                )
                if not entry.next:
                    break
                offset += entry.next
            return listed
        page = os.sysconf("SC_PAGE_SIZE")
        ticks = os.sysconf("SC_CLK_TCK")
        try:
            pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
        except OSError:  # no procfs (macOS): no listing, so no peak
            return listed
        for pid in pids:
            try:
                stat = Path(f"/proc/{pid}/stat").read_bytes()
            except OSError:  # exited since the listing
                continue
            # From the state field on, past a command name that may hold spaces or parentheses.
            fields = stat[stat.rindex(b")") + 2 :].split()
            cpu_s = (int(fields[11]) + int(fields[12])) / ticks  # utime + stime
            listed[pid] = _Process(int(fields[1]), int(fields[19]), int(fields[21]) * page, cpu_s)
        return listed

    def _descends(self, process: _Process, listed: dict[int, _Process]) -> bool:
        # Windows keeps an exited parent's pid as the ppid: the parent is the latest born before it.
        occupant = listed.get(process.ppid)
        if occupant is not None and occupant.born <= process.born:
            return self._born.get(process.ppid) == occupant.born
        return process.ppid in self._born and self._born[process.ppid] <= process.born

    def sample(self) -> None:
        listed = self._list()
        if not self._born:
            if self._root not in listed:
                return
            self._born[self._root] = listed[self._root].born
        joined = True
        while joined:
            joined = False
            for pid, process in listed.items():
                if self._born.get(pid) != process.born and self._descends(process, listed):
                    self._born[pid] = process.born
                    joined = True
        members = [(pid, p) for pid, p in listed.items() if self._born.get(pid) == p.born]
        for pid, p in members:
            self._cpu_s[pid, p.born] = p.cpu_s
        if members:
            self.peak_mb = max(self.peak_mb or 0, sum(p.resident_bytes for _, p in members) >> 20)


def memory_budget_mb() -> int:
    return max(_free_memory_mb() - 1024, 512)


_CORES = os.cpu_count() or 2
_MEMORY_MB = memory_budget_mb()


class _Measured(threading.local):
    peak_mb: int | None = None
    cpu_s: float = 0.0


_measured = _Measured()

Outcome = tuple[int, str]

_LINTED = (".ts", ".tsx", ".js", ".jsx", ".mjs")
# Anything else under `webapp/` is in no module graph, so it narrows nothing.
_WEB_MODULES = ("webapp/app/", "webapp/components/", "webapp/lib/")


@dataclass(frozen=True)
class Selection:
    staged: bool
    py_files: tuple[str, ...]
    web_files: tuple[str, ...]
    changed: tuple[str, ...] | None = None
    # ``None`` is the whole check.
    narrowed: tuple[str, ...] | None = None

    @classmethod
    def build(cls, staged: bool, changed: bool) -> Selection:
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


class _Cost(NamedTuple):
    peak_mb: int | None
    cpu_s: float


def _measured_run(argv: Sequence[str], cwd: Path, **extra_env: str) -> tuple[Outcome, _Cost]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **extra_env}
    # Files, not pipes: the waiting thread samples, so nothing drains a pipe while it runs.
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out,
        tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as err,
    ):
        proc = subprocess.Popen(list(argv), cwd=cwd, env=env, stdout=out, stderr=err)
        tree = _ProcessTree(proc.pid)
        while proc.returncode is None:
            tree.sample()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(_SAMPLE_SECS)
        out.seek(0)
        err.seek(0)
        return (proc.returncode, (out.read() + err.read()).strip()), _Cost(tree.peak_mb, tree.cpu_s)


def _charge(cost: _Cost) -> None:
    if cost.peak_mb is not None:
        _measured.peak_mb = max(_measured.peak_mb or 0, cost.peak_mb)
    _measured.cpu_s += cost.cpu_s


def _run(argv: Sequence[str], cwd: Path, **extra_env: str) -> Outcome:
    outcome, cost = _measured_run(argv, cwd, **extra_env)
    _charge(cost)
    return outcome


def _py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def _node(exe: str, *args: str) -> list[str]:
    # Windows ships npm/npx as .cmd shims, which subprocess will not find bare.
    return [shutil.which(exe) or exe, *args]


def _snapshot(paths: Sequence[str]) -> list[bytes | None]:
    # Prior CONTENT, never the index: a model edit leaves the regenerated file right but unstaged.
    return [(_REPO / p).read_bytes() if (_REPO / p).exists() else None for p in paths]


def _stale(paths: Sequence[str], before: Sequence[bytes | None]) -> Outcome:
    stale = [p for p, now, was in zip(paths, _snapshot(paths), before, strict=True) if now != was]
    if stale:
        return 1, f"{', '.join(stale)} stale — regenerated in place; re-run to confirm."
    return 0, ""


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


def _layering(_: Selection) -> Outcome:
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


def _loaded_use_cases(out: str) -> Outcome:
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
from promptpotter.domain.command_kinds import CommandKind
from promptpotter.presentation.cli import campaign_runner
for table, package, rows in (
    ("COMMANDS", campaign_runner._COMMANDS_PACKAGE, campaign_runner.COMMANDS),
    ("CommandKind", dispatcher._HANDLER_PACKAGE, {k.value: k.handler for k in CommandKind}),
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


def _dangling_handlers(out: str) -> Outcome:
    return (1, "a handler table names what does not resolve:\n" + out) if out.strip() else (0, "")


_ANSWERS = "probe answers: "
_PROBE_HOST = f"""
import contextlib, io, json, runpy, sys
answers = []
for source in json.loads(sys.argv[1]):
    out, code = io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            if source.endswith(".py"):
                sys.argv = [source]
                runpy.run_path(source, run_name="__main__")
            else:
                exec(source, {{"__name__": "__probe__"}})
        except SystemExit as stop:
            code = stop.code if isinstance(stop.code, int) else int(stop.code is not None)
            if isinstance(stop.code, str):
                print(stop.code)
        except Exception as exc:
            code = 1
            print(f"{{type(exc).__name__}}: {{exc}}")
    answers.append((code, out.getvalue().strip()))
print({_ANSWERS!r} + json.dumps(answers))
"""


@dataclass(frozen=True)
class _Probe:
    source: str
    regenerates: tuple[str, ...] = ()
    judge: Callable[[str], Outcome] | None = None
    fresh: bool = False

    def __call__(self, _sel: Selection) -> Outcome:
        return _PROBES.answer(self)


def _ask_together(probes: Sequence[_Probe]) -> tuple[dict[_Probe, Outcome], _Cost]:
    ordered = sorted(probes, key=lambda probe: not probe.fresh)
    before = [_snapshot(probe.regenerates) for probe in ordered]
    sources = json.dumps([probe.source for probe in ordered])
    host = [sys.executable, "-X", "utf8", "-c", _PROBE_HOST, sources]
    (rc, out), cost = _measured_run(host, _REPO)
    marked = [line for line in out.splitlines() if line.startswith(_ANSWERS)]
    if not marked:
        return dict.fromkeys(ordered, (rc or 1, out)), cost
    verdicts: dict[_Probe, Outcome] = {}
    answers = json.loads(marked[-1][len(_ANSWERS) :])
    for probe, prior, (code, text) in zip(ordered, before, answers, strict=True):
        if code:
            verdicts[probe] = code, text
        elif probe.judge is not None:
            verdicts[probe] = probe.judge(text)
        else:
            verdicts[probe] = _stale(probe.regenerates, prior)
    return verdicts, cost


class _Probes:
    def __init__(self) -> None:
        self._turn = threading.Lock()
        self._due: tuple[_Probe, ...] = ()
        self._answers: dict[_Probe, Outcome] = {}

    def expect(self, due: Sequence[_Probe]) -> None:
        self._due = tuple(due)

    def answer(self, probe: _Probe) -> Outcome:
        with self._turn:
            if probe not in self._answers:
                asked = [p for p in dict.fromkeys((*self._due, probe)) if p not in self._answers]
                verdicts, cost = _ask_together(asked)
                self._answers.update(verdicts)
                # The host's cost is the asking check's alone: the others only read its answers.
                _charge(cost)
            return self._answers[probe]


_PROBES = _Probes()


# CR is absent deliberately: `read_text` translates line endings.
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _undiffable(_: Selection) -> Outcome:
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
_INSTRUCTION_CHAIN_MAX_BYTES = 72_000
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


def _instruction_files(_: Selection) -> Outcome:
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
    pages: dict[Path, int] = {}
    # Both spellings git may know a claim by: from the root, and from the file's own directory.
    unresolved: dict[tuple[str, str], set[str]] = {}
    for rel in filter(None, listed.split("\0")):
        text = (_REPO / rel).read_text(encoding="utf-8")
        if (words := len(text.split())) > _INSTRUCTION_MAX_WORDS:
            over.append(f"{rel}: {words} words")
        if Path(rel).name == "CLAUDE.md":
            pages[Path(rel).parent] = len(text.encode())
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
    # A directory's chain is every CLAUDE.md from the root down to its own — what auto-loads there.
    chains = sorted(
        (
            (sum(size for page, size in pages.items() if page in (here, *here.parents)), here)
            for here in pages
        ),
        reverse=True,
    )
    code, hook = _run([sys.executable, ".claude/hooks/shell_file_io.py", "--selftest"], _REPO)
    failures = [hook] if code else []
    if over:
        failures.append(f"over {_INSTRUCTION_MAX_WORDS} words:\n" + "\n".join(over))
    if heavy := [f"  {d.as_posix()}: {b} B" for b, d in chains if b > _INSTRUCTION_CHAIN_MAX_BYTES]:
        failures.append(
            f"a CLAUDE.md chain over {_INSTRUCTION_CHAIN_MAX_BYTES} bytes:\n" + "\n".join(heavy)
        )
    if unresolved:
        failures.append(
            "names a path that resolves nowhere in the repo:\n"
            + "\n".join(f"  {rel}: {claim}" for rel, claim in sorted(unresolved))
        )
    return (1, "\n".join(failures)) if failures else (0, "")


# `--staged` lints staged paths verbatim, so a directory missing here is linted by the hook only.
_RUFF_TARGETS = ("promptpotter/", "scripts/", "tests/", "examples/")


def _ruff(*argv: str) -> Callable[[Selection], Outcome]:
    def check(sel: Selection) -> Outcome:
        paths = sel.py_files if sel.staged else _RUFF_TARGETS
        return _run(_py("ruff", *argv, *paths), _REPO)

    return check


def _tsc(_sel: Selection) -> Outcome:
    # `next build` checks neither this nor eslint: next.config sets `typescript.ignoreBuildErrors`.
    return _run(_node("npm", "run", "typecheck"), _WEBAPP)


def _eslint(sel: Selection) -> Outcome:
    # `web_files` is empty outside --staged, where bare `eslint` means the whole tree.
    return _run(_node("npm", "run", "lint", "--", *sel.web_files, *(sel.narrowed or ())), _WEBAPP)


def _playwright(_sel: Selection) -> Outcome:
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


def _lockfile(_: Selection) -> Outcome:
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


def _npm_audit(_: Selection) -> Outcome:
    # `--audit-level` sets the exit code only; `--omit=dev` because the wheel carries no dev tooling.
    audit = ("audit", "--package-lock-only", "--omit=dev", "--audit-level=high")
    return _run(_node("npm", *audit), _WEBAPP)


# No fixed release exists to bump to; each goes the day its fix ships.
_PIP_AUDIT_UNPATCHED = (
    "PYSEC-2026-2447",  # diskcache — reached only through the `dspy` extra
)
_PIP_AUDIT = "pip-audit@2.10.1"


def _pip_audit(_: Selection) -> Outcome:
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


def _advisories(_: Selection) -> Outcome:
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


# Too narrow is the one way a kept green can lie, so widen on any doubt: `_TREE` costs only a rerun.
_TREE = ("",)
_ENGINE = ("!webapp/",)
_GATE = ("scripts/gate.py", "scripts/last_green.py")
_WEBAPP_READS = ("webapp/", "tests/fixtures/", *_GATE)
_ENGINE_RUNTIME = (*ENGINE_RUNTIME, *_GATE)


@dataclass(frozen=True)
class Check:
    name: str
    kind: Literal["py", "web", "release"]  # CI's `check` job, its `webapp` job, publish.yml
    run: Callable[[Selection], Outcome]
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
    # Peak `rss` MB above which a run that passed is red.
    ceiling_mb: int | None = None
    # Returns `None` where ownership cannot be told, and then the whole check runs.
    narrow: Callable[[Sequence[str]], tuple[str, ...] | None] | None = None

    def inputs(self) -> tuple[str, ...] | None:
        if self.kind == "release":  # the answer lives on the network
            return None
        return self.reads or (_ENGINE if self.kind == "py" else _WEBAPP_READS)

    def key(self, store: LastGreen) -> str | None:
        prefixes = self.inputs()
        return None if prefixes is None else store.key(prefixes, self.product)

    def kept(self, store: LastGreen, key: str | None) -> bool:
        standing = not self.builds or (_REPO / self.builds).exists()
        return key is not None and standing and store.holds(self.name, key)


def _web_modules(changed: Sequence[str]) -> tuple[str, ...] | None:
    if not all(
        path.startswith(_WEB_MODULES) and path.endswith((".ts", ".tsx")) for path in changed
    ):
        return None
    return tuple(path[len("webapp/") :] for path in changed if (_REPO / path).is_file())


def _vitest(sel: Selection) -> Outcome:
    if sel.narrowed:
        related = ("vitest", "related", "--run", "--passWithNoTests", *sel.narrowed)
        return _run(_node("npx", *related), _WEBAPP)
    return _run(_node("npm", "run", "test"), _WEBAPP)


def _pytest(sel: Selection) -> Outcome:
    if sel.narrowed is None:
        return _run([sys.executable, "scripts/tests_owning.py", "--record"], _REPO)
    if not sel.narrowed:
        return 0, "no test owns what moved"
    return _run(_py("pytest", "-p", "no:xdist", *sel.narrowed), _REPO)


_HASHED_BY_THE_RECORD = re.compile(r"(promptpotter/.*|tests/[^/]*)\.py")


def _owning_tests(changed: Sequence[str]) -> tuple[str, ...] | None:
    # The record hashes package and suite modules itself; `_GATE` files own no test's verdict.
    moved = [p for p in changed if p not in _GATE and not _HASHED_BY_THE_RECORD.fullmatch(p)]
    nodes = tests_owning.tests_owning(moved)
    return None if nodes is None else tuple(nodes)


_BBEH_DIR = "docs/research/bbeh-comparison"
_PLATFORMS = ("linux", "win32")


def _mypy(_sel: Selection) -> Outcome:
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


_EXTRA_ARG = re.compile(r"--extra[ =]([\w-]+)")


def _extras(_: Selection) -> Outcome:
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
    Check("deptry", "py", lambda _: _run(_py("deptry", "."), _REPO)),
    Check("mypy", "py", _mypy),
    Check("extras", "py", _extras, staged=True),
    Check("layering", "py", _layering, staged=True),
    Check(
        "help-imports",
        "py",
        _Probe(_HELP_PROBE, judge=_loaded_use_cases, fresh=True),
        reads=_ENGINE_RUNTIME,
    ),
    Check(
        "handler-tables",
        "py",
        _Probe(_HANDLER_PROBE, judge=_dangling_handlers),
        reads=_ENGINE_RUNTIME,
    ),
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
        _Probe("scripts/build_ts_types.py", ("webapp/lib/api/types.generated.ts",)),
        staged=True,
        reads=(*_ENGINE, "webapp/lib/api/types.generated.ts"),
    ),
    # "web", unlike its siblings: it reads `simple-icons` out of `webapp/node_modules`.
    Check(
        "vendor-marks",
        "web",
        _Probe(
            "scripts/build_vendor_marks.py", ("webapp/components/ui/vendor-marks.generated.ts",)
        ),
        staged=True,
        reads=(*_WEBAPP_READS, "scripts/"),
    ),
    Check(
        "optimizer-schemas",
        "py",
        _Probe(
            "scripts/build_optimizer_schemas.py",
            (
                "promptpotter/assets/optimizers/potter/resolved_schemas.json",
                "promptpotter/assets/checkin/resolved_schemas.json",
            ),
        ),
        staged=True,
    ),
    Check(
        "openapi",
        "py",
        _Probe("scripts/build_openapi.py", ("docs/specs/openapi.generated.json",)),
        staged=True,
    ),
    Check(
        "pytest",
        "py",
        _pytest,
        reads=(*_ENGINE_RUNTIME, "tests/", "scripts/tests_owning.py"),
        # A fifth above its measured peak: drift passes, a growth of one fifth does not.
        ceiling_mb=198,
        narrow=_owning_tests,
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
    ),
    Check("lockfile", "web", _lockfile, staged=True),
    Check("eslint", "web", _eslint, staged=True, narrow=_web_modules),
    # tsconfig includes `.next/types`, which `next build` rewrites: concurrent, tsc reads it mid-rewrite.
    Check(
        "tsc",
        "web",
        _tsc,
        staged=True,
        after="next-build",
        product=("webapp/.next/types",),
    ),
    Check("vitest", "web", _vitest, narrow=_web_modules),
    Check(
        "next-build",
        "web",
        lambda _: _run(_node("npm", "run", "build"), _WEBAPP),
        builds="webapp/out/index.html",
    ),
    Check(
        "playwright",
        "web",
        _playwright,
        landing=True,
        after="next-build",
        product=("webapp/out",),
        reads=(*_WEBAPP_READS, *_ENGINE_RUNTIME),
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
    # The most `rss` any one `_run` of it measured; `None` where it spawned nothing.
    peak_mb: int | None = None
    # CPU seconds its process trees spent; work done inside the gate's own process is not counted.
    cpu_s: float = 0.0
    # False when narrowed to what changed: then it measured less than the check costs.
    whole: bool = True

    @property
    def cores(self) -> float:
        return self.cpu_s / self.secs if self.secs else 0.0


class _Weight(NamedTuple):
    secs: float
    cores: float
    rss_mb: int | None


class _Weights:
    """What each check costs on THIS machine, as measured; the scheduler admits on nothing else."""

    _STORE = _REPO / ".gate_cache" / "weights.json"

    def __init__(self) -> None:
        try:
            stored = json.loads(self._STORE.read_text(encoding="utf-8"))
            self._held = {name: _Weight(**weight) for name, weight in stored.items()}
        except (OSError, ValueError, TypeError):
            self._held = {}

    def get(self, name: str) -> _Weight | None:
        return self._held.get(name)

    def record(self, results: Sequence[Result]) -> None:
        for r in results:
            if r.kept or not r.whole:
                continue
            weight = _Weight(r.secs, r.cores, r.peak_mb)
            held = self._held.get(r.check.name)
            if r.rc and held is not None:
                # A red run may stop short, so it can only raise what is held.
                rss = max(held.rss_mb or 0, weight.rss_mb or 0) or None
                weight = _Weight(held.secs, max(held.cores, weight.cores), rss)
            self._held[r.check.name] = weight
        self._STORE.parent.mkdir(exist_ok=True)
        self._STORE.write_text(
            json.dumps({n: w._asdict() for n, w in self._held.items()}, indent=1, sort_keys=True),
            encoding="utf-8",
        )


def _execute(check: Check, sel: Selection, store: LastGreen | None, started: float) -> Result:
    key = None if store is None else check.key(store)
    if store is not None and check.kept(store, key):
        return Result(check, 0, "", 0.0, key, kept=True)
    if sel.changed is not None and check.narrow is not None:
        mine = [path for path in sel.changed if reads(path, check.inputs() or ())]
        sel = dataclasses.replace(sel, narrowed=check.narrow(mine))
    began = time.monotonic()
    _measured.peak_mb = None
    _measured.cpu_s = 0.0
    try:
        rc, out = check.run(sel)
    except Exception as exc:  # a broken check is a red check, never a silent skip
        rc, out = 1, f"{type(exc).__name__}: {exc}"
    peak_mb = _measured.peak_mb
    ceiling = check.ceiling_mb
    if not rc and ceiling is not None and peak_mb is not None and peak_mb > ceiling:
        rc, out = 1, f"{out}\npeaked at {peak_mb} MB rss, over its {ceiling} MB ceiling."
    whole = sel.narrowed is None and not sel.staged
    if not whole or (store is not None and check.key(store) != key):
        key = None  # narrowed, or edited while it ran: the verdict is for no tree anyone can name
    return Result(
        check,
        rc,
        out,
        time.monotonic() - began,
        key,
        at=began - started,
        peak_mb=peak_mb,
        cpu_s=_measured.cpu_s,
        whole=whole,
    )


def _demand(weight: _Weight | None) -> tuple[int, int]:
    # Never measured here: it runs alone, and that run is its measurement.
    if weight is None:
        return _CORES, _MEMORY_MB
    return min(_CORES, max(1, math.ceil(weight.cores))), weight.rss_mb or 0


def _schedule(
    checks: Sequence[Check], sel: Selection, store: LastGreen | None, weights: _Weights
) -> list[Result]:
    names = {c.name for c in checks}
    demand = {c.name: _demand(weights.get(c.name)) for c in checks}

    def longest_first(check: Check) -> float:
        weight = weights.get(check.name)
        return -math.inf if weight is None else -weight.secs

    waiting = sorted(checks, key=longest_first)
    running: dict[str, Check] = {}
    done: dict[str, Result] = {}
    turn = threading.Condition()
    started = time.monotonic()

    def work(check: Check) -> None:
        result = _execute(check, sel, store, started)
        with turn:
            del running[check.name]
            done[check.name] = result
            turn.notify()

    with turn:
        while waiting or running:
            for check in [c for c in waiting if c.after not in names or c.after in done]:
                cores = sum(demand[name][0] for name in running) + demand[check.name][0]
                memory = sum(demand[name][1] for name in running) + demand[check.name][1]
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


def _widest(results: Sequence[Result]) -> tuple[float, int, list[str]]:
    ran = [r for r in results if not r.kept]
    widest: tuple[float, int, list[str]] = (0.0, 0, [])
    for moment in ran:  # the peak begins where some check does
        beside = [r for r in ran if r.at <= moment.at < r.at + r.secs]
        memory = sum(r.peak_mb for r in beside if r.peak_mb is not None)
        if memory > widest[1] or not widest[2]:
            widest = (sum(r.cores for r in beside), memory, [r.check.name for r in beside])
    return widest


# `api`: mypy resolves fastapi. `harbor`, `dspy`: a test skipped for a missing extra never ran.
_PINNED_EXTRAS = ("dev", "api", "harbor", "dspy", "excel")
_LOCK_INPUTS = ("pyproject.toml", "uv.lock")
_LOCK_STAMP = _PINNED / "gate-lock.sha256"


def _reexec_pinned() -> None:
    # From another interpreter, `mypy` resolves different stubs than `uv.lock` pins.
    if Path(sys.prefix) == _PINNED or os.environ.get(_REEXEC):
        return
    extras = [arg for extra in _PINNED_EXTRAS for arg in ("--extra", extra)]
    locked = hashlib.sha256(
        b"\0".join(
            [*((_REPO / name).read_bytes() for name in _LOCK_INPUTS), " ".join(extras).encode()]
        )
    ).hexdigest()
    bindir = _PINNED / ("Scripts" if sys.platform == "win32" else "bin")
    python = bindir / ("python.exe" if sys.platform == "win32" else "python")
    if not (python.exists() and _LOCK_STAMP.exists() and _LOCK_STAMP.read_text() == locked):
        uv = shutil.which("uv")
        if not uv:
            raise SystemExit(
                "gate: `uv` is not on PATH, so the locked env is unreachable.\n"
                f"Run: uv sync --frozen --inexact {' '.join(extras)}"
            )
        if subprocess.run([uv, "sync", "--frozen", "--inexact", *extras], cwd=_REPO).returncode:
            raise SystemExit("gate: `uv sync` could not build the locked env.")
        _LOCK_STAMP.write_text(locked)
    proc = subprocess.run(
        [str(python), __file__, *sys.argv[1:]],
        cwd=_REPO,
        env={
            **os.environ,
            _REEXEC: "1",
            "VIRTUAL_ENV": str(_PINNED),
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        },
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

    sel = Selection.build(args.staged, args.changed)
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

    # `--staged` checks less than any key names, so it neither reads nor records a green.
    store = None if args.staged else LastGreen.open()
    _PROBES.expect(
        [
            c.run
            for c in checks
            if isinstance(c.run, _Probe) and not (store is not None and c.kept(store, c.key(store)))
        ]
    )
    weights = _Weights()
    started = time.monotonic()
    # CI's configuration, never the desk's: no `.env` and no workspace, or a verdict reads either.
    with tempfile.TemporaryDirectory(prefix="pp-gate-", ignore_cleanup_errors=True) as home:
        os.environ["PROMPTPOTTER_HOME"] = home
        os.environ["PROMPTPOTTER_ENV_FILE"] = str(Path(home) / ".env")
        results = _schedule(checks, sel, store, weights)
    total = time.monotonic() - started
    ran = [r for r in results if not r.kept]
    weights.record(ran)
    if store is not None:
        store.record({r.check.name: None if r.rc else r.key for r in ran})
    failed = [r for r in results if r.rc]
    kept = sum(r.kept for r in results)
    unchanged = f", {kept} unchanged since green" if kept else ""
    cores, memory, together = _widest(results)
    measured = [(r.peak_mb, r.check) for r in ran if r.peak_mb is not None]
    rss = f"{memory if measured else '—'} MB rss"
    width = (
        f"; widest {'+'.join(together)} at {cores:.1f} of {_CORES} cores, "
        f"{rss} of a {_MEMORY_MB / 1024:.1f} GB budget"
        if together
        else ""
    )
    heaviest_mb, heaviest = max(measured, key=lambda m: m[0], default=(0, None))
    if heaviest and together != [heaviest.name]:
        width += f", heaviest {heaviest.name} {heaviest_mb} MB"
    width += "".join(
        f"; {check.name} peaked {peak_mb} of its {check.ceiling_mb} MB ceiling"
        for peak_mb, check in measured
        if check.ceiling_mb is not None
    )

    if failed or os.environ.get("GITHUB_ACTIONS"):
        for r in results:
            verdict = "FAIL" if r.rc else "kept" if r.kept else "ok"
            peak = "—" if r.peak_mb is None else str(r.peak_mb)
            span = "" if r.kept else f"{peak:>6} MB {r.cores:>5.1f} cores  from {r.at:>6.1f}s"
            print(f"{r.check.name:<20}{verdict:>5}{r.secs:>8.1f}s{span}")
    for r in failed:
        print(f"\n--- {r.check.name} ---\n{r.out}")
    label = "staged" if args.staged else "+".join(sorted(kinds)) + ("/changed" * args.changed)
    if failed:
        print(
            "\nBefore editing: find where the failing fact is DECIDED, not where it shows, and fix it"
            "\nthere. A check that is green on rerun, or red only on this machine, is a question"
            "\nabout the gate or the desk, never a fix."
        )
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
