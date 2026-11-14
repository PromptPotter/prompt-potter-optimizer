from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import fnmatch
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import tomllib
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import yaml
from last_green import (
    ENGINE_RUNTIME,
    OFFLINE_RUN,
    OFFLINE_RUN_READS,
    LastGreen,
    digest,
    imported,
    reads,
)
from processes import (
    OFFLINE_RUN_CHILDREN_MAX,
    REPO,
    Outcome,
    RamBudget,
    alive,
    clone_cache,
    clone_root,
    close_job,
    end_children_with_this_process,
    git,
    long_path,
    metered,
    open_job,
    queue_behind_siblings,
    run,
)

logger = logging.getLogger(__name__)

_WEBAPP = REPO / "webapp"
# The clone's one venv: a worktree or a pushed tip reads it in place, so a commit there builds none.
_PINNED = clone_root() / ".venv"
_REEXEC = "PROMPTPOTTER_GATE_REEXEC"

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
    # Every check is commit-tier, so this gate runs beside a sibling's rather than queuing.
    unqueued: bool = False
    # The run `walk-world` builds and `walk` serves a copy of, under that check's key: `None` only
    # where git lists no tree to key it on.
    walk_world: Path | None = None
    # The base home `equals-main` reads the offline run against (`--against`).
    against: Path | None = None

    @property
    def world(self) -> Path:
        """The walk world, which nothing builds, prunes or walks under no key."""
        if self.walk_world is None:
            raise RuntimeError("git lists no tree, so the walk world has no key to stand under")
        return self.walk_world

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


def _rewrote(out: str) -> Outcome:
    # Every regenerator writes through `processes.py::replace_changed` and names each output on a
    # line, `wrote` where it moved and `unchanged` where it did not: a run naming none is unjudged.
    lines = out.splitlines()
    if not any(line.startswith(("wrote ", "unchanged ")) for line in lines):
        return 1, "named no output on a `wrote|unchanged` line, so nothing judged what it wrote."
    stale = [line[len("wrote ") :] for line in lines if line.startswith("wrote ")]
    return (
        (1, f"{', '.join(stale)} stale — regenerated in {REPO}; re-run to confirm.")
        if stale
        else (0, "")
    )


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
# `api/` is held by the router rule alone: its deps and middleware are the Identity adapter's seam;
# the admin bot rings the operator's channel itself (`infrastructure/outbound.py::notify_operator`).
_PRESENTATION_IMPORTS_INFRASTRUCTURE = (
    "presentation/",
    ("infrastructure/",),
    ("presentation/api/", "presentation/admin_bot.py"),
)
_LAYERING: tuple[tuple[str, tuple[str, ...], str | tuple[str, ...]], ...] = (
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
        imports[rel[len(package) :]] = sorted(target[len(package) :] for target in imported(path))
    hits = [
        f"{who or package}* must not import {' or '.join(banned)} — {source} imports {target}"
        for who, banned, exempt in _LAYERING
        for source, targets in imports.items()
        if source.startswith(who) and not (exempt and source.startswith(exempt))
        for target in targets
        if target != "__init__.py" and reads(target, banned)
    ]
    return (1, "\n".join(hits)) if hits else (0, "")


# Verbs name their handlers as strings (`main.py::COMMANDS`) so the parser loads no use case.
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
from promptpotter.presentation.cli import main as cli_main
for table, package, rows in (
    ("COMMANDS", cli_main.VERB_PACKAGE, cli_main.COMMANDS),
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


# `StrictModel` builds on first use, so a schema that cannot build raises mid-run, on the first
# record of its kind; this builds every one the package defines.
_SCHEMA_PROBE = """
import importlib, pkgutil
import promptpotter
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
"""


def _unbuilt_schemas(out: str) -> Outcome:
    return (1, "a model's schema does not build:\n" + out) if out.strip() else (0, "")


def _probe(source: str, judge: Callable[[str], Outcome]) -> Callable[[Selection], Outcome]:
    argv = [source] if source.endswith(".py") else ["-c", source]

    def check(_: Selection) -> Outcome:
        rc, out = run([sys.executable, "-X", "utf8", *argv], REPO)
        return (rc, out) if rc else judge(out)

    return check


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
    """The `pipeline` section, a key the definition every `dataset.yaml` is laid over holds, or
    `campaign`'s model declares; a node template is its connector's, not this file's."""
    keys = symbol.split(".")
    defaults, schemas = _dataset_shape()
    return (
        keys == ["pipeline"]
        or _key_path(defaults, keys)
        or (keys[0] == "campaign" and _schema_admits(schemas["CampaignConfig"], keys[1:], schemas))
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
    word = _word(symbol)
    if path.suffix == ".md":
        return bool(re.search(rf"(?m)^#{{1,6}}[ \t].*(?<![\w-]){word}", _unfenced(path)))
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".ts", ".tsx"):
        return _ts_holds(text, symbol)
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
def _unfenced(path: Path) -> str:
    return _FENCE.sub("", path.read_text("utf-8"))


@cache
def _headings(path: Path) -> tuple[str, ...]:
    return tuple(h.replace("`", "") for h in _HEADING.findall(_unfenced(path)))


def _link_target(rel: str, target: str) -> str:
    """Where *target*, a link written in the doc *rel*, lands: a repo-relative POSIX path, or
    `../…` when it leaves the repo. No filesystem is read, so a checkout's name and OS never vote."""
    cited = urllib.parse.unquote(target.partition("#")[0].partition("?")[0])
    if cited.startswith("/"):
        return posixpath.normpath(cited.lstrip("/") or ".")
    return posixpath.normpath(posixpath.join(posixpath.dirname(rel), cited)) if cited else rel


def _heading_claim(claim: str, path: Path, name: str) -> str | None:
    """*name* names exactly one heading of *path*: the longest prefix of the name ending on a word
    boundary that matches any heading must match only one."""
    headings = _headings(path)
    words = " ".join(name.replace("`", "").split())
    for cut in reversed([m.start() for m in re.finditer(r"(?<=\w)(?!\w)", words)]):
        hit = re.compile(rf"(?<![\w-]){re.escape(words[:cut])}(?![\w-]|\.\w)")
        if matched := [h for h in headings if hit.search(h)]:
            return (
                f"{claim}: matches {len(matched)} headings: {matched}" if len(matched) > 1 else None
            )
    return f"{claim}: matches no heading"


def _section_claims(rel: str, prose: str, tree: frozenset[str]) -> list[str]:
    """Each section a doc's link names is exactly one heading of the linked doc."""
    failures = []
    for target, name in _SECTION_CLAIM.findall(prose):
        at = _link_target(rel, target)
        if not at.endswith(".md") or at.startswith("../") or at not in tree:
            continue  # a sibling checkout's page, a section of no markdown file, or `_link_claims`'s
        claim = f"  {rel}: `{target}` § {' '.join(name.split())}"
        if failure := _heading_claim(claim, REPO / at, name):
            failures.append(failure)
    return failures


# Code cites a doc by the tail of its path, backticked, linked or bare: `dispatch-hub.md` § Mechanics.
_CODE_SECTION_CLAIM = re.compile(
    r"([\w./-]+\.md)`?\)?[ \t]*§[ \t]*((?:[^.,;()\[\]|·*<\s`]|\.(?=\w))(?:[^.,;()\[\]|·*<`\n]|\.(?=\w))*)"
)


def _code_section_claims(listed: Sequence[str], tails: Mapping[str, list[Path]]) -> list[str]:
    """Each doc section a source file cites names one doc, by a tail of its path, and one heading
    there: a docstring's pointer drifts like a doc's."""
    failures = []
    for rel in (p for p in listed if p.endswith(".py")):
        text = (REPO / rel).read_text(encoding="utf-8")
        for cited, name in _CODE_SECTION_CLAIM.findall(text) if "§" in text else ():
            claim = f"  {rel}: `{cited}` § {' '.join(name.split())}"
            found = tails.get("/".join(p for p in cited.split("/") if p not in ("", ".", "..")), [])
            if len(found) != 1:
                failures.append(f"{claim} names {len(found)} docs; qualify the path")
            elif failure := _heading_claim(claim, found[0], name):
                failures.append(failure)
    return failures


# A link's target: `[text](target "title")`, or a `[label]: target` definition.
_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)|^ {0,3}\[(?!\^)[^\]\n]+\]:[ \t]*<?([^\s>]+)", re.MULTILINE)
_SCHEME = re.compile(r"[A-Za-z][\w+.-]*://|mailto:")
# The checkouts beside this one a link may enter; no clone can vouch for their pages.
_SIBLING_CHECKOUTS = ("promptpotter-web", "TermNorm-excel")


@cache
def _anchors(path: Path) -> frozenset[str]:
    """GitHub's heading ids: lower-cased, punctuation dropped, spaces dashed, a repeat suffixed `-N`.
    Not modelled: emphasis or inline HTML inside a heading, setext headings, a `-N` id colliding."""
    anchors = set(re.findall(r"<a\s+(?:id|name)=\"([^\"]+)\"", _unfenced(path)))
    seen: dict[str, int] = {}
    for heading in _headings(path):
        words = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading).lower()
        slug = re.sub(r"[^\w\- ]", "", words).replace(" ", "-")
        anchors.add(f"{slug}-{seen[slug]}" if slug in seen else slug)
        seen[slug] = seen.get(slug, 0) + 1
    return frozenset(anchors)


def _link_claims(rel: str, prose: str, tree: frozenset[str]) -> list[str]:
    """Each relative link names a path in *tree* (the files git would commit, and their directories),
    and its `#anchor` a heading when it is a doc; one leaving the repo enters a sibling checkout or
    fails. Resolved repo-relative in POSIX space, so a worktree, CI and any OS agree."""
    failures = []
    for inline, defined in _LINK.findall(prose):
        target = inline or defined
        claim = f"  {rel}: `{target}`"
        if target.startswith("file:"):
            failures.append(f"{claim}: a `file:` link names one machine's disk")
            continue
        if _SCHEME.match(target):
            continue
        anchor = target.partition("#")[2]
        at = _link_target(rel, target)
        if "\\" in at:
            failures.append(f"{claim}: spells its path with `\\`, where a link takes `/`")
            continue
        if (leaves := at.split("/"))[0] == "..":
            if len(leaves) < 2 or leaves[1] not in _SIBLING_CHECKOUTS:
                failures.append(f"{claim} leaves the repo for no sibling checkout")
            continue
        if at != "." and at not in tree:
            failures.append(f"{claim} names no tracked path")
        elif anchor and at.endswith(".md") and anchor not in _anchors(REPO / at):
            failures.append(f"{claim}: no heading has the id `{anchor}`")
    return failures


# The link check's own proof, run with it: a link red (True) or green beside the root CLAUDE.md.
_LINK_PROOF = {
    "[deleted](gone.md)": True,
    "[web](../promptpotter-web/BRAND.md)": False,
    "[one ../ too many](../../promptpotter-web/BRAND.md)": True,
    "[no heading](#no-such-heading)": True,
    "[defined]: gone.md": True,
    "[out by this repo's name](../prompt-potter-optimizer/CLAUDE.md)": True,
    "[backslash](docs\\architecture.md)": True,
    "[root-relative](/CLAUDE.md)": False,
    "[root-relative, gone](/gone.md)": True,
    "[^1]: a footnote, never a link": False,
    "[query](CLAUDE.md?plain=1#no-such-heading)": True,
    "[parent](..)": True,
    "[disk](file:///C:/CLAUDE.md)": True,
}


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
    tree = set(listed)
    for path in listed:
        parts = path.split("/")
        for i in range(len(parts)):
            tails.setdefault("/".join(parts[i:]), []).append(REPO / path)
        tree.update("/".join(parts[:i]) for i in range(1, len(parts)))
    linkable = frozenset(tree)
    failures: list[str] = [
        f"  the link check calls `{link}` {'green' if red else 'red'}"
        for link, red in _LINK_PROOF.items()
        if bool(_link_claims("CLAUDE.md", link, linkable)) != red
    ]
    for rel in claim_docs:
        # A cited path is relative to the doc, or the tail of ONE tracked path: `runner/round.py`.
        here = (REPO / rel).parent
        prose = _unfenced(REPO / rel)
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
    failures += _code_section_claims(listed, tails)
    tracker = (REPO / _TRACKER).read_text(encoding="utf-8")
    urgent = " ".join(tracker.partition("\n## URGENT")[2].partition("\n## ")[0].split())
    failures += [
        f"  `_KNOWN_FALSE` files `{probe}` under {phrase!r}, which {_TRACKER} § URGENT lacks"
        for probe, phrase in _KNOWN_FALSE.items()
        if phrase not in urgent
    ]
    probes: list[tuple[str, ...]] = []
    for rel in every_doc:
        unfenced = _unfenced(REPO / rel)
        failures += _section_claims(rel, unfenced, linkable)
        # A probe or link quoted in code is an example, never one to run.
        prose = _BACKTICKED.sub("", unfenced)
        failures += _link_claims(rel, prose, linkable)
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
    # `vite build` strips types and checks none, so this is the one type check.
    return run(_node("npm", "run", "typecheck"), _WEBAPP)


def _eslint(sel: Selection) -> Outcome:
    # `web_files` is empty outside --staged, where bare `eslint` means the whole tree.
    own: tuple[str, ...] = ()
    if sel.unqueued:
        # Beside a sibling, it lints on a copy of the tree's cache: eslint rewrites it every run.
        copy = Path(os.environ["PROMPTPOTTER_HOME"]) / ".eslintcache"
        with contextlib.suppress(FileNotFoundError):
            shutil.copyfile(_WEBAPP / ".eslintcache", copy)
        own = (f"--cache-location={copy}",)
    lint = ("lint", "--", *own, *sel.web_files, *(sel.narrowed or ()))
    return run(_node("npm", "run", *lint), _WEBAPP)


def _playwright(sel: Selection) -> Outcome:
    # `walk` serves a copy of the walk world, in this gate's own home; `spend` costs real money.
    tiers = _node(
        "npx", "playwright", "test", "--project=cold", "--project=walk", "--project=touch"
    )
    walk_home = str(Path(os.environ["PROMPTPOTTER_HOME"]) / "e2e-walk")
    return run(tiers, _WEBAPP, PP_E2E_WALK_WORLD=str(sel.world), PP_E2E_WALK_HOME=walk_home)


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
_GENERATED_TYPES = "webapp/lib/api/types.generated.ts"
_GENERATED_MARKS = "webapp/components/ui/vendor-marks.generated.ts"
_WEBAPP_READS = ("webapp/", "tests/fixtures/", *_GATE)
_ENGINE_RUNTIME = (*ENGINE_RUNTIME, *_GATE)
_RUFF_READS = (*_RUFF_TARGETS, "pyproject.toml", *_GATE)


@dataclass(frozen=True)
class Check:
    name: str
    # CI's `check` job, its `webapp` job, publish.yml; `parity` is what `--against` asks.
    kind: Literal["py", "web", "release", "parity"]
    run: Callable[[Selection], Outcome]
    # What its job may commit: the schedule reserves it, and a peak past it is red on Windows,
    # the one platform a job meters.
    mb: int
    # `commit`: the pre-commit set, cheap and writing nothing a sibling gate reads, so a gate of
    # only these never queues; `landing` proves a landing, so `--changed` leaves it out.
    tier: Literal["commit", "edit", "landing"] = "edit"
    # The cores it keeps busy; the schedule never runs more beside each other than the box has.
    slots: int = 1
    # Empty is its kind's prefixes; `!prefix` is everything outside one. A product it reads is
    # named by its directory (`builds`).
    reads: tuple[str, ...] = ()
    # Tracked files it regenerates: every check reading one runs after it.
    writes: tuple[str, ...] = ()
    # Untracked products another check names by directory: a `{key}` path is the stamp a green
    # leaves, and the verdict is kept only while each stands.
    builds: tuple[str, ...] = ()
    # Returns `None` where ownership cannot be told, and then the whole check runs.
    narrow: Callable[[Sequence[str]], tuple[str, ...] | None] | None = None

    def inputs(self) -> tuple[str, ...] | None:
        if self.kind == "release":  # the answer lives on the network
            return None
        return self.reads or (_ENGINE if self.kind == "py" else _WEBAPP_READS)

    def key(self, store: LastGreen) -> str | None:
        """Its sources, and each builder's key, which stands for the product it left."""
        prefixes = self.inputs()
        # A parity's answer is the `--against` home's too, which no key covers.
        if prefixes is None or self.kind == "parity":
            return None
        made = [key for b in _builders(self, CHECKS) if (key := b.key(store)) is not None]
        return digest(store.key(prefixes).encode(), *(key.encode() for key in made)).hex()

    def kept(self, store: LastGreen, key: str | None) -> bool:
        standing = all((REPO / path.format(key=key)).exists() for path in self.builds)
        return key is not None and standing and store.holds(self.name, key)


def _product_root(path: str) -> str:
    stem = path.partition("{key}")[0]
    return stem[: max(stem.rfind("/"), stem.rfind("\\")) + 1] if "{key}" in path else path


def _builders(check: Check, among: Sequence[Check]) -> tuple[Check, ...]:
    """The checks *among* building a product *check* names by its directory: brought along by
    any query that asks for *check*, as it reads what they leave."""
    named = [prefix for prefix in check.inputs() or () if prefix and not prefix.startswith("!")]
    return tuple(
        other
        for other in among
        if other is not check
        and any(prefix.startswith(_product_root(path)) for path in other.builds for prefix in named)
    )


def _producers(check: Check, among: Sequence[Check]) -> tuple[Check, ...]:
    inputs = check.inputs() or ()
    rewriting = [
        other
        for other in among
        if other is not check and any(reads(path, inputs) for path in other.writes)
    ]
    return tuple(dict.fromkeys((*rewriting, *_builders(check, among))))


def _web_modules(changed: Sequence[str]) -> tuple[str, ...] | None:
    if not all(
        path.startswith(_WEB_MODULES) and path.endswith((".ts", ".tsx")) for path in changed
    ):
        return None
    return tuple(path[len("webapp/") :] for path in changed if (REPO / path).is_file())


# Its pool's default width is every core, past which its worker timeout trips on a busy box.
_VITEST_SLOTS = 4


def _vitest(sel: Selection) -> Outcome:
    width = f"--maxWorkers={_VITEST_SLOTS}"
    if sel.narrowed:
        related = ("vitest", "related", "--run", "--passWithNoTests", width, *sel.narrowed)
        return run(_node("npx", *related), _WEBAPP)
    return run(_node("npm", "run", "test", "--", width), _WEBAPP)


def _pytest(_: Selection) -> Outcome:
    # The cap is the suite's, never the compiler's: whatever `__pycache__` a checkout starts with,
    # every module it imports is bytecode, pytest's rewritten ones too, before the job meters it.
    job, metered.job = metered.job, None
    for warm in (("compileall", "-q", "promptpotter", "scripts"), ("pytest", "--collect-only")):
        code, out = run(_py(*warm), REPO)
        if code:
            return code, out
    metered.job = job
    return run(_py("pytest"), REPO)


# The plug point's one out-of-package optimizer.
_EXAMPLE_PLUGIN_DIR = "examples/optimizer-plugin"
_PLATFORMS = ("linux", "win32")


def _mypy(_sel: Selection) -> Outcome:
    # Its files and search path are `pyproject.toml`'s, so a bare `mypy` checks what this does.
    # mypy checks only the `sys.platform` branch it is told; a shared cache would evict per pass.
    # The warm `.mypy_cache/` is TRUSTED: delete it only on a suspected false green against CI.
    for platform in _PLATFORMS:
        foreign = () if platform == sys.platform else ("--cache-dir", f".mypy_cache/{platform}")
        rc, out = run(_py("mypy", "--platform", platform, *foreign), REPO)
        if rc:
            return rc, f"as {platform}:\n{out}"
    return 0, ""


# What the plug point must stamp on an arm the example's proposing node made.
_PLUGIN_SOURCE = "example:propose"


# Each proposed arm's source, over every round the run closed, read off its ledger; it enters
# as every process that reads a ledger does.
_PLUGIN_SOURCES_PROBE = """
from promptpotter.application.maintenance.landing import land_at_entry
from promptpotter.config.paths import default_projects_root
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.identity import default_identity
land_at_entry(every_treatment=False)
campaigns = build_stores(default_identity(), projects_root=default_projects_root()).campaigns
for campaign_id in campaigns.list_campaign_ids():
    hop = campaigns.load_campaign(campaign_id).root_hop
    for stood in campaigns.standing_rounds(hop, moment=None).rounds.values():
        for arm in stood.close.panel.values():
            print(arm.individual.lineage.source)
"""


def _optimizer_plugin(_: Selection) -> Outcome:
    """The example installed as a distribution of its own, into a scratch venv layered on this
    one, then run offline: the plug point holds only if its arms carry the example's source."""
    uv = shutil.which("uv")
    if not uv:
        return 1, "`uv` is not on PATH, so the example plugin cannot be installed"
    with tempfile.TemporaryDirectory(prefix="pp-plugin-") as scratch:
        venv = Path(scratch) / "venv"
        code, out = run([sys.executable, "-m", "venv", "--without-pip", str(venv)], REPO)
        if code:
            return code, out
        where = {"base": str(venv), "platbase": str(venv)}
        site = Path(sysconfig.get_path("purelib", "venv", where))
        layered = f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n"
        (site / "_layered.pth").write_text(layered, encoding="utf-8")
        python = Path(sysconfig.get_path("scripts", "venv", where)) / Path(sys.executable).name
        # A copy: setuptools builds inside the directory it installs from.
        plugin = shutil.copytree(REPO / _EXAMPLE_PLUGIN_DIR, Path(scratch) / "plugin")
        install = [uv, "pip", "install", "--python", str(python), "--no-deps", str(plugin)]
        code, out = run(install, REPO)
        if code:
            return code, out
        home = Path(scratch) / "home"
        argv = [str(python), "scripts/offline_run.py", "--optimizer", "example", "--straight"]
        code, out = run(argv, REPO, PROMPTPOTTER_HOME=str(home))
        if code:
            return code, out
        # `offline_run.py` homes each optimizer's run under its own name, its data root inside.
        probe = [str(python), "-c", _PLUGIN_SOURCES_PROBE]
        code, read = run(probe, REPO, PROMPTPOTTER_HOME=str(home / "example" / "data"))
        if code:
            return code, read
        if _PLUGIN_SOURCE not in (sources := set(read.split())):
            return (
                1,
                f"no arm carries source {_PLUGIN_SOURCE!r}; arms carry {sorted(sources)}\n{out}",
            )
    return 0, out


# What the `walk` tier serves a copy of: potter's straight run's data root
# (`offline_run.py::data_root`), an L4 campaign over inner runs nested in it (`--l4`), in a home of its own under the `walk-world` check's key, so a world is read only by a tree whose engine built it. One world
# stands: building one removes every other entry of `walk-world/`, under the clone's lock, which
# a walk's copy of its world takes too (`webapp/e2e/walk_world.py`), so no world goes while a tree
# copies it; a tree whose world went builds its own again (`Check.kept` reads `builds`). Spelled
# long, so its prune and its copy reach a ledger lock nested past MAX_PATH under a desk checkout.
_WALK_WORLDS = Path(long_path(clone_cache() / "walk-world"))
_WALK_WORLD = str(_WALK_WORLDS / "{key}" / "potter" / "data")


def _walk_world(sel: Selection) -> Outcome:
    home = sel.world.parent.parent
    for other in _WALK_WORLDS.iterdir() if _WALK_WORLDS.is_dir() else ():
        if other.is_dir() and other != home:
            shutil.rmtree(other)
        elif other.is_file():
            other.unlink()
    argv = [sys.executable, "scripts/offline_run.py", "--optimizer=potter", "--straight", "--l4"]
    return run(argv, REPO, PROMPTPOTTER_HOME=str(home))


# The clone's homes, so a worktree's run answers for main and the worktree deletes clean. One
# home per leg: a home stamped for other arguments is rewritten whole.
_OFFLINE_HOME = clone_cache() / OFFLINE_RUN
_CONTROLLED = f"{OFFLINE_RUN}-controlled"


def _offline_leg(home: Path, *flags: str) -> Callable[[Selection], Outcome]:
    def check(_: Selection) -> Outcome:
        argv = [sys.executable, "scripts/offline_run.py", *flags]
        return run(argv, REPO, PROMPTPOTTER_HOME=str(home))

    return check


def _equals_main(sel: Selection) -> Outcome:
    argv = [sys.executable, "scripts/offline_run.py", f"--against={sel.against}"]
    code, out = run(argv, REPO, PROMPTPOTTER_HOME=str(_OFFLINE_HOME))
    compared = [line for line in out.splitlines() if ": against " in line]
    if code or not compared or any(not line.endswith(": UNMOVED") for line in compared):
        return code or 1, out
    return 0, ""


_EXTRA_ARG = re.compile(r"--extra[ =]([\w-]+)")
# The extras code asks for by name (`require_extra`): each must be one pyproject.toml declares.
_EXTRA_ENUM = REPO / "promptpotter" / "shared" / "extras.py"


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
    asked |= {
        ("extras.py::Extra", member.value.value)
        for cls in ast.parse(_EXTRA_ENUM.read_text(encoding="utf-8")).body
        if isinstance(cls, ast.ClassDef) and cls.name == "Extra"
        for member in cls.body
        if isinstance(member, ast.Assign) and isinstance(member.value, ast.Constant)
    }
    gone = sorted(f"  {caller}: {name}" for caller, name in asked if name not in known)
    if gone:
        return 1, "not an extra in pyproject.toml:\n" + "\n".join(gone)
    return 0, ""


_CORES = os.cpu_count() or 1

# Each `mb` is the job peak measured on a cold cache (after `#`) plus about a quarter; a check
# at 0 spawns no process.
CHECKS: tuple[Check, ...] = (
    Check(
        "ruff-format",
        "py",
        _ruff("format", "--check"),
        mb=185,  # 146
        tier="commit",
        reads=_RUFF_READS,
    ),
    Check("ruff-check", "py", _ruff("check"), mb=235, tier="commit", reads=_RUFF_READS),  # 186
    Check("deptry", "py", lambda _: run(_py("deptry", "."), REPO), mb=50),  # 37
    Check("mypy", "py", _mypy, mb=715),  # 570
    Check(
        "extras",
        "py",
        _extras,
        mb=0,
        tier="commit",
        reads=(
            "pyproject.toml",
            ".github/workflows/",
            "deploy-linux/",
            "promptpotter/shared/extras.py",
            *_GATE,
        ),
    ),
    Check("layering", "py", _layering, mb=0, tier="commit", reads=("promptpotter/", *_GATE)),
    Check(
        "help-imports",
        "py",
        _probe(_HELP_PROBE, _loaded_use_cases),
        mb=55,  # 43
        reads=_ENGINE_RUNTIME,
    ),
    Check(
        "handler-tables",
        "py",
        _probe(_HANDLER_PROBE, _dangling_handlers),
        mb=115,  # 92
        reads=_ENGINE_RUNTIME,
    ),
    Check("instruction-files", "py", _instruction_files, mb=45, tier="commit", reads=_TREE),  # 33
    Check("doc-claims", "py", _doc_claims, mb=270, tier="commit", reads=_TREE),  # 215
    Check(
        "surface-ledger",
        "py",
        lambda _: run([sys.executable, "scripts/complexity_ledger.py", "--check"], REPO),
        mb=250,  # 200
        tier="commit",
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
    Check("undiffable", "py", _undiffable, mb=0, tier="commit", reads=_TREE),
    # A regenerator reads what it imports and its own output, so none reads another's: no cycle.
    Check(
        "ts-types",
        "py",
        _probe("scripts/build_ts_types.py", _rewrote),
        mb=190,  # 153
        tier="commit",
        reads=(*_ENGINE_RUNTIME, "scripts/build_ts_types.py", _GENERATED_TYPES),
        writes=(_GENERATED_TYPES,),
    ),
    # "web", unlike its siblings: it reads `simple-icons` out of `webapp/node_modules`.
    Check(
        "vendor-marks",
        "web",
        _probe("scripts/build_vendor_marks.py", _rewrote),
        mb=15,  # 12
        tier="commit",
        reads=(
            "scripts/build_vendor_marks.py",
            "webapp/assets/vendor-marks/",
            _GENERATED_MARKS,
            "webapp/package-lock.json",
            *_GATE,
        ),
        writes=(_GENERATED_MARKS,),
    ),
    Check(
        "optimizer-schemas",
        "py",
        _probe("scripts/build_optimizer_schemas.py", _rewrote),
        mb=80,  # 65
        tier="commit",
        reads=(
            *_ENGINE_RUNTIME,
            "scripts/build_optimizer_schemas.py",
            f"{_EXAMPLE_PLUGIN_DIR}/",
        ),
        writes=(
            "promptpotter/assets/optimizers/",
            "promptpotter/assets/checkin/",
            f"{_EXAMPLE_PLUGIN_DIR}/example_optimizer/resolved_schemas.json",
        ),
    ),
    Check(
        "specs",
        "py",
        _probe("scripts/build_specs.py", _rewrote),
        mb=165,  # 131
        tier="commit",
        reads=(*_ENGINE_RUNTIME, "scripts/build_specs.py", "docs/specs/"),
        writes=("docs/specs/",),
    ),
    Check(
        "schema-builds",
        "py",
        _probe(_SCHEMA_PROBE, _unbuilt_schemas),
        mb=230,  # 182
        reads=_ENGINE_RUNTIME,
    ),
    Check(
        "pytest",
        "py",
        _pytest,
        mb=375,  # 38 MB above a measured warm 337 MB: a 40 MB growth is red
        # Every script, as the suite imports them bare off pytest's `pythonpath`.
        reads=(*ENGINE_RUNTIME, "tests/", "scripts/"),
    ),
    # The script keeps a green per optimizer, keyed on the sources that optimizer reaches, so a run
    # by hand answers for it and a pushed tip reruns only the optimizers whose sources moved. It
    # regenerates `behaviour_keys.json` and is red where it moved; that file is no `writes`, which
    # would queue every engine reader behind this landing check.
    Check(
        OFFLINE_RUN,
        "py",
        _offline_leg(_OFFLINE_HOME),
        mb=1270,  # 1017
        tier="landing",
        slots=min(OFFLINE_RUN_CHILDREN_MAX, _CORES),
        reads=(*OFFLINE_RUN_READS, *_GATE),
        builds=(str(_OFFLINE_HOME),),
    ),
    Check(
        _CONTROLLED,
        "py",
        _offline_leg(clone_cache() / _CONTROLLED, "--controlled"),
        mb=460,  # 368
        tier="landing",
        slots=2,  # its two workspaces
        reads=(*OFFLINE_RUN_READS, *_GATE),
    ),
    # "py" as it runs the engine: a `--web` gate then runs in the pinned venv `walk` serves from.
    Check(
        "walk-world",
        "py",
        _walk_world,
        mb=300,  # 237
        tier="landing",
        reads=(*OFFLINE_RUN_READS, *_GATE),
        builds=(_WALK_WORLD,),
    ),
    Check(
        "optimizer-plugin",
        "py",
        _optimizer_plugin,
        mb=275,  # 218
        tier="landing",
        reads=(*OFFLINE_RUN_READS, f"{_EXAMPLE_PLUGIN_DIR}/", *_GATE),
    ),
    Check(
        "equals-main",
        "parity",
        _equals_main,
        mb=175,  # 140
        tier="landing",
        reads=(str(_OFFLINE_HOME),),
    ),
    Check("lockfile", "web", _lockfile, mb=0, tier="commit"),
    Check("eslint", "web", _eslint, mb=2135, tier="commit", narrow=_web_modules),  # 1706
    Check("vitest", "web", _vitest, mb=735, slots=_VITEST_SLOTS, narrow=_web_modules),  # 586
    Check(
        "vite-build",
        "web",
        lambda _: run(_node("npm", "run", "build"), _WEBAPP),
        mb=1360,  # 1086
        slots=2,
        # Inside `out/`, so vite's `emptyOutDir` takes it with the product it vouched for.
        builds=("webapp/out/.built-{key}",),
    ),
    # Its incremental `tsconfig.tsbuildinfo` is written only where its content moved.
    Check("tsc", "web", _tsc, mb=735, tier="commit"),  # 586
    Check(
        "playwright",
        "web",
        _playwright,
        mb=1680,  # 1344
        tier="landing",
        slots=3,  # its browser and both servers
        # Both servers' backend is the offline world's (`e2e/serve_app.py`); `walk` walks its run.
        reads=(*_WEBAPP_READS, *OFFLINE_RUN_READS, "webapp/out/", os.path.join(_WALK_WORLDS, "")),
    ),
    Check("npm-audit", "release", _npm_audit, mb=115),  # 91
    Check("pip-audit", "release", _pip_audit, mb=165),  # 130
    Check("advisories", "release", _advisories, mb=115),  # 93
)


def _graph_order() -> list[Check]:
    placed: list[Check] = []
    while len(placed) < len(CHECKS):
        ready = [c for c in CHECKS if c not in placed and set(_producers(c, CHECKS)) <= set(placed)]
        if not ready:
            cycle = [c.name for c in CHECKS if c not in placed]
            raise AssertionError(f"the checks' writes and reads form a cycle among {cycle}")
        placed += ready
    return placed


_NAMED = {c.name: c for c in CHECKS}
_graph_order()
# The products the landing checks read, pinned beneath the derived edges.
assert {"vite-build", "walk-world"} <= {b.name for b in _builders(_NAMED["playwright"], CHECKS)}
assert {OFFLINE_RUN} <= {b.name for b in _builders(_NAMED["equals-main"], CHECKS)}


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
    if result.kept:
        return result
    if peak is not None and peak > check.mb:
        over = f"peaked at {peak} MB, over its {check.mb} MB cap.\n{result.out}"
        return dataclasses.replace(result, rc=result.rc or 1, out=over, peak_mb=peak)
    return dataclasses.replace(result, peak_mb=peak)


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
        for path in check.builds if not rc and key is not None else ():
            (REPO / path.format(key=key)).touch()
    except Exception as exc:  # a broken check is a red check, never a silent skip
        logger.exception("check %s raised", check.name)
        rc, out, key = 1, f"{type(exc).__name__}: {exc}", None
    return Result(check, rc, out, time.monotonic() - began, key)


def _schedule(
    checks: Sequence[Check], sel: Selection, store: LastGreen | None, free_mb: int
) -> list[Result]:
    """Each check once its producers are done, beside every other the box's free memory and cores
    still hold; the widest first. One too big for the box runs when nothing else does."""
    done: dict[Check, Result] = {}
    running: set[Check] = set()
    moved = threading.Condition()

    def execute(check: Check) -> None:
        result = _execute(check, sel, store)
        with moved:
            running.discard(check)
            done[check] = result
            moved.notify()

    with moved:
        while len(done) < len(checks):
            ready = [
                c
                for c in checks
                if c not in done and c not in running and done.keys() >= set(_producers(c, checks))
            ]
            for check in sorted(ready, key=lambda c: (-c.slots, -c.mb)):
                mb = sum(c.mb for c in running) + check.mb
                slots = sum(c.slots for c in running) + check.slots
                if not running or (mb <= free_mb and slots <= _CORES):
                    running.add(check)
                    # A daemon, so Ctrl+C ends the gate rather than waiting out each check.
                    threading.Thread(target=execute, args=(check,), daemon=True).start()
            moved.wait()
    return [done[check] for check in checks]


# The one remote ref a push proves.
_PROVEN_REF = "refs/heads/main"
# What a checkout (a pushed tip, a commit's index) borrows from the clone's main tree, as it reads
# that tree's venv and greens: the web checks' packages, which a side worktree has none of.
_BORROWED = ("webapp/node_modules",)
# The temp dir is the machine's: the clone's tag keeps one clone's sweep off another's checkouts.
_CHECKOUT = f"pp-tree-{digest(str(clone_root()).encode()).hex()[:8]}-"
_CHECKOUT_NAME = re.compile(rf"{_CHECKOUT}[0-9a-f]+-(\d+)")


def _link(link: Path, target: Path) -> None:
    if sys.platform == "win32":  # a junction: a directory symlink needs a privilege
        code, out = run(["cmd", "/c", "mklink", "/J", str(link), str(target)], REPO)
        if code:
            raise RuntimeError(f"mklink /J {link}: {out}")
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_borrowed(tree: Path) -> None:
    # Each link goes before the tree, so no delete walks into what it points at.
    for link in (tree / rel for rel in _BORROWED):
        if link.is_symlink() or link.is_junction():
            os.unlink(link)


def _sweep_checkouts() -> None:
    """A checkout whose gate is gone was hard-killed before its own cleanup."""
    for tree in Path(tempfile.gettempdir()).glob(f"{_CHECKOUT}*"):
        if (named := _CHECKOUT_NAME.fullmatch(tree.name)) and not alive(int(named[1])):
            _unlink_borrowed(tree)
            shutil.rmtree(tree)
    code, out = git("worktree", "prune")
    if code:
        raise RuntimeError(f"git worktree prune: {out}")


@contextlib.contextmanager
def _checkout(commit: str, *, files: str | None = None) -> Iterator[Path]:
    """*commit* in a throwaway worktree; with *files* (a tree object), its index and files are
    that tree's, so `git diff --cached` there is the same change as here."""
    tree = Path(tempfile.gettempdir()) / f"{_CHECKOUT}{commit[:12]}-{os.getpid()}"
    lazy = ("--no-checkout",) if files else ()
    code, out = git("worktree", "add", "--detach", *lazy, str(tree), commit)
    if code:
        raise RuntimeError(f"git worktree add {commit}: {out}")
    lent = [rel for rel in _BORROWED if (clone_root() / rel).is_dir()]
    try:
        if files and (laid := run(["git", "read-tree", "--reset", "-u", files], tree))[0]:
            raise RuntimeError(f"git read-tree {files}: {laid[1]}")
        for rel in lent:
            _link(tree / rel, (clone_root() / rel).resolve())
        yield tree
    finally:
        _unlink_borrowed(tree)
        code, out = git("worktree", "remove", "-f", "-f", str(tree))
        if code:
            raise RuntimeError(f"git worktree remove {tree}: {out}")


def _proven_in(commit: str, asked: Sequence[str], *, files: str | None = None) -> int:
    """The tree a mode proves: the gate re-run on *asked* in *commit*'s own checkout (*files*, the
    index's tree laid over it), its cwd and PYTHONPATH that tree's, so every check reads that tree
    whether this gate re-execs into the pinned venv or borrows it (`_REEXEC` inherited)."""
    # A hook inherits the repo git located for it: in a linked worktree `GIT_DIR`, and a commit's
    # `GIT_INDEX_FILE`, would aim every git call in the checkout at the committing worktree's own
    # index. Git's own list of them, dropped: the checkout's `.git` names its repo.
    code, local = git("rev-parse", "--local-env-vars")
    if code:
        raise RuntimeError(f"git rev-parse --local-env-vars: {local}")
    for var in local.split():
        os.environ.pop(var, None)
    _sweep_checkouts()
    with _checkout(commit, files=files) as tree:
        argv = [sys.executable, "scripts/gate.py", *asked]
        env = {**os.environ, "PYTHONPATH": str(tree)}
        return subprocess.run(argv, cwd=tree, env=env, stdin=subprocess.DEVNULL).returncode


def _tree_is_index() -> bool:
    """Every file here is the index's: no unstaged edit or delete, and no untracked file."""
    unstaged, _ = git("diff", "--quiet")
    code, untracked = git("ls-files", "--others", "--exclude-standard")
    return unstaged == 0 and code == 0 and not untracked.strip()


def _prove_index() -> int:
    """`--staged` where this tree holds what the commit leaves out: the same gate, in a checkout
    of the index alone, so no check reads a file the commit does not carry."""
    # The commit's index is read once, into a tree; past it no git call reads or writes it.
    code, files = git("write-tree")
    if code:
        raise RuntimeError(f"git write-tree: {files}")
    _, head = git("rev-parse", "HEAD")
    print("gate[staged]: the tree holds unstaged work, so the index is checked in its own checkout")
    return _proven_in(head.strip(), sys.argv[1:], files=files.strip())


def _ci_of(commit: str) -> str:
    """CI's word on the main a push lands on: this gate proves the desk's platform, and a red main
    on another (a POSIX-only path) is read here or by no one."""
    gh = shutil.which("gh")
    if not gh:
        return "unread — `gh` is not on PATH"
    line = '.[] | "\\(.status) \\(.conclusion) \\(.url)"'
    listed = [gh, "run", "list", "--workflow", "CI", "--commit", commit, "--limit", "1"]
    rc, out = run([*listed, "--json", "status,conclusion,url", "-q", line], REPO)
    if rc:
        return f"unread — {out.strip() or 'gh could not list its runs'}"
    status, _, rest = out.strip().partition(" ")
    if not status:
        return "no run"
    conclusion, _, url = rest.partition(" ")
    if status != "completed":
        return f"{status}, {url}"
    if conclusion == "success":
        return "green"
    return f"RED ({conclusion}) — this push lands on it; read {url} first"


def _prove_tips(refs: str, checks: Sequence[Check]) -> int:
    # *refs* is git's pre-push stdin: `<local ref> <local sha> <remote ref> <remote sha>` a line.
    # Only main is proven: a branch is work in flight, proven when it lands. An all-zero local
    # sha is a deletion: no tip to prove.
    pushed = [line.split() for line in refs.splitlines()]
    tips = sorted({sha for _, sha, ref, _ in pushed if ref == _PROVEN_REF and set(sha) != {"0"}})
    for landed in {sha for _, _, ref, sha in pushed if ref == _PROVEN_REF and set(sha) != {"0"}}:
        print(f"gate[push]: main's CI at {landed[:12]}: {_ci_of(landed)}")
    missing = [str(clone_root() / rel) for rel in _BORROWED if not (clone_root() / rel).is_dir()]
    if tips and missing and any(c.kind == "web" for c in checks):
        print(
            f"gate[push]: the web checks read {', '.join(missing)}, which is absent: `npm ci` there"
        )
        return 1
    failed = 0
    for tip in tips:
        began = time.monotonic()
        rc = _proven_in(tip, ["--only", ",".join(c.name for c in checks)])
        verdict = "FAIL" if rc else "green"
        print(
            f"gate[push]: {tip[:12]} {verdict} in its own checkout, {time.monotonic() - began:.1f}s"
        )
        failed += bool(rc)
    return 1 if failed else 0


# The one list of the env every verdict is taken in; CI's and a release's gate build it too.
# `api`, `mcp`: mypy resolves fastapi and the MCP SDK. `harbor`, `dspy`, `mcp`: a test skipped
# for a missing extra never ran. `benchmarks`: the hub datasets the box offers start here too.
_PINNED_EXTRAS = ("dev", "api", "harbor", "dspy", "excel", "mcp", "benchmarks")
_LOCK_STAMP = _PINNED / "gate-lock.sha256"


def _install_digest(uv: str, extras: list[str]) -> str:
    """The resolved pinned set, never the lock's bytes: a lock edit outside the pinned extras, or
    the project's own version (a release bumps it and installs nothing else), moves nothing."""
    export = subprocess.run(
        [uv, "export", "--frozen", "--quiet", "--no-header", "--no-annotate", *extras],
        cwd=REPO,
        capture_output=True,
        encoding="utf-8",
    )
    if export.returncode:
        raise SystemExit(f"gate: `uv export --frozen` failed:\n{export.stderr}")
    return digest(export.stdout.encode()).hex()


def _editable_target() -> Path | None:
    # The tree `_PINNED`'s editable `promptpotter` imports from, read off its install record.
    sites = (*_PINNED.glob("Lib/site-packages"), *_PINNED.glob("lib/python*/site-packages"))
    dist = next(
        iter(metadata.distributions(name="promptpotter", path=[str(p) for p in sites])), None
    )
    record = dist.read_text("direct_url.json") if dist else None
    return Path.from_uri(json.loads(record)["url"]).resolve() if record else None


def _surplus(uv: str, owner: Path, extras: list[str]) -> list[str]:
    """What the venv holds that an exact sync to the pinned extras would uninstall."""
    dry = subprocess.run(
        [uv, "sync", "--frozen", "--dry-run", *extras],
        cwd=owner,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if dry.returncode:
        raise SystemExit(f"gate: `uv sync --dry-run` failed:\n{dry.stdout}{dry.stderr}")
    said = (dry.stdout + dry.stderr).splitlines()

    def named(sign: str) -> set[str]:
        return {re.split(r"[=@ ]", ln[3:], maxsplit=1)[0] for ln in said if ln.startswith(sign)}

    # An editable install is reinstalled on every sync: out and in again is no surplus.
    return sorted(named(" - ") - named(" + "))


def _with_builders(checks: list[Check]) -> list[Check]:
    """`checks` plus every builder of a product they read, transitively, so `--only playwright`
    never reads a product older than its sources."""
    pulled, todo = list(checks), list(checks)
    while todo:
        for builder in _builders(todo.pop(), CHECKS):
            if builder not in pulled:
                pulled.append(builder)
                todo.append(builder)
    return pulled


# The kinds that run the engine, so a run holding one runs in the pinned venv.
_PINNED_KINDS = ("py", "parity")


def _kinds(args: argparse.Namespace) -> set[str]:
    selected = (
        ("py", args.py),
        ("web", args.web),
        ("release", args.release),
        ("parity", args.against is not None),
    )
    return {kind for kind, on in selected if on} or {"py", "web"}


def _asked(args: argparse.Namespace) -> set[str]:
    return {name for value in args.only for name in value.split(",") if name}


def _query(args: argparse.Namespace, sel: Selection) -> list[Check]:
    asked = _asked(args)
    checks = (
        [c for c in CHECKS if c.name in asked]
        if asked
        else [
            c for c in CHECKS if c.kind in _kinds(args) and (c.tier == "commit" or not args.staged)
        ]
    )
    if args.staged:
        checks = [
            c for c in checks if any(reads(path, c.inputs() or ()) for path in sel.staged_paths)
        ]
    if sel.changed is not None and not asked:
        checks = [
            c
            for c in checks
            if c.tier != "landing" and any(reads(path, c.inputs() or ()) for path in sel.changed)
        ]
    return sorted(checks if args.staged else _with_builders(checks), key=CHECKS.index)


def _reexec_pinned() -> None:
    # From another interpreter, `mypy` resolves different stubs than `uv.lock` pins.
    if os.environ.get(_REEXEC):
        return
    # A sync from another tree would re-point the owner's editable install.
    owner = _PINNED.resolve().parent
    extras = [arg for extra in _PINNED_EXTRAS for arg in ("--extra", extra)]
    exact = f"uv sync --frozen {' '.join(extras)}"
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit(
            f"gate: `uv` is not on PATH, so the locked env is unreachable.\nRun: {exact}"
        )
    locked = _install_digest(uv, extras)
    bindir = _PINNED / ("Scripts" if sys.platform == "win32" else "bin")
    python = bindir / ("python.exe" if sys.platform == "win32" else "python")
    stamped = _LOCK_STAMP.exists() and _LOCK_STAMP.read_text() == locked
    target = _editable_target()
    fresh = python.exists() and stamped and target == owner
    if not fresh:
        if owner != REPO:
            remedy = (
                f"Run the gate in {owner}: it syncs the clone's venv."
                if stamped
                else f"Run `git -C {owner} switch --detach <sha>`, then the gate in {owner}: only "
                f"that tree syncs the venv, and the switch moves the shared main tree."
            )
            raise SystemExit(
                f"gate: {_PINNED} is {owner}'s venv, and the gate never syncs a venv this tree "
                f"does not own (pins {'match' if stamped else 'differ'}, editable on {target}).\n"
                f"{remedy}"
            )
        if subprocess.run([uv, "sync", "--frozen", "--inexact", *extras], cwd=REPO).returncode:
            raise SystemExit("gate: `uv sync` could not build the locked env.")
        _LOCK_STAMP.write_text(locked)
    # `--inexact` keeps what an exact sync would uninstall, so a surplus is refused, never dropped.
    if surplus := _surplus(uv, owner, extras):
        raise SystemExit(
            f"gate: {_PINNED} holds {', '.join(surplus)} beyond the pinned extras, and an optional "
            f"import loads what the gate would measure. Run in {owner}: {exact}"
        )
    if fresh and Path(sys.prefix) == _PINNED and target == REPO:
        return
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


def _parser() -> argparse.ArgumentParser:
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
        help="the pre-push hook: every CI check, in a clean checkout of the tip pushed to main",
    )
    scope.add_argument(
        "--changed",
        action="store_true",
        help="while iterating: the checks that read a file changed since HEAD, and inside "
        "vitest and eslint only what owns one",
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        default=[],
        help="checks by name, the flag repeated or the names comma-separated",
    )
    parser.add_argument(
        "--against",
        metavar="HOME",
        type=Path,
        help="a landing's parity (`equals-main`): the offline run's traces against this home's",
    )
    parser.add_argument(
        "--walk-world",
        action="store_true",
        help="answer this tree's walk world on stdout, building it when absent (`npm run e2e` asks)",
    )
    parser.add_argument(
        "--commit-msg",
        metavar="FILE",
        help="the commit-msg hook: a subject opening on ASCII",
    )
    return parser


def main() -> int:
    # Tools print ✖ and →, which raise on a cp1252 console while REPORTING the failure.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    end_children_with_this_process()
    parser = _parser()
    args = parser.parse_args()
    if args.commit_msg:
        subject = Path(args.commit_msg).read_bytes().split(b"\n", 1)[0]
        if not subject[:1].isascii():
            # A BOM (`EF BB BF`, PowerShell's `Out-File`) hides in front of the type `git log` greps.
            print(f"gate: the commit subject opens on a non-ASCII byte: {subject!r}")
            return 1
        return 0

    answer = sys.stdout
    if args.walk_world:
        # The path alone on stdout, which `playwright.config.ts` reads; the check reports on stderr.
        args.only = ["walk-world"]
        sys.stdout = sys.stderr
    asked = _asked(args)
    if unknown := sorted(asked - _NAMED.keys()):
        parser.error(f"no such check: {', '.join(unknown)} (have: {', '.join(_NAMED)})")
    if args.against is None and any(_NAMED[name].kind == "parity" for name in asked):
        parser.error("equals-main reads the home `--against` names")

    sel = Selection.build(args.staged, args.changed)
    checks = _query(args, sel)
    # Over the very checks the run executes, builders included: `--web` runs walk-world, a py check.
    if not args.push and any(c.kind in _PINNED_KINDS for c in checks):
        # A push re-execs nowhere: each checkout pins its own, and a path pinned here names this tree.
        _reexec_pinned()
    if args.push:
        return _prove_tips(sys.stdin.read(), checks)
    if args.staged and checks and not _tree_is_index():
        return _prove_index()
    kinds = "+".join(sorted(_kinds(args)))
    label = "staged" if args.staged else kinds + ("/changed" * args.changed)
    # A CI runner has no sibling to queue behind, so it takes the lock like a desk does.
    unqueued = all(c.tier == "commit" for c in checks)
    queue_behind_siblings(f"gate[{label}]", exempt=unqueued)
    budget = RamBudget.read()

    # `--staged` checks less than any key names, so it neither reads nor records a green, and CI
    # keeps none; the walk world is keyed all the same, on the tree that builds it.
    store = None if args.staged else LastGreen.open()
    tree = store or LastGreen.of_tree()
    world_key = None if tree is None else _NAMED["walk-world"].key(tree)
    sel = dataclasses.replace(
        sel,
        walk_world=None if world_key is None else Path(_WALK_WORLD.format(key=world_key)),
        unqueued=unqueued,
        against=args.against,
    )
    started = time.monotonic()
    # CI's configuration, never the desk's: no `.env` and no workspace, or a verdict reads either.
    with tempfile.TemporaryDirectory(prefix="pp-gate-", ignore_cleanup_errors=True) as home:
        os.environ["PROMPTPOTTER_HOME"] = home
        os.environ["PROMPTPOTTER_ENV_FILE"] = str(Path(home) / ".env")
        results = _schedule(checks, sel, store, budget.free_mb)
    total = time.monotonic() - started
    if store is not None:
        store.record({r.check.name: None if r.rc else r.key for r in results if not r.kept})
    failed = [r for r in results if r.rc]
    kept = sum(r.kept for r in results)
    unchanged = f", {kept} unchanged since green" if kept else ""

    for r in results:
        if not r.kept:
            peak = f"{r.peak_mb or '-'} of {r.check.mb} MB"
            verdict = "FAIL" if r.rc else "ok"
            print(f"{r.check.name:<24}{r.check.kind:<8}{verdict:>5}{r.secs:>8.1f}s{peak:>16}")
    for r in failed:
        print(f"\n--- {r.check.name} ---\n{r.out}")
    busy = sum(r.secs for r in results)
    timed = f"{total:.1f}s ({busy:.1f}s of checks, at {budget.free_mb} MB free on {_CORES} cores)"
    if failed:
        print(
            "\nChange the failing fact where it is DECIDED, not where it shows. Green on rerun, or"
            "\nred only on this machine, is a defect in the gate or the desk, never a pass."
        )
        print(f"\ngate[{label}]: {len(results)} checks, {len(failed)} failed{unchanged}, {timed}")
        return 1
    print(f"gate[{label}]: {len(results)} checks green in {timed}{unchanged}")
    if args.walk_world:
        print(sel.world, file=answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
