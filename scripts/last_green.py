"""Stdlib only: the gate imports this before it has chosen its interpreter."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import sys
import sysconfig
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from stat import S_ISDIR, S_ISREG

from processes import REPO, clone_cache, exclusive, git, replace_changed

# No prose is among them, so a docs edit reruns nothing that only RUNS the engine.
ENGINE_RUNTIME = ("promptpotter/", "examples/", "pyproject.toml", "uv.lock")
OFFLINE_RUN = "offline-run"
OFFLINE_RUN_READS = (
    *ENGINE_RUNTIME,
    # The L4 leg's fixture (`offline_run.py::L4_DATASET`).
    "research/datasets/promptpotter-self/",
    "scripts/offline_run.py",
    "scripts/offline_world.py",
    "scripts/last_green.py",
    "scripts/processes.py",
)
# Stamped by size and mtime, so a late reader is keyed on the file as it is when IT asks.
_DIGESTS: dict[Path, tuple[tuple[int, int], bytes]] = {}


def digest(*chunks: bytes) -> bytes:
    return hashlib.sha256(b"\0".join(chunks)).digest()


def file_digest(path: Path) -> bytes:
    try:
        stat = path.stat()
    except OSError:  # listed and since deleted: its absence is the content
        return b""
    if S_ISDIR(stat.st_mode) and (path / ".git").exists():  # a gitlink: its content is its commit
        code, head = git("-C", str(path), "rev-parse", "HEAD")
        if code:
            raise RuntimeError(f"{path}: a gitlink whose commit git cannot read: {head}")
        return digest(head.encode())
    if not S_ISREG(stat.st_mode):
        raise RuntimeError(f"{path}: git lists it, and it is neither a file nor a gitlink")
    stamp = (stat.st_size, stat.st_mtime_ns)
    held = _DIGESTS.get(path)
    if held is None or held[0] != stamp:
        held = _DIGESTS[path] = stamp, digest(path.read_bytes())
    return held[1]


def reads(rel: str, prefixes: Sequence[str]) -> bool:
    inside = tuple(p for p in prefixes if not p.startswith("!"))
    outside = tuple(p[1:] for p in prefixes if p.startswith("!"))
    return rel.startswith(inside) or bool(outside and not rel.startswith(outside))


def _module_name(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@cache
def _package_modules() -> dict[str, str]:
    rels = (p.relative_to(REPO).as_posix() for p in (REPO / "promptpotter").rglob("*.py"))
    return {_module_name(rel): rel for rel in rels}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _import_names(path: Path) -> set[str]:
    importer = _module_name(path.relative_to(REPO).as_posix())
    home = importer if path.name == "__init__.py" else importer.rpartition(".")[0]
    found: set[str] = set()
    # An import is a statement, so no expression is walked; a function body is.
    todo: list[ast.AST] = [ast.parse(path.read_text(encoding="utf-8"))]
    while todo:
        sub = todo.pop()
        if isinstance(sub, ast.Import):
            found.update(a.name for a in sub.names)
        elif isinstance(sub, ast.ImportFrom):
            base = sub.module or ""
            if sub.level:
                up = home.split(".")[: len(home.split(".")) - (sub.level - 1)]
                base = ".".join([*up, base] if base else up)
            found.add(base)
            found.update(f"{base}.{a.name}" for a in sub.names)
        for field, value in ast.iter_fields(sub):
            if not isinstance(value, list) or (
                field == "body" and isinstance(sub, ast.If) and _is_type_checking(sub.test)
            ):
                continue
            todo.extend(
                child
                for child in value
                if isinstance(child, ast.stmt | ast.excepthandler | ast.match_case)
            )
    return found


def imported(path: Path) -> set[str]:
    """The package's files *path* imports, function bodies included, `TYPE_CHECKING` excluded."""
    modules = _package_modules()
    return {modules[name] for name in _import_names(path) if name in modules}


class LastGreen:
    """One store per clone: a key is content, so a green any worktree recorded holds in every other."""

    # What `npm ci` leaves naming the tree it installed: whether the install still matches the lock.
    _INSTALLED = ("webapp/node_modules/.package-lock.json",)
    # Per check, the newest greens kept: enough for main, a few worktrees and a pushed tip to stop
    # evicting each other.
    _KEPT = 8

    def __init__(self, listed: str, store: Path) -> None:
        self._store = store
        self._files = sorted(filter(None, listed.split("\0")))
        # The interpreter's own site dirs: a build product in the tree is on `sys.path`, never a tool.
        site = sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")})
        tools = sorted(f"{d.name}={d.version}" for d in importlib.metadata.distributions(path=site))
        tools.append(sys.version)  # the interpreter is a tool no distribution names
        self._tools = digest(*(tool.encode() for tool in tools))

    @classmethod
    def open(cls) -> LastGreen | None:
        """``None`` under ``CI``, which keeps no green, and where git cannot list the tree."""
        return None if os.environ.get("CI") else cls.of_tree()

    @classmethod
    def of_tree(cls) -> LastGreen | None:
        """The tree's keys, whether or not a green is kept: ``None`` where git cannot list the
        tree, as a key over no files matches every tree."""
        code, listed = git("ls-files", "-co", "--exclude-standard", "-z")
        return None if code else cls(listed, clone_cache() / "greens.json")

    def key(self, prefixes: Sequence[str], without: Sequence[str] = ()) -> str:
        skipped = tuple(without)
        sources = (
            rel for rel in self._files if reads(rel, prefixes) and not rel.startswith(skipped)
        )
        return digest(
            self._tools,
            *(
                rel.encode() + b"\0" + file_digest(REPO / rel)
                for rel in (*self._INSTALLED, *sources)
            ),
        ).hex()

    def _load(self) -> dict[str, list[str]]:
        try:
            return dict(json.loads(self._store.read_text(encoding="utf-8"))["green"])
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def holds(self, name: str, key: str) -> bool:
        return key in self._load().get(name, ())

    def record(self, verdicts: Mapping[str, str | None]) -> None:
        """A key files a green beside the others; ``None`` drops every green the check holds."""
        # Load, merge and replace under one lock: a sibling gate finishing alongside keeps its greens.
        with exclusive(self._store.with_name(f"{self._store.name}.lock")):
            green = self._load()
            for name, key in verdicts.items():
                if key is None:
                    green.pop(name, None)
                else:
                    kept = [k for k in green.get(name, ()) if k != key]
                    green[name] = [*kept, key][-self._KEPT :]
            replace_changed(self._store, json.dumps({"green": green}, indent=1, sort_keys=True))
