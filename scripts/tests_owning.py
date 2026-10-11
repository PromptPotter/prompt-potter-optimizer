"""Blind to a once-per-process value and a re-exported constant: a commit still rests on a full run."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from collections.abc import Iterable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PACKAGE = "promptpotter"
TESTS = "tests"
EVERY_TEST = f"{TESTS}/"
RECORD = REPO / ".pytest_cache" / "tests_owning.json"


def _rel(path: str | Path) -> str | None:
    # No filesystem call: a recording asks this per opened path. `\\?\` is Windows' long-path form.
    full = os.path.abspath(os.path.join(REPO, str(path).removeprefix("\\\\?\\")))
    if not os.path.normcase(full).startswith(os.path.normcase(str(REPO)) + os.sep):
        return None
    return Path(os.path.relpath(full, REPO)).as_posix()


def _hashed() -> dict[str, str]:
    files = [*(REPO / PACKAGE).rglob("*.py"), *(REPO / TESTS).glob("*.py")]
    return {p.relative_to(REPO).as_posix(): hashlib.sha1(p.read_bytes()).hexdigest() for p in files}


def _module_name(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@cache
def _modules() -> dict[str, str]:
    return {_module_name(rel): rel for rel in _hashed() if rel.startswith(f"{PACKAGE}/")}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(node, ast.If) and _is_type_checking(node.test) and child in node.body:
            continue
        yield from _runtime_nodes(child)


def _imported(node: ast.AST, importer: str, is_package: bool) -> set[str]:
    modules = _modules()
    home = importer if is_package else importer.rpartition(".")[0]
    found: set[str] = set()
    for sub in _runtime_nodes(node):
        if isinstance(sub, ast.Import):
            found.update(a.name for a in sub.names)
        elif isinstance(sub, ast.ImportFrom):
            base = sub.module or ""
            if sub.level:
                up = home.split(".")[: len(home.split(".")) - (sub.level - 1)]
                base = ".".join([*up, base] if base else up)
            found.add(base)
            found.update(f"{base}.{a.name}" for a in sub.names)
    return {modules[name] for name in found if name in modules}


@cache
def _importers() -> dict[str, frozenset[str]]:
    """A package's ``__init__`` runs for everything beneath it, so each file beneath imports it."""
    back: dict[str, set[str]] = {}
    for name, rel in _modules().items():
        tree = ast.parse((REPO / rel).read_text("utf-8"))
        imported = _imported(tree, name, rel.endswith("__init__.py"))
        parent = _modules().get(name.rpartition(".")[0])
        for target in imported | ({parent} if parent else set()):
            back.setdefault(target, set()).add(rel)
    return {target: frozenset(by) for target, by in back.items()}


class _SuiteFile:
    def __init__(self, rel: str) -> None:
        self.name = _module_name(rel)
        self.tree = ast.parse((REPO / rel).read_text("utf-8"))
        self.bound: dict[str, list[ast.stmt]] = {}
        self.always: list[ast.stmt] = []
        self.tests: dict[str, ast.stmt] = {}
        # A star import or a test class is a shape this reader does not walk.
        self.readable = True
        for stmt in self.tree.body:
            names: list[str] = []
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names = [stmt.name]
                if any("autouse" in ast.dump(d) for d in stmt.decorator_list):
                    self.always.append(stmt)
                if stmt.name.startswith("test_"):
                    self.tests[stmt.name] = stmt
                self.readable &= not stmt.name.startswith("Test")
            elif isinstance(stmt, ast.Import | ast.ImportFrom):
                names = [(a.asname or a.name).partition(".")[0] for a in stmt.names]
                self.readable &= "*" not in names
            elif isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                names = [n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            else:
                self.always.append(stmt)
            for name in names:
                self.bound.setdefault(name, []).append(stmt)

    def named(self, stmt: ast.stmt, seen: set[int] | None = None) -> set[str]:
        seen = set() if seen is None else seen
        if id(stmt) in seen:
            return set()
        seen.add(id(stmt))
        found = _imported(stmt, self.name, False)
        read = {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name)}
        read |= {a.arg for n in ast.walk(stmt) if isinstance(n, ast.arguments) for a in n.args}
        local = [n for n in ast.walk(stmt) if isinstance(n, ast.ImportFrom) and n is not stmt]
        for name in read:
            bindings = self.bound.get(name, []) + [
                n for n in local if any((a.asname or a.name) == name for a in n.names)
            ]
            for binding in bindings:
                if isinstance(binding, ast.ImportFrom):
                    kept = [a for a in binding.names if (a.asname or a.name) == name]
                    # `pythonpath = ["tests"]`: a sibling is imported bare.
                    sibling = _suite_file(f"{TESTS}/{(binding.module or '').rpartition('.')[2]}.py")
                    if sibling is None:
                        one = ast.ImportFrom(module=binding.module, names=kept, level=binding.level)
                        found |= _imported(one, self.name, False)
                        continue
                    for alias in kept:
                        for origin in sibling.bound.get(alias.name, []):
                            found |= sibling.named(origin, seen)
                elif isinstance(binding, ast.Import):
                    kept = [
                        a for a in binding.names if (a.asname or a.name).partition(".")[0] == name
                    ]
                    found |= _imported(ast.Import(names=kept), self.name, False)
                else:
                    found |= self.named(binding, seen)
            conftest = _suite_file(f"{TESTS}/conftest.py")
            if conftest is not None and conftest is not self and name not in self.bound:
                for fixture in conftest.bound.get(name, []):
                    found |= conftest.named(fixture, seen)
        return found


@cache
def _suite_file(rel: str) -> _SuiteFile | None:
    return _SuiteFile(rel) if rel.startswith(f"{TESTS}/") and (REPO / rel).is_file() else None


def _suite() -> dict[str, set[str]] | None:
    conftest = _suite_file(f"{TESTS}/conftest.py")
    out: dict[str, set[str]] = {}
    for path in sorted((REPO / TESTS).glob("test_*.py")):
        rel = path.relative_to(REPO).as_posix()
        file = _suite_file(rel)
        assert file is not None
        if not file.readable or (conftest is not None and not conftest.readable):
            return None
        shared: set[str] = set()
        for support in (file, conftest):
            if support is not None:
                for stmt in support.always:
                    shared |= support.named(stmt)
        for name, test in file.tests.items():
            out[f"{rel}::{name}"] = shared | file.named(test)
    return out


def tests_owning(paths: Iterable[str | Path] = ()) -> list[str] | None:
    """``[]`` = no test is owed; ``None`` = every test must run."""
    try:
        record: dict[str, Any] = json.loads(RECORD.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    suite = _suite()
    if suite is None:
        return None
    then, now = record["files"], _hashed()
    moved = {rel for rel in then.keys() | now.keys() if then.get(rel) != now.get(rel)}
    named = {_rel(p) for p in paths}
    if None in named:
        return None
    ran: dict[str, set[str]] = {node: set(files) for node, files in record["ran"].items()}
    # A test the record never ran is owed whatever moved.
    picked = set(suite.keys() - ran.keys())
    for rel in moved | {r for r in named if r is not None}:
        if rel.startswith(f"{TESTS}/test_") and rel in now:
            picked.update(node for node in suite if node.startswith(f"{rel}::"))
        elif rel.startswith(f"{PACKAGE}/") and rel in now and rel in then:
            reach = {rel, *_importers().get(rel, ())}
            picked.update(
                node for node in suite if reach & ran.get(node, set()) or rel in suite[node]
            )
        elif rel in now or rel in then or rel.endswith(".py"):
            # Code the record does not follow: an import of a cached module opens nothing.
            return None
        elif rel in record["opened"] or Path(rel).parent.as_posix() in record["listed"]:
            return None
    return sorted(picked)


def _record(pytest_args: list[str]) -> int:
    import pytest

    monitoring = sys.monitoring
    tool = monitoring.PROFILER_ID
    ran: dict[str, set[str]] = {}
    current: list[set[str]] = []
    opened: set[str] = set()
    listed: set[str] = set()

    def started(code: Any, _offset: int) -> object:
        # A module body is an import, which the import graph already answers for.
        if current and code.co_name != "<module>":
            current[-1].add(code.co_filename)
        return monitoring.DISABLE

    def audited(event: str, args: tuple[Any, ...]) -> None:
        if event not in ("open", "os.scandir", "os.listdir") or not args:
            return
        target = os.fsdecode(args[0]) if isinstance(args[0], str | bytes | os.PathLike) else None
        if target is not None:
            (opened if event == "open" else listed).add(target)

    class Ran:
        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_protocol(self, item: pytest.Item) -> Iterator[None]:
            current.append(ran.setdefault(item.nodeid.partition("[")[0], set()))
            monitoring.restart_events()
            try:
                return (yield)
            finally:
                current.pop()

    before = _hashed()
    sys.addaudithook(audited)
    monitoring.use_tool_id(tool, "tests_owning")
    monitoring.register_callback(tool, monitoring.events.PY_START, started)
    monitoring.set_events(tool, monitoring.events.PY_START)
    try:
        code = int(pytest.main(["-p", "no:xdist", *pytest_args], plugins=[Ran()]))
    finally:
        monitoring.set_events(tool, 0)
        monitoring.free_tool_id(tool)
    suite = _suite()
    if code or suite is None or suite.keys() - ran.keys() or before != _hashed():
        # A failed, partial or mid-edit run describes no tree: the old record stands.
        return code
    # Bytecode is the hashed source over again, and it is most of what a session opens.
    inside, scanned = (
        {rel for p in paths if (rel := _rel(p)) is not None and "__pycache__" not in rel}
        for paths in (opened, listed)
    )
    package = set(before)
    RECORD.parent.mkdir(exist_ok=True)
    RECORD.write_text(
        json.dumps(
            {
                "files": before,
                "ran": {
                    node: sorted(rel for f in files if (rel := _rel(f)) in package)
                    for node, files in sorted(ran.items())
                },
                "opened": sorted(inside - package),
                "listed": sorted(scanned),
            },
            indent=1,
        ),
        "utf-8",
    )
    return code


def main(argv: list[str]) -> int:
    split = argv.index("--") if "--" in argv else len(argv)
    own, pytest_args = argv[:split], argv[split + 1 :]
    if own[:1] == ["--record"]:
        return _record(pytest_args)
    run = own[:1] == ["--run"]
    nodes = tests_owning(own[run:])
    if not run:
        print("\n".join([EVERY_TEST] if nodes is None else nodes))
        return 0
    if nodes is None:
        return _record(pytest_args)
    if not nodes:
        print("tests_owning: no test owns what moved")
        return 0
    import pytest

    return int(pytest.main(["-p", "no:xdist", *nodes, *pytest_args]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
