from __future__ import annotations

import ast
import functools
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

ID_HEX = 16
# 16 hex collides at archive scale (cells = configurations x samples); 64 strains Windows paths.
ADDRESS_HEX = 32


def _import_bindings(stmt: ast.stmt) -> list[tuple[str, str]] | None:
    if isinstance(stmt, ast.If):
        nested = [_import_bindings(s) for s in (*stmt.body, *stmt.orelse)]
        if not nested or any(n is None for n in nested):
            return None
        return [pair for n in nested if n is not None for pair in n]
    if isinstance(stmt, ast.ImportFrom):
        return [(a.asname or a.name, a.name) for a in stmt.names]
    if isinstance(stmt, ast.Import):
        return [
            (a.asname, a.name.rsplit(".", 1)[-1]) if a.asname else (a.name.split(".")[0], a.name)
            for a in stmt.names
        ]
    return None


def _is_prose(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def module_source_digest(*modules: ModuleType) -> str:
    return stable_hash(
        [normalized_module(Path(str(m.__file__)).read_text("utf-8")) for m in modules]
    )


class Imported(NamedTuple):
    """One import statement as written: `module` None for `import a.b` (its `names` dotted), else
    `from <level dots><module> import <names>`; `typed` under ``TYPE_CHECKING``."""

    level: int
    module: str | None
    names: tuple[str, ...]
    typed: bool


class ModuleCode(NamedTuple):
    normalized: str
    imports: tuple[Imported, ...]


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def written_imports(tree: ast.Module) -> tuple[Imported, ...]:
    """Each import statement of *tree*, function bodies included."""
    found: list[Imported] = []
    # An import is a statement, so no expression is walked; a function body is.
    todo: list[tuple[ast.AST, bool]] = [(tree, False)]
    while todo:
        sub, typed = todo.pop()
        if isinstance(sub, ast.Import):
            found.append(Imported(0, None, tuple(a.name for a in sub.names), typed))
        elif isinstance(sub, ast.ImportFrom):
            names = tuple(a.name for a in sub.names)
            found.append(Imported(sub.level, sub.module or "", names, typed))
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
    return tuple(found)


def normalized_module(text: str) -> str:
    return module_code(text).normalized


@functools.cache
def module_code(text: str) -> ModuleCode:
    """One parse of a module's text, memoized on the text, never the module (an edit on disk is
    never answered by an older read): every digest of code reads it, the import closure's walk
    included, so a text is parsed and normalized once a process. Its code less its prose:
    documenting, reformatting or relocating a name is free; rebinding one moves the digest."""
    tree = ast.parse(text)
    imports = written_imports(tree)
    bindings: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list):
                kept = []
                for s in stmts:
                    if _is_prose(s):
                        continue
                    if (pairs := _import_bindings(s)) is None:
                        kept.append(s)
                    else:
                        bindings.update(pairs)
                stmts[:] = kept
        body = getattr(node, "body", None)
        if isinstance(body, list) and not body:
            body.append(ast.Pass())
    return ModuleCode(f"{sorted(bindings)}\n{ast.unparse(tree)}", imports)


def stable_hash(value: Any, *, length: int = ID_HEX) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:length]


CANONICAL_PIN: dict[str, Any] = {"b": 0.1, "a": {"ü": [1.5, None], "A": "Zürich 東京"}}
assert stable_hash(CANONICAL_PIN, length=ADDRESS_HEX) == "bff2239dc003d203bdf1154c1e7222c6", (
    "every stored key is cut from this canonical form: a change here re-keys the archive"
)


def dataset_hash(dataset: Iterable[Any]) -> str:
    """Over each row as its bank file stores it, IN ORDER: one hash is one bank, every slot included."""
    return stable_hash({"rows": [row.banked() for row in dataset]}, length=ADDRESS_HEX)


def content_hash(
    dataset: Iterable[Any],
    pipeline_params: dict[str, Any] | None,
    *,
    answer_terms: Iterable[str],
) -> str:
    """Each node's rendered ``prompt`` rides its config, so *pipeline_params* covers every prompt."""
    blob_dict: dict[str, Any] = {"dataset": dataset_hash(dataset)}
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    if library := sorted(set(answer_terms)):
        blob_dict["answer_terms"] = library
    return stable_hash(blob_dict, length=ADDRESS_HEX)
