"""The package's import walk: which of its files a module imports, read off the source. Stdlib
only and importing nothing of the package, so a gate script reads it beside the landing key."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import cache
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1]
# Files are named as the repo names them (`promptpotter/x/y.py`), installed or not.
_BASE = _PACKAGE.parent


def _module_name(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@cache
def package_modules() -> dict[str, str]:
    """Each package module's dotted name, to its file."""
    rels = (p.relative_to(_BASE).as_posix() for p in _PACKAGE.rglob("*.py"))
    return {_module_name(rel): rel for rel in rels}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def import_statements(path: Path) -> list[tuple[set[str], bool]]:
    """Each package file one import statement of *path* names, and whether the statement sits
    under ``TYPE_CHECKING``; function bodies included."""
    importer = _module_name(path.resolve().relative_to(_BASE).as_posix())
    home = importer if path.name == "__init__.py" else importer.rpartition(".")[0]
    modules = package_modules()
    found: list[tuple[set[str], bool]] = []
    # An import is a statement, so no expression is walked; a function body is.
    todo: list[tuple[ast.AST, bool]] = [(ast.parse(path.read_text(encoding="utf-8")), False)]
    while todo:
        sub, typed = todo.pop()
        names: list[str] = []
        if isinstance(sub, ast.Import):
            names = [a.name for a in sub.names]
        elif isinstance(sub, ast.ImportFrom):
            base = sub.module or ""
            if sub.level:
                up = home.split(".")[: len(home.split(".")) - (sub.level - 1)]
                base = ".".join([*up, base] if base else up)
            names = [base, *(f"{base}.{a.name}" for a in sub.names)]
        if names:
            found.append(({modules[n] for n in names if n in modules}, typed))
        for field, value in ast.iter_fields(sub):
            if isinstance(value, list):
                inner = typed or (
                    field == "body" and isinstance(sub, ast.If) and _is_type_checking(sub.test)
                )
                todo.extend(
                    (child, inner)
                    for child in value
                    if isinstance(child, ast.stmt | ast.excepthandler | ast.match_case)
                )
    return found


def imported_files(path: Path) -> set[str]:
    """The package's files *path* imports, function bodies and `TYPE_CHECKING` included: a layer a
    type crosses is crossed."""
    return {rel for files, _ in import_statements(path) for rel in files}


def import_closure(modules: Iterable[str]) -> dict[str, Path]:
    """Every package module *modules* reach through imports, to its file, themselves and each
    one's enclosing packages included (an import runs their `__init__`): the code a call into one
    can run, a function body's import included, so a module named may not be loaded yet."""
    files = package_modules()
    names = {rel: name for name, rel in files.items()}
    seen: set[str] = set()
    todo = [files[m] for m in modules if m in files]
    while todo:
        if (rel := todo.pop()) in seen:
            continue
        seen.add(rel)
        name = names[rel]
        parents = (name.rpartition(".")[0],) if "." in name else ()
        todo.extend({*imported_files(_BASE / rel), *(files[p] for p in parents)} - seen)
    return {names[rel]: _BASE / rel for rel in sorted(seen)}
