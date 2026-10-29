from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import tests_owning
import yaml
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
    queue_behind_siblings,
    run,
)

logger = logging.getLogger(__name__)

_WEBAPP = REPO / "webapp"
_PINNED = REPO / ".venv"
_REEXEC = "PROMPTPOTTER_GATE_REEXEC"


# What one unit of each fan-out commits: the slope of its check's job peak across widths.
_OFFLINE_RUN_CHILD_MB = 108
_NEXT_WORKER_MB = 80
# Each lane's heaviest check at full width (offline-run; a cold eslint): below their sum, in turn.
_PY_LANE_MB = 1200
_WEB_LANE_MB = 1000
# Past these, free memory is no reason to widen: the OS kills concurrent processes on low memory.
OFFLINE_RUN_CHILDREN_MAX = 8
NEXT_WORKERS_MAX = 4


@dataclass(frozen=True)
class RamBudget:
    """Free memory, read off the box when a fan-out starts: what that fan-out is sized by."""

    free_mb: int

    @classmethod
    def read(cls) -> RamBudget:
        return cls(free_mb())

    def lanes_overlap(self) -> bool:
        return self.free_mb >= _PY_LANE_MB + _WEB_LANE_MB

    def offline_run_children(self, tasks: int) -> int:
        fit = self.free_mb // _OFFLINE_RUN_CHILD_MB
        return max(2, min(fit, tasks, os.cpu_count() or 2, OFFLINE_RUN_CHILDREN_MAX))

    def next_workers(self) -> int:
        fit = self.free_mb // _NEXT_WORKER_MB
        return max(1, min(fit, (os.cpu_count() or 2) - 1, NEXT_WORKERS_MAX))


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
    # The commit-msg hook's message: a "commit" check then reads the index, not the working tree.
    commit_msg: str | None = None

    @classmethod
    def build(cls, staged: bool, changed: bool) -> Selection:
        if changed:
            _, untracked = git("ls-files", "-o", "--exclude-standard")
            moved = [path for row in _name_status("HEAD") for path in row[1:]]
            return cls(False, (), (), (*moved, *filter(None, untracked.split("\n"))))
        if not staged:
            return cls(False, (), ())
        rows = _name_status("--cached")
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


def _name_status(against: str) -> list[list[str]]:
    """``[status, path]``, or ``[status, old, new]`` for a rename: both sides moved."""
    _, out = git("diff", against, "--name-status", "--diff-filter=ACMRD")
    return [line.split("\t") for line in out.splitlines() if line]


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


_OPTIMIZERS = REPO / "promptpotter/application/optimizers"
# Every package under `optimizers/`: one reached from outside binds the bench to an optimizer.
_IMPORTS_OPTIMIZER = (
    "",
    tuple(
        f"application/optimizers/{p.name}/"
        for p in sorted(_OPTIMIZERS.iterdir())
        if (p / "__init__.py").is_file()
    ),
    "application/optimizers/",
)
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
    _IMPORTS_OPTIMIZER,
)


def _layering(_: Selection) -> Outcome:
    package = "promptpotter/"
    imports: dict[str, list[str]] = {}
    for path in _sources(REPO / package, "*.py"):
        rel = path.relative_to(REPO).as_posix()
        reached = tests_owning.imported(
            ast.parse(path.read_text(encoding="utf-8")),
            tests_owning.module_name(rel),
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
    code, listed = _on_disk(*_INSTRUCTION_FILES)
    if code:
        return code, listed[0]
    code, tracked = _on_disk()
    if code:
        return code, tracked[0]
    tails: set[str] = set()
    for path in tracked:
        parts = path.split("/")
        for end in range(1, len(parts) + 1):
            tails.update("/".join(parts[start:end]) for start in range(end))

    over: list[str] = []
    pages: dict[Path, int] = {}
    # Both spellings git may know a claim by: from the root, and from the file's own directory.
    unresolved: dict[tuple[str, str], set[str]] = {}
    for rel in listed:
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


# A paper, a peer's write-up or released history cites code that is not ours to keep.
_CLAIM_DOCS = ("*.md", ":!paper/", ":!docs/research/", ":!CHANGELOG.md")
# A source or spec file: `index.json::rounds` names a runtime artifact no clone holds. A template
# other than `{dataset}/dataset.yaml` (`optimizers/{name}/pipeline.yaml::nodes`) names no one file.
_DATASET_TEMPLATE = "{dataset}/dataset.yaml"
_SYMBOL_CLAIM = re.compile(
    r"(?<![\w./{}-])(\{dataset\}/dataset\.yaml|[\w./-]+\.(?:py|tsx?|ya?ml|md|toml|css))"
    r"::((?:--)?[A-Za-z_][\w.*-]*[\w*])(?![\w.*-]*\{)"
)
_PROBE_OPENS = re.compile(r"<!--\s*probe:")
_PROBE = re.compile(r'<!--\s*probe:\s*(git grep -cE "([^"]+)" -- (\S+) (==|<=|>=) (\d+))\s*-->')
# Git's ERE runs in bounded time except through a back-reference.
_BACKREFERENCE = re.compile(r"\\[1-9]")
_TRACKER = "docs/specs/code-debt-cleanup.md"
# Past four counts at once the probes finish no sooner and the job's peak doubles.
_PROBE_WIDTH = 4
# An invariant a doc states as the target while its sentence says what holds today, keyed to the
# phrase the tracker's URGENT section files it under; once true, the sentence is the invariant.
_KNOWN_FALSE: dict[str, str] = {
    'git grep -cE "\\.theta - " -- promptpotter/*.py == 0': "θ differenced without `comparable_to`",
    'git grep -cE "<table" -- webapp/components/*.tsx == 0': "hand-rolled `<table>`",
    'git grep -cE "role=.dialog." -- webapp/components/*.tsx <= 1': "a hand-rolled dialog",
}


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


def _key_path(node: object, keys: Sequence[str]) -> bool:
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def _schema_admits(node: dict[str, Any], keys: Sequence[str], schemas: dict[str, Any]) -> bool:
    """An open mapping, or a value of no declared type, admits every path below it."""
    if "$ref" in node:
        return _schema_admits(schemas[node["$ref"].rpartition("/")[2]], keys, schemas)
    if "anyOf" in node:
        return any(_schema_admits(branch, keys, schemas) for branch in node["anyOf"])
    if not keys or not node:
        return True
    if keys[0] in node.get("properties", {}):
        return _schema_admits(node["properties"][keys[0]], keys[1:], schemas)
    extra = node.get("additionalProperties", False)
    return extra is True or (isinstance(extra, dict) and _schema_admits(extra, keys[1:], schemas))


@cache
def _dataset_shape() -> tuple[Any, dict[str, Any]]:
    defaults = yaml.safe_load(
        (REPO / "promptpotter/assets/datasets/defaults.yaml").read_text(encoding="utf-8")
    )
    spec = json.loads((REPO / "docs/specs/openapi.generated.json").read_text(encoding="utf-8"))
    return defaults, spec["components"]["schemas"]


def _in_any_dataset(symbol: str) -> bool:
    """A key the definition every `dataset.yaml` is laid over holds, or `campaign`'s model declares."""
    keys = symbol.split(".")
    defaults, schemas = _dataset_shape()
    return _key_path(defaults, keys) or (
        keys[0] == "campaign" and _schema_admits(schemas["CampaignConfig"], keys[1:], schemas)
    )


def _word(segment: str) -> str:
    stem = re.escape(segment.rstrip("*"))
    return rf"(?P<name>{stem}[\w-]*)" if segment.endswith("*") else rf"(?P<name>{stem})(?![\w-])"


_TS_MODIFIERS = r"(?m)^[ \t]*(?:(?:export|default|declare|abstract|async)[ \t]+)*"
_TS_DECLARES = _TS_MODIFIERS + r"(?:function\*?|const|let|class|interface|type|enum)[ \t]+"
_TS_TYPES = re.compile(_TS_MODIFIERS + r"(?:class|interface)[ \t]+\w+")
_TS_MEMBER = (
    r"(?m)^[ \t]*(?:(?:public|private|protected|readonly|static|abstract|override|async|get|set)"
    r"[ \t]+)*{}[ \t]*[?!]?[ \t]*(?:[:(<=,;]|$)"
)


def _ts_block(text: str, at: int, stops: str) -> tuple[int, int] | None:
    """The brace block opening past *at* before any of *stops*, as the span inside its braces."""
    opened = re.compile(rf"[^{{{stops}]*\{{").match(text, at)
    if opened is None:
        return None
    depth = 1
    for end in range(opened.end(), len(text)):
        depth += {"{": 1, "}": -1}.get(text[end], 0)
        if depth == 0:
            return opened.end(), end
    return None


def _ts_members(text: str, block: tuple[int, int] | None, word: str) -> list[int]:
    if block is None:
        return []
    start, end = block
    inside = text[start:end]
    return [
        start + m.end("name")
        for m in re.finditer(_TS_MEMBER.format(word), inside)
        if inside.count("{", 0, m.start()) == inside.count("}", 0, m.start())
    ]


def _ts_holds(text: str, symbol: str) -> bool:
    """A declaration, an `import { x as name }` alias or a class or interface member; each further
    segment of a dotted symbol a member of the block the one before it opens."""
    head, *rest = (_word(segment) for segment in symbol.split("."))
    held = [m.end("name") for m in re.finditer(_TS_DECLARES + head, text)]
    if not rest:
        types = (_ts_block(text, m.end(), ";") for m in _TS_TYPES.finditer(text))
        return bool(
            held
            or re.search(rf"\bimport[ \t]+(?:type[ \t]+)?\{{[^}}]*\bas\s+{head}", text)
            or any(_ts_members(text, block, head) for block in types)
        )
    stops = ";"
    for word in rest:
        blocks = [_ts_block(text, opened, stops) for opened in held]
        held = [at for block in blocks for at in _ts_members(text, block, word)]
        stops = ";,\n"
    return bool(held)


@cache
def _document(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    return tomllib.loads(text) if path.suffix == ".toml" else yaml.safe_load(text)


def _symbol_in(path: Path, symbol: str) -> bool:
    """Python: a name the module binds, a method also bare (`ledger.py::iter`). YAML and TOML: a key
    path from the document root. Markdown: a heading. CSS: a selector or a custom property.
    """
    if path.suffix == ".py":
        names = _defined(path)
        tails = {name.rpartition(".")[2] for name in names}
        return any(fnmatch.fnmatchcase(n, symbol) for n in names) or symbol in tails
    if path.suffix in (".yaml", ".yml", ".toml"):
        return _key_path(_document(path), symbol.split("."))
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".ts", ".tsx"):
        return _ts_holds(text, symbol)
    word = _word(symbol)
    if path.suffix == ".md":
        return bool(re.search(rf"(?m)^#{{1,6}}[ \t].*(?<![\w-]){word}", _FENCE.sub("", text)))
    rules = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    if symbol.startswith("--"):
        return bool(re.search(rf"(?<![\w-]){word}[ \t]*:", rules))
    return bool(re.search(rf"(?<![\w-])[.#]?{word}[^{{;}}]*\{{", rules))


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


def _on_disk(*pathspec: str) -> tuple[int, list[str]]:
    """The working tree's files: tracked ones still present, and new ones git does not ignore."""
    code, out = git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *pathspec)
    if code:
        return code, [out]
    return 0, sorted({p for p in out.split("\0") if p and (REPO / p).is_file()})


# A link, then `§ Name`: the name runs to the first punctuation (`0.5` keeps its dot) and may
# wrap a line; the prose it runs into is cut by the longest-prefix match below.
_SECTION_CLAIM = re.compile(
    r"\]\(([^)\s]+)\)\s*§[ \t]*((?:[^.,;()\[\]|·*<\s]|\.(?=\w))(?:[^.,;()\[\]|·*<]|\.(?=\w))*)"
)
_HEADING = re.compile(r"(?m)^#{1,6}[ \t]+(.+?)[ \t#]*$")


@cache
def _headings(path: Path) -> tuple[str, ...]:
    text = _FENCE.sub("", path.read_text("utf-8"))
    return tuple(h.replace("`", "") for h in _HEADING.findall(text))


def _section_claims(rel: str, prose: str) -> list[str]:
    """Each `[...](doc.md) § Name` names exactly one heading there: the longest prefix of the
    name ending on a word boundary that matches any heading must match only one."""
    failures = []
    here = (REPO / rel).parent
    for target, name in _SECTION_CLAIM.findall(prose):
        path = Path(os.path.normpath(here / target.partition("#")[0]))
        if path.suffix != ".md" or not path.is_relative_to(REPO):
            continue  # a sibling checkout's page, or a section of no markdown file
        claim = f"  {rel}: `{target}` § {' '.join(name.split())}"
        if not path.is_file():
            failures.append(f"{claim}: no such file")
            continue
        headings = _headings(path)
        words = " ".join(name.replace("`", "").split())
        cuts = [m.start() for m in re.finditer(r"(?<=\w)(?!\w)", words)]
        for cut in reversed(cuts):
            hit = re.compile(rf"(?<![\w-]){re.escape(words[:cut])}(?![\w-]|\.\w)")
            if matched := [h for h in headings if hit.search(h)]:
                if len(matched) > 1:
                    failures.append(f"{claim}: matches {len(matched)} headings: {matched}")
                break
        else:
            failures.append(f"{claim}: matches no heading")
    return failures


def _doc_claims(_: Selection) -> Outcome:
    code, claim_docs = _on_disk(*_CLAIM_DOCS)
    if code:
        return code, claim_docs[0]
    code, every_doc = _on_disk("*.md")
    if code:
        return code, every_doc[0]
    code, listed = _on_disk()
    if code:
        return code, listed[0]
    tails: dict[str, list[Path]] = {}
    for path in listed:
        parts = path.split("/")
        for i in range(len(parts)):
            tails.setdefault("/".join(parts[i:]), []).append(REPO / path)
    failures: list[str] = []
    for rel in claim_docs:
        # A cited path is relative to the doc, or the tail of ONE tracked path: `runner/round.py`.
        here = (REPO / rel).parent
        prose = _FENCE.sub("", (REPO / rel).read_text(encoding="utf-8"))
        for span in _BACKTICKED.findall(prose):
            for cited, symbol in _SYMBOL_CLAIM.findall(span):
                claim = f"  {rel}: `{cited}::{symbol}`"
                if cited == _DATASET_TEMPLATE:
                    if not _in_any_dataset(symbol):
                        failures.append(f"{claim} names neither a defaults.yaml nor a model key")
                    continue
                beside = Path(os.path.normpath(here / cited))
                found = [beside] if beside.is_file() else tails.get(cited.lstrip("/"), [])
                if not found:
                    failures.append(f"{claim} names no file")
                    continue
                if len(found) > 1:
                    failures.append(f"{claim} names {len(found)} files; qualify the path")
                    continue
                try:
                    defined = _symbol_in(found[0], symbol)
                except (yaml.YAMLError, tomllib.TOMLDecodeError) as exc:
                    failures.append(f"{claim}: unparsable: {' '.join(str(exc).split())}")
                    continue
                if not defined:
                    failures.append(f"{claim} names nothing defined there")
    tracker = (REPO / _TRACKER).read_text(encoding="utf-8")
    urgent = " ".join(tracker.partition("\n## URGENT")[2].partition("\n## ")[0].split())
    failures += [
        f"  `_KNOWN_FALSE` files `{probe}` under {phrase!r}, which {_TRACKER} § URGENT lacks"
        for probe, phrase in _KNOWN_FALSE.items()
        if phrase not in urgent
    ]
    probes: list[tuple[str, ...]] = []
    for rel in every_doc:
        unfenced = _FENCE.sub("", (REPO / rel).read_text(encoding="utf-8"))
        failures += _section_claims(rel, unfenced)
        # A probe quoted in code is an example, never one to run.
        prose = _BACKTICKED.sub("", unfenced)
        for opened in _PROBE_OPENS.finditer(prose):
            if not (parsed := _PROBE.match(prose, opened.start())):
                line = prose[opened.start() :].partition("\n")[0]
                failures.append(f"  {rel}: a probe that does not parse: {line}")
                continue
            probes.append((rel, *parsed.groups()))
    probed = {probe for _, probe, *_ in probes}
    # Each count waits on two git processes, so the counts are asked side by side, in this job.
    joined = ("job", metered.job)
    with ThreadPoolExecutor(_PROBE_WIDTH, initializer=metered.__setattr__, initargs=joined) as pool:
        counts = list(pool.map(lambda p: _probe_count(p[2], p[3]), probes))
    for (rel, probe, _pattern, _where, op, bound), count in zip(probes, counts, strict=True):
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
_BBEH_DIR = "paper/bbeh-comparison"
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
    # CI's `check` job, its `webapp` job, publish.yml, the commit-msg hook.
    kind: Literal["py", "web", "release", "commit"]
    run: Callable[[Selection], Outcome]
    # The pre-commit hook's fast set (`--staged`). The pre-push hook runs every check CI runs.
    on_commit: bool = False
    landing: bool = False  # proves a landing rather than an edit, so `--changed` leaves it out
    # Runs after both lanes, by itself: its tool hard-codes a worker timeout a busy machine trips,
    # or its peak (next-build's) is more than any lane beside it leaves free.
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

    def inputs(self) -> tuple[str, ...] | None:
        if self.kind in ("release", "commit"):  # the answer lives on the network, or in HEAD
            return None
        reads = self.reads() if callable(self.reads) else self.reads
        return reads or (_ENGINE if self.kind == "py" else _WEBAPP_READS)

    def key(self, store: LastGreen) -> str | None:
        prefixes = self.inputs()
        if prefixes is None:
            return None
        return digest(store.key(prefixes, self.product).encode()).hex()

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


def _suite_reads() -> tuple[str, ...]:
    """The suite, its runner, and every module it imports bare off pytest's `pythonpath`."""
    beside = sorted(tests_owning.bare_imports())
    return (*_ENGINE_RUNTIME, "tests/", "scripts/tests_owning.py", *beside)


def _owning_tests(_changed: Sequence[str]) -> tuple[str, ...] | None:
    # Narrowed by the tree against the last whole run's record, never by the selection's files.
    nodes = tests_owning.tests_owning()
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


# The ledger's wire shape, kept fresh by the `specs` check: what `restamp` prunes a line against.
_EVENTS_SPEC = "docs/specs/events-asyncapi.generated.json"


def _ledger_shape(text: str) -> dict[str, bool]:
    """Every key path a ledger line may carry → whether its reader requires it, walked from
    `CycleRecord`'s discriminator; a list's or a mapping's members sit at their holder's path."""
    schemas = json.loads(text)["components"]["schemas"]
    shape: dict[str, bool] = {}

    def walk(node: dict[str, Any], path: str, seen: frozenset[str]) -> None:
        if ref := node.get("$ref"):
            name = ref.rpartition("/")[2]
            if name not in seen:
                walk(schemas[name], path, seen | {name})
            return
        for branch in (*node.get("anyOf", ()), *node.get("oneOf", ()), *node.get("allOf", ())):
            walk(branch, path, seen)
        required = set(node.get("required", ()))
        for key, sub in node.get("properties", {}).items():
            shape[path + key] = shape.get(path + key, False) or key in required
            walk(sub, f"{path}{key}.", seen)
        for held in (node.get("items"), node.get("additionalProperties")):
            if isinstance(held, dict):
                walk(held, path, seen)

    for kind, ref in schemas["CycleRecord"]["discriminator"]["mapping"].items():
        shape[kind] = True
        walk({"$ref": ref}, f"{kind}.", frozenset())
    return shape


def _ledger_fields(sel: Selection) -> Outcome:
    """A commit that drops a ledger key, or requires one an older line lacks, leaves every
    campaign written before it unreadable until `restamp` runs: its body says so."""
    code, before = git("show", f"HEAD:{_EVENTS_SPEC}")
    if code:
        return code, before
    if sel.commit_msg is None:
        after = (REPO / _EVENTS_SPEC).read_text(encoding="utf-8")
    elif (shown := git("show", f":{_EVENTS_SPEC}"))[0]:
        return shown
    else:
        after = shown[1]
    was, now = _ledger_shape(before), _ledger_shape(after)
    moved = [f"  dropped {key}" for key in sorted(was.keys() - now.keys())] + [
        f"  newly required {key}"
        for key, required in sorted(now.items())
        # Under a key the older line lacks, nothing is newly missing.
        if required and not was.get(key) and key.rpartition(".")[0] in was
    ]
    body = "\n".join(
        line for line in (sel.commit_msg or "").splitlines() if not line.startswith("#")
    )
    if not moved or "restamp" in body:
        return 0, ""
    return 1, (
        "a ledger record's shape moved, so a campaign on disk written before it is unreadable "
        "until `python -m promptpotter restamp --apply` prunes a dropped key (a newly required "
        "one it cannot: its report names the campaign to delete). Name `restamp` in the commit "
        "body:\n" + "\n".join(moved)
    )


CHECKS: tuple[Check, ...] = (
    Check("ruff-format", "py", _ruff("format", "--check"), on_commit=True, reads=_RUFF_READS),
    Check("ruff-check", "py", _ruff("check"), on_commit=True, reads=_RUFF_READS),
    Check("deptry", "py", lambda _: run(_py("deptry", "."), REPO)),
    Check("mypy", "py", _mypy),
    Check(
        "extras",
        "py",
        _extras,
        on_commit=True,
        reads=("pyproject.toml", ".github/workflows/", "deploy-linux/", *_GATE),
    ),
    Check(
        "layering",
        "py",
        _layering,
        on_commit=True,
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
    Check("instruction-files", "py", _instruction_files, on_commit=True, reads=_TREE),
    Check("doc-claims", "py", _doc_claims, on_commit=True, reads=_TREE),
    Check(
        "surface-ledger",
        "py",
        lambda _: run([sys.executable, "scripts/complexity_ledger.py", "--check"], REPO),
        on_commit=True,
        reads=(
            "promptpotter/",
            "tests/",
            "scripts/",
            "docs/specs/",
            *(f"webapp/{root}/" for root in ("app", "components", "lib")),
            "pyproject.toml",
        ),
    ),
    # "py" so it runs without `webapp/node_modules`, which is routinely absent.
    Check("undiffable", "py", _undiffable, on_commit=True, reads=_TREE),
    Check(
        "ts-types",
        "py",
        _Probe("scripts/build_ts_types.py", ("webapp/lib/api/types.generated.ts",)),
        on_commit=True,
        reads=(*_ENGINE, "webapp/lib/api/types.generated.ts"),
    ),
    # "web", unlike its siblings: it reads `simple-icons` out of `webapp/node_modules`.
    Check(
        "vendor-marks",
        "web",
        _Probe(
            "scripts/build_vendor_marks.py", ("webapp/components/ui/vendor-marks.generated.ts",)
        ),
        on_commit=True,
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
        on_commit=True,
    ),
    Check(
        "specs",
        "py",
        _Probe(
            "scripts/build_specs.py",
            ("docs/specs/openapi.generated.json", "docs/specs/events-asyncapi.generated.json"),
        ),
        on_commit=True,
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
    # The script keeps a green per optimizer, keyed on the sources that optimizer reaches, so a run
    # by hand answers for it and a pushed tip reruns only the optimizers whose sources moved.
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
    Check("lockfile", "web", _lockfile, on_commit=True),
    Check("eslint", "web", _eslint, on_commit=True, narrow=_web_modules),
    Check("vitest", "web", _vitest, alone=True, narrow=_web_modules),
    Check(
        "next-build",
        "web",
        lambda _: run(
            _node("npm", "run", "build"),
            _WEBAPP,
            NEXT_BUILD_CPUS=str(RamBudget.read().next_workers()),
        ),
        alone=True,
        builds="webapp/out/index.html",
    ),
    # `npm run typecheck` runs `next typegen` first, rewriting `.next/types` and the gitignored
    # `next-env.d.ts` that imports them; the exclude in `tsconfig.json` keeps out `.next/dev`.
    Check("tsc", "web", _tsc, on_commit=True),
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
    Check("ledger-fields", "commit", _ledger_fields),
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
        logger.exception("check %s raised", check.name)
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


# What a pushed tip's checkout borrows from the pushing tree: its env, and the store its greens key in.
_BORROWED = (".venv", "webapp/node_modules", ".gate_cache")


def _link(link: Path, target: Path) -> None:
    if sys.platform == "win32":  # a junction: a directory symlink needs a privilege
        code, out = run(["cmd", "/c", "mklink", "/J", str(link), str(target)], REPO)
        if code:
            raise RuntimeError(f"mklink /J {link}: {out}")
    else:
        link.symlink_to(target, target_is_directory=True)


@contextlib.contextmanager
def _tip_checkout(tip: str) -> Iterator[Path]:
    tree = Path(tempfile.gettempdir()) / f"pp-push-{tip[:12]}-{os.getpid()}"
    code, out = git("worktree", "add", "--detach", str(tree), tip)
    if code:
        raise RuntimeError(f"git worktree add {tip}: {out}")
    (REPO / ".gate_cache").mkdir(exist_ok=True)
    links = [tree / rel for rel in _BORROWED if (REPO / rel).is_dir()]
    try:
        for link in links:
            _link(link, (REPO / link.relative_to(tree)).resolve())
        yield tree
    finally:
        # Each link goes before the tree, so no delete walks into what it points at.
        for link in links:
            if link.is_symlink() or link.is_junction():
                os.unlink(link)
        code, out = git("worktree", "remove", "-f", "-f", str(tree))
        if code:
            raise RuntimeError(f"git worktree remove {tree}: {out}")


def _prove_tips(refs: str, names: Sequence[str]) -> int:
    # *refs* is git's pre-push stdin: `<local ref> <local sha> <remote ref> <remote sha>` a line.
    # An all-zero local sha is a branch deletion: no tip to prove.
    tips = sorted({sha for line in refs.splitlines() if set(sha := line.split()[1]) != {"0"}})
    failed = 0
    for tip in tips:
        began = time.monotonic()
        with _tip_checkout(tip) as tree:
            argv = [sys.executable, "scripts/gate.py", "--only", ",".join(names)]
            rc = subprocess.run(argv, cwd=tree, stdin=subprocess.DEVNULL).returncode
        verdict = "FAIL" if rc else "green"
        print(
            f"gate[push]: {tip[:12]} {verdict} in its own checkout, {time.monotonic() - began:.1f}s"
        )
        failed += bool(rc)
    return 1 if failed else 0


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
        "--push",
        action="store_true",
        help="the pre-push hook: every CI check, in a clean checkout of each tip git names on stdin",
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
        "--commit-msg",
        metavar="FILE",
        help="the commit-msg hook: a subject opening on ASCII, and the `commit` checks",
    )
    args = parser.parse_args()
    if args.commit_msg:
        message = Path(args.commit_msg).read_bytes()
        subject = message.split(b"\n", 1)[0]
        if not subject[:1].isascii():
            # A BOM (`EF BB BF`, PowerShell's `Out-File`) hides in front of the type `git log` greps.
            print(f"gate: the commit subject opens on a non-ASCII byte: {subject!r}")
            return 1
        sel = Selection(True, (), (), commit_msg=message.decode("utf-8", errors="replace"))
        refused = 0
        for check in (c for c in CHECKS if c.kind == "commit"):
            rc, out = check.run(sel)
            if rc:
                refused += 1
                print(f"gate: {check.name}: {out}")
        return 1 if refused else 0

    selected = (("py", args.py), ("web", args.web), ("release", args.release))
    kinds = {k for k, on in selected if on} or {"py", "web"}
    checks = [c for c in CHECKS if c.kind in kinds and (c.on_commit or not args.staged)]
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
        # `--only playwright` brings `next-build` along, so it never reads a build older than its sources.
        named = {c.name: c for c in CHECKS}
        for check in list(checks):
            while check.after and named[check.after] not in checks:
                check = named[check.after]
                checks.append(check)
    checks.sort(key=CHECKS.index)
    if args.push:
        # Before any re-exec: each checkout pins its own, and a path pinned here would name this tree.
        return _prove_tips(sys.stdin.read(), [c.name for c in checks])
    if any(c.kind == "py" for c in checks):
        _reexec_pinned()
    label = "staged" if args.staged else "+".join(sorted(kinds)) + ("/changed" * args.changed)
    # The pre-commit set is light and holds a commit open, so it never waits; one CI runner, one gate.
    queue_behind_siblings(f"gate[{label}]", exempt=bool(args.staged or os.environ.get("CI")))
    budget = RamBudget.read()

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
