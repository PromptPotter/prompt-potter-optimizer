"""The package's import walk: which of its files a module imports, read off the source. Stdlib
only and importing nothing of the package but its hashing, so a gate script reads it beside the
landing key."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from promptpotter.shared.hashing import normalized_tree

_PACKAGE = Path(__file__).resolve().parents[1]


def _tree(path: Path) -> Path:
    """The tree *path* sits in, its files named from there (`promptpotter/x/y.py`): a script walks
    its own checkout, never the one the interpreter imported the package from."""
    return next(p.parent for p in path.resolve().parents if not (p.parent / "__init__.py").exists())


def _module_name(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


@cache
def package_modules(tree: Path) -> dict[str, str]:
    """Each module of the package in *tree*: its dotted name, to its file."""
    rels = (p.relative_to(tree).as_posix() for p in (tree / _PACKAGE.name).rglob("*.py"))
    return {_module_name(rel): rel for rel in rels}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def import_statements(path: Path) -> list[tuple[set[str], bool]]:
    """Each package file one import statement of *path* names, and whether the statement sits
    under ``TYPE_CHECKING``; function bodies included."""
    return _imports(path, ast.parse(path.read_text(encoding="utf-8")))


def _imports(path: Path, parsed: ast.Module) -> list[tuple[set[str], bool]]:
    root = _tree(path)
    importer = _module_name(path.resolve().relative_to(root).as_posix())
    home = importer if path.name == "__init__.py" else importer.rpartition(".")[0]
    modules = package_modules(root)
    return [
        ({modules[n] for n in names if n in modules}, typed)
        for names, typed in _named(parsed, home)
    ]


def package_imports(path: Path) -> set[str]:
    """The package modules a file outside the package (a plugin's) imports by name, function
    bodies and `TYPE_CHECKING` included."""
    modules = package_modules(_PACKAGE.parent)
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    return {n for names, _ in _named(parsed, home="") for n in names if n in modules}


def _named(parsed: ast.Module, home: str) -> list[tuple[list[str], bool]]:
    """Each import statement's dotted names, a relative one read from *home*, and whether it sits
    under ``TYPE_CHECKING``."""
    found: list[tuple[list[str], bool]] = []
    # An import is a statement, so no expression is walked; a function body is.
    todo: list[tuple[ast.AST, bool]] = [(parsed, False)]
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
            found.append((names, typed))
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


def import_closure(modules: Iterable[str]) -> dict[str, str]:
    """Every package module *modules* reach through imports, to its code less its prose
    (`hashing.py::normalized_tree`, off the one parse its walk read), themselves and each one's
    enclosing packages included (an import runs their `__init__`): the code a call into one can
    run, a function body's import included, so a module named may not be loaded yet."""
    root = _PACKAGE.parent
    files = package_modules(root)
    names = {rel: name for name, rel in files.items()}
    seen: dict[str, str] = {}
    todo = [files[m] for m in modules if m in files]
    while todo:
        if (rel := todo.pop()) in seen:
            continue
        parsed = ast.parse((root / rel).read_text(encoding="utf-8"))
        name = names[rel]
        parents = (name.rpartition(".")[0],) if "." in name else ()
        imported = {f for found, _ in _imports(root / rel, parsed) for f in found}
        seen[rel] = normalized_tree(parsed)
        todo.extend({*imported, *(files[p] for p in parents)} - seen.keys())
    return {names[rel]: seen[rel] for rel in sorted(seen)}
