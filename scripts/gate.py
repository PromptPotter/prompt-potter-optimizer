from __future__ import annotations

import argparse
import ast
import dataclasses
import fnmatch
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
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Literal

import tests_owning
from last_green import ENGINE_RUNTIME, OFFLINE_RUN, OFFLINE_RUN_READS, LastGreen, digest, reads
from processes import (
    REPO,
    Outcome,
    close_job,
    end_children_with_this_process,
    free_mb,
    git,
    metered,
    open_job,
    run,
)

_WEBAPP = REPO / "webapp"
_PINNED = REPO / ".venv"
_REEXEC = "PROMPTPOTTER_GATE_REEXEC"


# What one unit of each fan-out commits: the slope of its check's job peak across widths.
_OFFLINE_RUN_CHILD_MB = 108
_NEXT_WORKER_MB = 80
# Each lane's heaviest check at full width (offline-run; a cold next-build): below their sum, in turn.
_PY_LANE_MB = 1200
_WEB_LANE_MB = 3700
# A check's child inherits the reading, so one gate sizes every fan-out from one number.
_FREE_MB = "PROMPTPOTTER_GATE_FREE_MB"


@dataclass(frozen=True)
class RamBudget:
    """Free memory, read off the box once per gate: what every fan-out the gate starts is sized by."""

    free_mb: int

    @classmethod
    def read(cls) -> RamBudget:
        inherited = os.environ.get(_FREE_MB)
        return cls(int(inherited) if inherited else free_mb())

    def lanes_overlap(self) -> bool:
        return self.free_mb >= _PY_LANE_MB + _WEB_LANE_MB

    def offline_run_children(self, tasks: int) -> int:
        return max(2, min(self.free_mb // _OFFLINE_RUN_CHILD_MB, tasks, os.cpu_count() or 2))

    def next_workers(self) -> int:
        return max(1, min(self.free_mb // _NEXT_WORKER_MB, (os.cpu_count() or 2) - 1))


def _locked(fd: int) -> bool:
    # Byte 0 is the lock; the holder's name is written after it, where a waiter can read it.
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _queue_behind_siblings(label: str) -> None:
    """One gate per clone at a time, every worktree's included: an OS lock, dropped when we exit."""
    rc, common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    if rc:  # no clone, so no sibling
        return
    path = Path(common).parent / ".gate_cache" / "gate.lock"
    path.parent.mkdir(exist_ok=True)
    # Never closed: the process holds the lock to its end, whichever way it ends.
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    # No timeout: a whole gate legitimately holds it for many minutes, and a dead holder's lock
    # is gone with its process, so only a hung live gate blocks — re-named on a beat until killed.
    began = time.monotonic()
    said: tuple[str | None, float] = (None, 0.0)
    while not _locked(fd):
        with path.open("rb") as held:
            held.seek(1)
            holder = held.read().decode(errors="replace") or "a sibling gate"
        waited = time.monotonic() - began
        if holder != said[0] or waited - said[1] >= 60:
            print(f"gate: waiting on {holder} ({waited / 60:.0f} min so far)", flush=True)
            said = holder, waited
        time.sleep(2)
    os.ftruncate(fd, 1)
    os.lseek(fd, 1, os.SEEK_SET)
    since = time.strftime("%H:%M:%S")
    os.write(fd, f"gate[{label}] in {REPO}, pid {os.getpid()}, since {since}".encode())


_LINTED = (".ts", ".tsx", ".js", ".jsx", ".mjs")
# Anything else under `webapp/` is in no module graph, so it narrows nothing.
_WEB_MODULES = ("webapp/app/", "webapp/components/", "webapp/lib/")


@dataclass(frozen=True)
class Selection:
    staged: bool
    py_files: tuple[str, ...]
    web_files: tuple[str, ...]
    changed: tuple[str, ...] | None = None
    staged_paths: tuple[str, ...] = ()
    # ``None`` is the whole check.
    narrowed: tuple[str, ...] | None = None
    # The py lane's probes not kept green, asked in one host process.
    probes: tuple[_Probe, ...] = ()

    @classmethod
    def build(cls, staged: bool, changed: bool) -> Selection:
        if changed:
            _, tracked = git("diff", "--name-only", "HEAD")
            _, untracked = git("ls-files", "-o", "--exclude-standard")
            return cls(False, (), (), tuple(filter(None, (tracked + "\n" + untracked).split("\n"))))
        if not staged:
            return cls(False, (), ())
        _, out = git("diff", "--cached", "--name-status", "--diff-filter=ACMRD")
        rows = [line.split("\t") for line in out.splitlines() if line]
        # A delete or a rename's old side selects checks; only a path on disk is handed to a tool.
        names = [row[-1] for row in rows if row[0] != "D"]
        return cls(
            True,
            tuple(n for n in names if n.endswith((".py", ".ipynb"))),
            # eslint and tsc both want to run from webapp/, so the prefix goes.
            tuple(
                n[len("webapp/") :]
                for n in names
                if n.startswith("webapp/") and n.endswith(_LINTED)
            ),
            staged_paths=tuple(path for row in rows for path in row[1:]),
        )


def _py(*args: str) -> list[str]:
    return [sys.executable, "-m", *args]


def _node(exe: str, *args: str) -> list[str]:
    # Windows ships npm/npx as .cmd shims, which subprocess will not find bare.
    return [shutil.which(exe) or exe, *args]


def _snapshot(paths: Sequence[str]) -> list[bytes | None]:
    # Prior CONTENT, never the index: a model edit leaves the regenerated file right but unstaged.
    return [(REPO / p).read_bytes() if (REPO / p).exists() else None for p in paths]


def _stale(paths: Sequence[str], before: Sequence[bytes | None]) -> Outcome:
    stale = [p for p, now, was in zip(paths, _snapshot(paths), before, strict=True) if now != was]
    if stale:
        return 1, f"{', '.join(stale)} stale — regenerated in place; re-run to confirm."
    return 0, ""


def _scan(files: Sequence[Path], needle: re.Pattern[str]) -> list[str]:
    hits = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
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
# `api/` is held by the router rule alone: its deps and middleware are the Identity adapter's seam.
_PRESENTATION_IMPORTS_INFRASTRUCTURE = ("presentation/", ("infrastructure/",), "presentation/api/")
_LAYERING: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("application/", ("presentation/",), ""),
    ("shared/", ("!shared/",), ""),
    ("domain/", ("infrastructure/", "connectors/", "application/", "presentation/"), ""),
    ("infrastructure/", ("application/", "presentation/"), ""),
    # `infrastructure/` reads the connector table, so a connector importing `application/` is a cycle.
    ("connectors/", ("application/", "presentation/"), ""),
    _ROUTER_IMPORTS_INFRASTRUCTURE,
    _PRESENTATION_IMPORTS_INFRASTRUCTURE,
    _IMPORTS_POTTER,
)


def _layering(_: Selection) -> Outcome:
    package = "promptpotter/"
    imports: dict[str, list[str]] = {}
    for path in _sources(REPO / package, "*.py"):
        rel = path.relative_to(REPO).as_posix()
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


# `StrictModel` and the record adapter build on first use, so a schema that cannot build raises
# mid-run, on the first record of its kind; this builds every one the package defines.
_SCHEMA_PROBE = """
import importlib, pkgutil
import promptpotter
from promptpotter.domain.run_records import RECORD_ADAPTER
from promptpotter.domain.strict_model import StrictModel
for info in pkgutil.walk_packages(promptpotter.__path__, "promptpotter."):
    importlib.import_module(info.name)
models, todo = set(), [StrictModel]
while todo:
    fresh = set(todo.pop().__subclasses__()) - models
    models |= fresh
    todo.extend(fresh)
for model in sorted(models, key=lambda m: f"{m.__module__}.{m.__qualname__}"):
    try:
        model.model_rebuild(force=True)
    except Exception as exc:
        print(f"  {model.__module__}.{model.__qualname__}: {exc}")
RECORD_ADAPTER.rebuild(force=True)
"""


def _unbuilt_schemas(out: str) -> Outcome:
    return (1, "a model's schema does not build:\n" + out) if out.strip() else (0, "")


_ANSWERS = "probe answers: "
_PROBE_HOST = f"""
import contextlib, io, json, os, runpy, sys
answers = []
for source in json.loads(sys.argv[1]):
    out, code = io.StringIO(), 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            if source.endswith(".py"):
                sys.argv = [source]
                sys.path.insert(0, os.path.dirname(os.path.abspath(source)))
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

    def __call__(self, sel: Selection) -> Outcome:
        return _ask_together(sel.probes if self in sel.probes else (self,))[self]


@dataclass(frozen=True)
class _Hosted:
    probes: int
    secs: float
    peak_mb: int | None


# A host's peak is every probe it ran, so it is metered in its own job and printed as its own row.
_HOSTS: list[_Hosted] = []


@cache
def _ask_together(probes: tuple[_Probe, ...]) -> dict[_Probe, Outcome]:
    ordered = sorted(probes, key=lambda probe: not probe.fresh)
    before = [_snapshot(probe.regenerates) for probe in ordered]
    sources = json.dumps([probe.source for probe in ordered])
    began = time.monotonic()
    asking, metered.job = metered.job, (job := open_job())
    try:
        rc, out = run([sys.executable, "-X", "utf8", "-c", _PROBE_HOST, sources], REPO)
    finally:
        metered.job = asking
        _HOSTS.append(_Hosted(len(ordered), time.monotonic() - began, close_job(job)))
    marked = [line for line in out.splitlines() if line.startswith(_ANSWERS)]
    if not marked:
        return dict.fromkeys(ordered, (rc or 1, out))
    verdicts: dict[_Probe, Outcome] = {}
    answers = json.loads(marked[-1][len(_ANSWERS) :])
    for probe, prior, (code, text) in zip(ordered, before, answers, strict=True):
        if code:
            verdicts[probe] = code, text
        elif probe.judge is not None:
            verdicts[probe] = probe.judge(text)
        else:
            verdicts[probe] = _stale(probe.regenerates, prior)
    return verdicts


# CR is absent deliberately: `read_text` translates line endings.
_CONTROL_CHAR = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _undiffable(_: Selection) -> Outcome:
    files = [
        *_sources(REPO / "promptpotter", "*.py"),
        *_sources(REPO / "scripts", "*.py"),
        *_sources(REPO / "tests", "*.py"),
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
    code, listed = git("ls-files", "-z", *_INSTRUCTION_FILES)
    if code:
        return code, listed
    code, tracked = git("ls-files", "-z")
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
        text = (REPO / rel).read_text(encoding="utf-8")
        if (words := len(text.split())) > _INSTRUCTION_MAX_WORDS:
            over.append(f"{rel}: {words} words")
        if Path(rel).name == "CLAUDE.md":
            pages[Path(rel).parent] = len(text.encode())
        here = (REPO / rel).parent
        for claim in _path_claims(text):
            if claim.startswith("../"):
                # One that climbs out of the repo names a sibling checkout no clone can vouch for.
                target = Path(os.path.normpath(here / claim))
                if target.is_relative_to(REPO) and not target.exists():
                    unresolved[rel, claim] = set()
                continue
            on_disk = any((base / claim).exists() for base in (here, REPO))
            if not on_disk and claim.strip("/") not in tails:
                beside = f"{here.relative_to(REPO).as_posix()}/{claim}".removeprefix("./")
                unresolved[rel, claim] = {claim, beside}
    if asked := sorted(set().union(*unresolved.values())):
        code, answer = git("check-ignore", "--stdin", "-z", "--verbose", input="\0".join(asked))
        if code not in (0, 1):  # 1 is "none of them is ignored"
            return code, answer
        # `--verbose` for the PATTERN: git on Windows reports a missing directory as matched by an empty one.
        fields = answer.split("\0")  # source, line, pattern, path — per path
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
    code, hook = run([sys.executable, ".claude/hooks/shell_file_io.py", "--selftest"], REPO)
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


_CLAIM_DOCS = (
    "promptpotter/*CLAUDE.md",
    "docs/architecture.md",
    "docs/specs/*.md",
    "webapp/*CLAUDE.md",
)
# A source or spec file: `index.json::rounds` names a runtime artifact no clone holds.
_SYMBOL_CLAIM = re.compile(r"([\w./-]+\.(?:py|tsx?|ya?ml|md|toml|css))::([A-Za-z_][\w.*]*\w\*?)")
_PROBE_OPENS = re.compile(r"<!--\s*probe:")
_PROBE = re.compile(r'<!--\s*probe:\s*(git grep -cE "([^"]+)" -- (\S+) (==|<=|>=) (\d+))\s*-->')
# Git's ERE runs in bounded time except through a back-reference.
_BACKREFERENCE = re.compile(r"\\[1-9]")
_TRACKER = "docs/specs/code-debt-cleanup.md"
# An invariant a doc states as the target while its sentence says what holds today, keyed to the
# phrase the tracker's URGENT section files it under; once true, the sentence is the invariant.
_KNOWN_FALSE: dict[str, str] = {}


@cache
def _defined(path: Path) -> frozenset[str]:
    """Dotted names a module binds: its defs, classes and assignments, a class's own `self.x`.

    A class also binds what its in-file bases bind, and a union alias what its members bind.
    """
    names: set[str] = set()
    heirs: dict[str, set[str]] = {}  # an in-file class or union alias → the names it inherits from

    def scope(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.add(prefix + node.name)
                if isinstance(node, ast.ClassDef):
                    heirs[prefix + node.name] = {
                        b.id for b in node.bases if isinstance(b, ast.Name)
                    }
                    scope(node.body, f"{prefix}{node.name}.")
                    names.update(
                        f"{prefix}{node.name}.{sub.attr}"
                        for sub in ast.walk(node)
                        if isinstance(sub, ast.Attribute)
                        and isinstance(sub.ctx, ast.Store)
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"
                    )
            elif isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target if isinstance(node, ast.AnnAssign) else node.name]
                )
                names.update(
                    prefix + n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)
                )
                if not prefix and isinstance(node.value, ast.BinOp) and len(targets) == 1:
                    members = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
                    heirs.update(
                        (n.id, members) for n in ast.walk(targets[0]) if isinstance(n, ast.Name)
                    )
            elif isinstance(node, ast.If | ast.Try):
                scope(node.body, prefix)

    scope(ast.parse(path.read_text(encoding="utf-8")).body, "")
    grew = True
    while grew:
        inherited = {
            f"{heir}.{name.partition('.')[2]}"
            for heir, bases in heirs.items()
            for name in names
            if name.partition(".")[0] in bases and "." in name
        }
        grew = not inherited <= names
        names |= inherited
    return frozenset(names)


def _symbol_in(path: Path, symbol: str) -> bool:
    """A method may be cited bare (`ledger.py::iter`); outside Python, the identifier opens a word."""
    if path.suffix != ".py":
        head = symbol.split(".")[0]
        word = re.escape(head.rstrip("*")) + ("" if head.endswith("*") else r"\b")
        return bool(re.search(rf"(?<!\w){word}", path.read_text(encoding="utf-8")))
    names = _defined(path)
    tails = {name.rpartition(".")[2] for name in names}
    return any(fnmatch.fnmatchcase(n, symbol) for n in names) or symbol in tails


def _probe_count(pattern: str, where: str) -> int | str:
    """The lines *pattern* matches in the tracked files under *where*, or why it cannot be run."""
    if os.path.isabs(where) or where.startswith(("/", "\\")) or ".." in Path(where).parts:
        return f"`{where}` is outside the tree"
    if _BACKREFERENCE.search(pattern):
        return "a back-reference has no time bound"
    code, listed = git("ls-files", "--", where)
    if code or not listed:
        return f"`{where}` names no tracked file"
    code, out = git("grep", "-cE", "-e", pattern, "--", where)
    if code not in (0, 1):  # 1 is "no line matched"
        return out
    return sum(int(line.rpartition(":")[2]) for line in out.splitlines())


def _doc_claims(_: Selection) -> Outcome:
    code, claim_docs = git("ls-files", "-z", "--", *_CLAIM_DOCS)
    if code:
        return code, claim_docs
    code, every_doc = git("ls-files", "-z", "--", "*.md")
    if code:
        return code, every_doc
    failures: list[str] = []
    for rel in filter(None, claim_docs.split("\0")):
        # A cited path hangs off the doc's own directory or one above it, or off the package.
        here = (REPO / rel).parent
        bases = (here, *(d for d in here.parents if REPO in (d, *d.parents)), REPO / "promptpotter")
        prose = _FENCE.sub("", (REPO / rel).read_text(encoding="utf-8"))
        for span in _BACKTICKED.findall(prose):
            for cited, symbol in _SYMBOL_CLAIM.findall(span):
                beside = (Path(os.path.normpath(b / cited)) for b in bases)
                found = [p for p in beside if p.is_file()]
                if not found:
                    failures.append(f"  {rel}: `{cited}::{symbol}` names no file")
                elif not any(_symbol_in(p, symbol) for p in found):
                    failures.append(f"  {rel}: `{cited}::{symbol}` names nothing defined there")
    tracker = (REPO / _TRACKER).read_text(encoding="utf-8")
    urgent = " ".join(tracker.partition("\n## URGENT")[2].partition("\n## ")[0].split())
    failures += [
        f"  `_KNOWN_FALSE` files `{probe}` under {phrase!r}, which {_TRACKER} § URGENT lacks"
        for probe, phrase in _KNOWN_FALSE.items()
        if phrase not in urgent
    ]
    probed: set[str] = set()
    for rel in filter(None, every_doc.split("\0")):
        # A probe quoted in code is an example, never one to run.
        prose = _BACKTICKED.sub("", _FENCE.sub("", (REPO / rel).read_text(encoding="utf-8")))
        for opened in _PROBE_OPENS.finditer(prose):
            if not (parsed := _PROBE.match(prose, opened.start())):
                line = prose[opened.start() :].partition("\n")[0]
                failures.append(f"  {rel}: a probe that does not parse: {line}")
                continue
            probe, pattern, where, op, bound = parsed.groups()
            probed.add(probe)
            count = _probe_count(pattern, where)
            if isinstance(count, str):
                failures.append(f"  {rel}: `{probe}` cannot run: {count}")
                continue
            limit = int(bound)
            holds = {"==": count == limit, "<=": count <= limit, ">=": count >= limit}[op]
            tracked = _KNOWN_FALSE.get(probe)
            if holds and tracked:
                failures.append(
                    f"  {rel}: `{probe}` now holds, so {tracked!r} is done: the sentence states "
                    "the invariant again and the entry leaves `_KNOWN_FALSE` and the tracker"
                )
            elif not holds and not tracked:
                failures.append(
                    f"  {rel}: `{probe}` counts {count}, so the sentence beside it is false"
                )
    failures += [
        f"  `_KNOWN_FALSE` names a probe no doc carries: `{p}`"
        for p in _KNOWN_FALSE.keys() - probed
    ]
    if failures:
        return 1, "a doc claim the tree does not keep:\n" + "\n".join(failures)
    return 0, ""


# `--staged` lints staged paths verbatim, so a directory missing here is linted by the hook only.
_BBEH_DIR = "docs/research/bbeh-comparison"
_RUFF_TARGETS = ("promptpotter/", "scripts/", "tests/", "examples/", "notebooks/", _BBEH_DIR)


def _ruff(*argv: str) -> Callable[[Selection], Outcome]:
    def check(sel: Selection) -> Outcome:
        # Staged, only its config or the gate moving lints the whole tree: others' files stay theirs.
        whole = not sel.staged or any(p in ("pyproject.toml", *_GATE) for p in sel.staged_paths)
        paths = _RUFF_TARGETS if whole else sel.py_files
        if not paths:
            return 0, ""
        return run(_py("ruff", *argv, "--force-exclude", *paths), REPO)

    return check


def _tsc(_sel: Selection) -> Outcome:
    # `next build` checks neither this nor eslint: next.config sets `typescript.ignoreBuildErrors`.
    return run(_node("npm", "run", "typecheck"), _WEBAPP)


def _eslint(sel: Selection) -> Outcome:
    # `web_files` is empty outside --staged, where bare `eslint` means the whole tree.
    return run(_node("npm", "run", "lint", "--", *sel.web_files, *(sel.narrowed or ())), _WEBAPP)


def _playwright(_sel: Selection) -> Outcome:
    # Only `cold`: `walk` reads the operator's own `.promptpotter/` and `spend` costs real money.
    return run(_node("npx", "playwright", "test", "--project=cold"), _WEBAPP)


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
    return run(_node("npm", *audit), _WEBAPP)


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
        rc, out = run([*export, "--output-file", frozen], REPO)
        if rc:
            return rc, out
        ignored = [arg for vuln in _PIP_AUDIT_UNPATCHED for arg in ("--ignore-vuln", vuln)]
        # A `uv` tool, so the scanner is never a dependency of the lock it audits.
        audit = [uv, "tool", "run", "--quiet", _PIP_AUDIT, "--requirement", frozen]
        return run([*audit, "--no-deps", "--disable-pip", *ignored], REPO)


def _advisories(_: Selection) -> Outcome:
    # `publish.yml` runs the two scans above instead: its GITHUB_TOKEN cannot read Dependabot alerts.
    gh = shutil.which("gh")
    if not gh:
        return 1, (
            "`gh` is not on PATH, so the open advisories cannot be read — and a guard that "
            "cannot answer is not a guard.\nInstall the GitHub CLI, or read them on the "
            "repository's Security tab before cutting the release."
        )
    rc, out = run(
        [
            gh,
            "api",
            "repos/{owner}/{repo}/dependabot/alerts?state=open",
            "--paginate",
            "-q",
            _ALERT_FIELDS,
        ],
        REPO,
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
_GATE = ("scripts/gate.py", "scripts/last_green.py", "scripts/processes.py")
_WEBAPP_READS = ("webapp/", "tests/fixtures/", *_GATE)
_ENGINE_RUNTIME = (*ENGINE_RUNTIME, *_GATE)
_RUFF_READS = (*_RUFF_TARGETS, "pyproject.toml", *_GATE)


@dataclass(frozen=True)
class Check:
    name: str
    kind: Literal["py", "web", "release"]  # CI's `check` job, its `webapp` job, publish.yml
    run: Callable[[Selection], Outcome]
    staged: bool = False  # in the pre-commit fast set
    landing: bool = False  # proves a landing rather than an edit, so `--changed` leaves it out
    # Its tool hard-codes a worker timeout that a busy machine trips, so it runs after both lanes.
    alone: bool = False
    # The check that builds what it reads: earlier in its lane, and brought along by `--only`.
    after: str = ""
    # Empty is its kind's prefixes; `!prefix` is everything outside one. A callable is derived at
    # first use, so importing the gate parses nothing.
    reads: tuple[str, ...] | Callable[[], tuple[str, ...]] = ()
    # A file it BUILDS: its verdict is kept only while that file stands.
    builds: str = ""
    # Directories it reads of what its `after` built; their content is in its key.
    product: tuple[str, ...] = ()
    # Returns `None` where ownership cannot be told, and then the whole check runs.
    narrow: Callable[[Sequence[str]], tuple[str, ...] | None] | None = None
    # Environment variables naming a git revision its verdict reads: each one's COMMIT is in its key.
    revs: tuple[str, ...] = ()

    def inputs(self) -> tuple[str, ...] | None:
        if self.kind == "release":  # the answer lives on the network
            return None
        reads = self.reads() if callable(self.reads) else self.reads
        return reads or (_ENGINE if self.kind == "py" else _WEBAPP_READS)

    def key(self, store: LastGreen) -> str | None:
        prefixes = self.inputs()
        if prefixes is None:
            return None
        commits = []
        for name in self.revs:
            if asked := os.environ.get(name):
                code, commit = git("rev-parse", "--verify", "--quiet", f"{asked}^{{commit}}")
                if code:  # nothing to key on: the check runs and names the bad revision itself
                    return None
                commits.append(f"{name}={commit}".encode())
        return digest(store.key(prefixes, self.product).encode(), *commits).hex()

    def kept(self, store: LastGreen, key: str | None) -> bool:
        standing = not self.builds or (REPO / self.builds).exists()
        return key is not None and standing and store.holds(self.name, key)


def _web_modules(changed: Sequence[str]) -> tuple[str, ...] | None:
    if not all(
        path.startswith(_WEB_MODULES) and path.endswith((".ts", ".tsx")) for path in changed
    ):
        return None
    return tuple(path[len("webapp/") :] for path in changed if (REPO / path).is_file())


def _vitest(sel: Selection) -> Outcome:
    if sel.narrowed:
        related = ("vitest", "related", "--run", "--passWithNoTests", *sel.narrowed)
        return run(_node("npx", *related), _WEBAPP)
    return run(_node("npm", "run", "test"), _WEBAPP)


def _pytest(sel: Selection) -> Outcome:
    if sel.narrowed is None:
        return run([sys.executable, "scripts/tests_owning.py", "--record"], REPO)
    if not sel.narrowed:
        return 0, "no test owns what moved"
    return run(_py("pytest", *sel.narrowed), REPO)


@cache
def _suite_reads() -> tuple[str, ...]:
    """The suite, its runner, and every module it imports bare off pytest's `pythonpath`."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    path = config["tool"]["pytest"]["ini_options"]["pythonpath"]
    todo = sorted((REPO / "tests").glob("*.py"))
    seen: set[Path] = set()
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        rel = module.relative_to(REPO).as_posix()
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for name in tests_owning._import_names(
            tree, tests_owning._module_name(rel), False, deferred=True
        ):
            todo.extend(p for d in path if (p := REPO / d / f"{name}.py").is_file())
    imported = {p.relative_to(REPO).as_posix() for p in seen}
    beside = sorted(rel for rel in imported if not rel.startswith("tests/"))
    return (*_ENGINE_RUNTIME, "tests/", "scripts/tests_owning.py", *beside)


_HASHED_BY_THE_RECORD = re.compile(r"(promptpotter/.*|tests/[^/]*)\.py")


def _owning_tests(changed: Sequence[str]) -> tuple[str, ...] | None:
    # The record hashes package and suite modules itself; `_GATE` files own no test's verdict.
    moved = [p for p in changed if p not in _GATE and not _HASHED_BY_THE_RECORD.fullmatch(p)]
    nodes = tests_owning.tests_owning(moved)
    return None if nodes is None else tuple(nodes)


# The plug point's one out-of-package optimizer, typechecked so a contract change breaks it here.
_EXAMPLE_PLUGIN_DIR = "examples/optimizer-plugin"
_PLATFORMS = ("linux", "win32")


def _mypy(_sel: Selection) -> Outcome:
    targets = (
        "promptpotter/",
        "scripts/",
        "tests/",
        f"{_BBEH_DIR}/bbeh_potter_runner.py",
        f"{_EXAMPLE_PLUGIN_DIR}/example_optimizer/",
    )
    # mypy checks only the `sys.platform` branch it is told; a shared cache would evict per pass.
    # The warm `.mypy_cache/` is TRUSTED: delete it only on a suspected false green against CI.
    for platform in _PLATFORMS:
        foreign = () if platform == sys.platform else ("--cache-dir", f".mypy_cache/{platform}")
        rc, out = run(
            _py("mypy", "--platform", platform, *foreign, *targets),
            REPO,
            MYPYPATH=os.pathsep.join(str(REPO / d) for d in (_BBEH_DIR, _EXAMPLE_PLUGIN_DIR)),
        )
        if rc:
            return rc, f"as {platform}:\n{out}"
    return 0, ""


_EXTRA_ARG = re.compile(r"--extra[ =]([\w-]+)")


def _extras(_: Selection) -> Outcome:
    declared = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    known = set(declared["project"]["optional-dependencies"])
    callers = [
        *(REPO / ".github" / "workflows").glob("*.yml"),
        *(REPO / "deploy-linux").glob("*.sh"),
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
    Check("ruff-format", "py", _ruff("format", "--check"), staged=True, reads=_RUFF_READS),
    Check("ruff-check", "py", _ruff("check"), staged=True, reads=_RUFF_READS),
    Check("deptry", "py", lambda _: run(_py("deptry", "."), REPO)),
    Check("mypy", "py", _mypy),
    Check(
        "extras",
        "py",
        _extras,
        staged=True,
        reads=("pyproject.toml", ".github/workflows/", "deploy-linux/", *_GATE),
    ),
    Check(
        "layering",
        "py",
        _layering,
        staged=True,
        reads=("promptpotter/", "scripts/tests_owning.py", *_GATE),
    ),
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
    Check("doc-claims", "py", _doc_claims, staged=True, reads=_TREE),
    Check(
        "surface-ledger",
        "py",
        lambda _: run([sys.executable, "scripts/complexity_ledger.py", "--check"], REPO),
        staged=True,
        reads=(
            "promptpotter/",
            "tests/",
            "examples/",
            "scripts/",
            "docs/specs/",
            *(f"webapp/{root}/" for root in ("app", "components", "lib")),
            "pyproject.toml",
        ),
        revs=("ARC_BASE",),
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
        reads=(
            "scripts/build_vendor_marks.py",
            "webapp/assets/vendor-marks/",
            "webapp/components/ui/vendor-marks.generated.ts",
            "webapp/package-lock.json",
            *_GATE,
        ),
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
        "specs",
        "py",
        _Probe(
            "scripts/build_specs.py",
            ("docs/specs/openapi.generated.json", "docs/specs/events-asyncapi.generated.json"),
        ),
        staged=True,
    ),
    # Last in the host: it imports every module, which no regenerator before it may see.
    Check(
        "schema-builds",
        "py",
        _Probe(_SCHEMA_PROBE, judge=_unbuilt_schemas),
        reads=_ENGINE_RUNTIME,
    ),
    Check(
        "pytest",
        "py",
        _pytest,
        reads=_suite_reads,
        narrow=_owning_tests,
    ),
    # The script keeps its own green under this name and these inputs, so a run by hand answers for it.
    Check(
        OFFLINE_RUN,
        "py",
        lambda _: run(
            [sys.executable, "scripts/offline_run.py"],
            REPO,
            PROMPTPOTTER_HOME=str(REPO / ".gate_cache" / OFFLINE_RUN),
        ),
        landing=True,
        reads=OFFLINE_RUN_READS,
    ),
    Check("lockfile", "web", _lockfile, staged=True),
    Check("eslint", "web", _eslint, staged=True, narrow=_web_modules),
    Check("vitest", "web", _vitest, alone=True, narrow=_web_modules),
    Check(
        "next-build",
        "web",
        lambda _: run(
            _node("npm", "run", "build"),
            _WEBAPP,
            NEXT_BUILD_CPUS=str(RamBudget.read().next_workers()),
        ),
        builds="webapp/out/index.html",
    ),
    # tsconfig includes `.next/types`, which `next build` rewrites.
    Check(
        "tsc",
        "web",
        _tsc,
        staged=True,
        after="next-build",
        product=("webapp/.next/types",),
    ),
    Check(
        "playwright",
        "web",
        _playwright,
        landing=True,
        alone=True,
        after="next-build",
        product=("webapp/out",),
        reads=(*_WEBAPP_READS, *_ENGINE_RUNTIME),
    ),
    Check("npm-audit", "release", _npm_audit),
    Check("pip-audit", "release", _pip_audit),
    Check("advisories", "release", _advisories),
)
# A lane runs in this order, so what a check reads is built before it.
assert all(any(b.name == c.after for b in CHECKS[:i]) for i, c in enumerate(CHECKS) if c.after)


@dataclass(frozen=True)
class Result:
    check: Check
    rc: int
    out: str
    secs: float
    key: str | None
    kept: bool = False  # green on these inputs already, so not run
    # Every process it started, at once; `None` where no job object meters (off Windows).
    peak_mb: int | None = None


def _execute(check: Check, sel: Selection, store: LastGreen | None) -> Result:
    metered.job = job = open_job()
    try:
        result = _verdict(check, sel, store)
    finally:
        metered.job = None
        peak = close_job(job)
    return result if result.kept else dataclasses.replace(result, peak_mb=peak)


def _verdict(check: Check, sel: Selection, store: LastGreen | None) -> Result:
    began = time.monotonic()
    try:
        key = None if store is None else check.key(store)
        if store is not None and check.kept(store, key):
            return Result(check, 0, "", 0.0, key, kept=True)
        if sel.changed is not None and check.narrow is not None:
            mine = [path for path in sel.changed if reads(path, check.inputs() or ())]
            sel = dataclasses.replace(sel, narrowed=check.narrow(mine))
        rc, out = check.run(sel)
        narrowed = sel.narrowed is not None or sel.staged
        if narrowed or (store is not None and check.key(store) != key):
            key = (
                None  # narrowed, or edited while it ran: the verdict is for no tree anyone can name
            )
    except Exception as exc:  # a broken check is a red check, never a silent skip
        rc, out, key = 1, f"{type(exc).__name__}: {exc}", None
    return Result(check, rc, out, time.monotonic() - began, key)


def _lanes(
    checks: Sequence[Check], sel: Selection, store: LastGreen | None, overlap: bool
) -> list[Result]:
    """CI's jobs, each serial, side by side where memory holds both: every tool owns its own parallelism."""
    shared = [c for c in checks if not c.alone]
    lanes = [[c for c in shared if c.kind == k] for k in dict.fromkeys(c.kind for c in shared)]
    ran: list[list[Result]] = [[] for _ in lanes]

    def lane(at: int) -> None:
        ran[at] = [_execute(check, sel, store) for check in lanes[at]]

    # Daemons, so Ctrl+C ends the gate rather than waiting out each lane.
    threads = [threading.Thread(target=lane, args=(at,), daemon=True) for at in range(len(lanes))]
    for thread in threads:
        thread.start()
        if not overlap:
            thread.join()
    for thread in threads:
        thread.join()
    ran.append([_execute(check, sel, store) for check in checks if check.alone])
    return sorted((r for results in ran for r in results), key=lambda r: checks.index(r.check))


# `api`: mypy resolves fastapi. `harbor`, `dspy`: a test skipped for a missing extra never ran.
_PINNED_EXTRAS = ("dev", "api", "harbor", "dspy", "excel")
# What decides the installed env; a tool table (`[tool.ruff]`, `[tool.deptry]`) does not.
_INSTALL_KEYS = ("dependencies", "optional-dependencies", "requires-python")
_LOCK_STAMP = _PINNED / "gate-lock.sha256"


def _install_digest(extras: list[str]) -> str:
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {key: pyproject["project"].get(key) for key in _INSTALL_KEYS}
    declared["build-system"] = pyproject.get("build-system")
    return digest(
        (REPO / "uv.lock").read_bytes(),
        json.dumps(declared, sort_keys=True).encode(),
        " ".join(extras).encode(),
    ).hex()


def _editable_target() -> Path | None:
    # The tree `_PINNED`'s editable `promptpotter` imports from, read off its install record.
    sites = (*_PINNED.glob("Lib/site-packages"), *_PINNED.glob("lib/python*/site-packages"))
    dist = next(
        iter(metadata.distributions(name="promptpotter", path=[str(p) for p in sites])), None
    )
    record = dist.read_text("direct_url.json") if dist else None
    return Path.from_uri(json.loads(record)["url"]).resolve() if record else None


def _reexec_pinned() -> None:
    # From another interpreter, `mypy` resolves different stubs than `uv.lock` pins.
    if os.environ.get(_REEXEC):
        return
    # A junctioned `.venv` is its owner's: a sync here would re-point the owner's editable install.
    owner = _PINNED.resolve().parent
    extras = [arg for extra in _PINNED_EXTRAS for arg in ("--extra", extra)]
    locked = _install_digest(extras)
    bindir = _PINNED / ("Scripts" if sys.platform == "win32" else "bin")
    python = bindir / ("python.exe" if sys.platform == "win32" else "python")
    stamped = _LOCK_STAMP.exists() and _LOCK_STAMP.read_text() == locked
    target = _editable_target()
    if python.exists() and stamped and target == owner:
        if Path(sys.prefix) == _PINNED and target == REPO:
            return
    else:
        if owner != REPO:
            raise SystemExit(
                f"gate: {_PINNED} is {owner}'s venv, and the gate never syncs a venv this tree "
                f"does not own (pins {'match' if stamped else 'differ'}, editable on {target}).\n"
                f"Run the gate from {owner}, or give this tree its own venv: replace the "
                f"junction, then `uv sync --frozen --inexact {' '.join(extras)}`."
            )
        uv = shutil.which("uv")
        if not uv:
            raise SystemExit(
                "gate: `uv` is not on PATH, so the locked env is unreachable.\n"
                f"Run: uv sync --frozen --inexact {' '.join(extras)}"
            )
        if subprocess.run([uv, "sync", "--frozen", "--inexact", *extras], cwd=REPO).returncode:
            raise SystemExit("gate: `uv sync` could not build the locked env.")
        _LOCK_STAMP.write_text(locked)
    proc = subprocess.run(
        [str(python), __file__, *sys.argv[1:]],
        cwd=REPO,
        env={
            **os.environ,
            _REEXEC: "1",
            "VIRTUAL_ENV": str(_PINNED),
            # A borrowed venv's editable install names its owner; the checks read this tree.
            "PYTHONPATH": os.pathsep.join(filter(None, (str(REPO), os.environ.get("PYTHONPATH")))),
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
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        default=[],
        help="checks by name, the flag repeated or the names comma-separated",
    )
    parser.add_argument(
        "--commit-msg", metavar="FILE", help="the commit-msg hook: a subject opening on ASCII"
    )
    args = parser.parse_args()
    if args.commit_msg:
        subject = Path(args.commit_msg).read_bytes().split(b"\n", 1)[0]
        if subject[:1].isascii():
            return 0
        # A BOM (`EF BB BF`, PowerShell's `Out-File`) hides in front of the type `git log` greps.
        print(f"gate: the commit subject opens on a non-ASCII byte: {subject!r}")
        return 1

    selected = (("py", args.py), ("web", args.web), ("release", args.release))
    kinds = {k for k, on in selected if on} or {"py", "web"}
    checks = [c for c in CHECKS if c.kind in kinds and (c.staged or not args.staged)]
    if args.only:
        asked = {name for value in args.only for name in value.split(",") if name}
        if unknown := sorted(asked - {c.name for c in CHECKS}):
            have = ", ".join(c.name for c in CHECKS)
            parser.error(f"no such check: {', '.join(unknown)} (have: {have})")
        checks = [c for c in CHECKS if c.name in asked]

    sel = Selection.build(args.staged, args.changed)
    if args.staged:
        checks = [
            c for c in checks if any(reads(path, c.inputs() or ()) for path in sel.staged_paths)
        ]
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
    checks.sort(key=CHECKS.index)
    if any(c.kind == "py" for c in checks):
        _reexec_pinned()
    label = "staged" if args.staged else "+".join(sorted(kinds)) + ("/changed" * args.changed)
    # The pre-commit set is light and holds a commit open, so it never waits; one CI runner, one gate.
    if not (args.staged or os.environ.get("CI")):
        _queue_behind_siblings(label)
    budget = RamBudget.read()
    os.environ[_FREE_MB] = str(budget.free_mb)

    # `--staged` checks less than any key names, so it neither reads nor records a green.
    store = None if args.staged else LastGreen.open()
    sel = dataclasses.replace(
        sel,
        probes=tuple(
            c.run
            for c in checks
            if c.kind == "py"
            and isinstance(c.run, _Probe)
            and not (store is not None and c.kept(store, c.key(store)))
        ),
    )
    started = time.monotonic()
    # CI's configuration, never the desk's: no `.env` and no workspace, or a verdict reads either.
    with tempfile.TemporaryDirectory(prefix="pp-gate-", ignore_cleanup_errors=True) as home:
        os.environ["PROMPTPOTTER_HOME"] = home
        os.environ["PROMPTPOTTER_ENV_FILE"] = str(Path(home) / ".env")
        results = _lanes(checks, sel, store, budget.lanes_overlap())
    total = time.monotonic() - started
    if store is not None:
        store.record({r.check.name: None if r.rc else r.key for r in results if not r.kept})
    failed = [r for r in results if r.rc]
    kept = sum(r.kept for r in results)
    unchanged = f", {kept} unchanged since green" if kept else ""

    for r in results:
        if not r.kept:
            peak = f"{r.peak_mb} MB" if r.peak_mb else "-"
            verdict = "FAIL" if r.rc else "ok"
            print(f"{r.check.name:<20}{r.check.kind:<8}{verdict:>5}{r.secs:>8.1f}s{peak:>10}")
    for host in _HOSTS:
        peak = f"{host.peak_mb} MB" if host.peak_mb else "-"
        print(f"{f'({host.probes} in a probe host)':<33}{host.secs:>8.1f}s{peak:>10}")
    for r in failed:
        print(f"\n--- {r.check.name} ---\n{r.out}")
    lanes = {
        kind: sum(r.secs for r in results if r.check.kind == kind)
        for kind in {r.check.kind for r in results}
    }
    wall = ", ".join(f"{kind} {secs:.1f}s" for kind, secs in sorted(lanes.items()) if secs)
    if len(lanes) > 1:
        turns = "side by side" if budget.lanes_overlap() else "in turn"
        wall += f"; lanes {turns} at {budget.free_mb} MB free"
    timed = f"{total:.1f}s" + (f" ({wall})" if wall else "")
    if failed:
        print(
            "\nChange the failing fact where it is DECIDED, not where it shows. Green on rerun, or"
            "\nred only on this machine, is a defect in the gate or the desk, never a pass."
        )
        print(f"\ngate[{label}]: {len(results)} checks, {len(failed)} failed{unchanged}, {timed}")
        return 1
    print(f"gate[{label}]: {len(results)} checks green in {timed}{unchanged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
