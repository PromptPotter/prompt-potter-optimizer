from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NewType, TypedDict

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType

ID_HEX = 16
# 16 hex collides at archive scale (cells = configurations x samples); 64 strains Windows paths.
ADDRESS_HEX = 32

_PACKAGE_NAME = __name__.split(".")[0]

__all__ = [
    "ADDRESS_HEX",
    "ID_HEX",
    "NormalizedSource",
    "SourceFacts",
    "SourceReads",
    "UnitFacts",
    "content_hash",
    "dataset_hash",
    "module_source_digest",
    "shapes_optimizer_prompt",
    "source_facts",
    "stable_hash",
]


def shapes_optimizer_prompt[T](obj: T) -> T:
    return obj


NormalizedSource = NewType("NormalizedSource", str)


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


def _normalized_source(tree: ast.AST) -> str:
    """Documenting, reformatting or relocating a name is free; rebinding one moves the digest."""
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
    return f"{sorted(bindings)}\n{ast.unparse(tree)}"


def _is_marker(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == shapes_optimizer_prompt.__name__


def _marked(tree: ast.Module) -> list[ast.AST]:
    if any(
        isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and _is_marker(s.value.func)
        for s in tree.body
    ):
        return [tree]
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if any(_is_marker(d) for d in node.decorator_list):
                found.append(node)
        elif isinstance(node, ast.AnnAssign) and any(map(_is_marker, ast.walk(node.annotation))):
            found.append(node)
    return found


def _package_imports(nodes: Iterable[ast.AST]) -> list[list[str]]:
    return [
        [alias.asname or alias.name, node.module, alias.name]
        for node in nodes
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[0] == _PACKAGE_NAME
        for alias in node.names
    ]


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign | ast.AnnAssign):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _unit_name(unit: ast.AST) -> str | None:
    if isinstance(unit, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return unit.name
    if isinstance(unit, ast.AnnAssign) and isinstance(unit.target, ast.Name):
        return unit.target.id
    return None


def _bound_within(unit: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(unit):
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif (
            isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.ExceptHandler
            )
            and node is not unit
            and node.name
        ):
            names.add(node.name)
    return names


def _annotation_ids(unit: ast.AST) -> set[int]:
    roots = [
        a
        for node in ast.walk(unit)
        for a in (
            node.annotation if isinstance(node, ast.arg | ast.AnnAssign) else None,
            getattr(node, "returns", None),
        )
        if a is not None
    ]
    return {id(n) for root in roots for n in ast.walk(root)}


class SourceReads(TypedDict):
    """A whole module's own names are left out: they are hashed with it."""

    names: list[str]
    attrs: list[list[str]]
    imports: list[list[str]]


class UnitFacts(TypedDict):
    name: str | None
    whole: bool
    source: str
    reads: SourceReads


class SourceFacts(TypedDict):
    """A function of ONE source text: ``source_scan.py`` resolves another module's imports."""

    names: list[str]
    classes: list[str]
    imports: list[list[str]]
    reads: SourceReads
    units: list[UnitFacts]


def _reads(unit: ast.AST, names: set[str], imported: set[str]) -> SourceReads:
    whole = isinstance(unit, ast.Module)
    own = [] if whole else _package_imports(n for n in ast.walk(unit) if n is not unit)
    bound = imported | {row[0] for row in own}
    local = set() if whole else _bound_within(unit) - bound
    skip = _annotation_ids(unit)
    read_names: set[str] = set()
    read_attrs: set[tuple[str, str]] = set()
    for node in ast.walk(unit):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in bound:
                read_attrs.add((node.value.id, node.attr))
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in local
            and not (whole and node.id in names)
        ):
            read_names.add(node.id)
    return {
        "names": sorted(read_names),
        "attrs": [list(pair) for pair in sorted(read_attrs)],
        "imports": own,
    }


def source_facts(text: str) -> SourceFacts:
    tree = ast.parse(text)
    names = _top_level_names(tree)
    imports = _package_imports(ast.walk(tree))
    imported = {row[0] for row in imports}
    marked = _marked(tree)
    module_reads = _reads(tree, names, imported)
    unit_reads = [module_reads if u is tree else _reads(u, names, imported) for u in marked]
    # Last, and in marked order: normalizing rewrites the tree, an outer unit's pass included.
    units: list[UnitFacts] = [
        {
            "name": _unit_name(unit),
            "whole": unit is tree,
            "source": _normalized_source(unit),
            "reads": reads,
        }
        for unit, reads in zip(marked, unit_reads, strict=True)
    ]
    return {
        "names": sorted(names),
        "classes": sorted(s.name for s in tree.body if isinstance(s, ast.ClassDef)),
        "imports": imports,
        "reads": module_reads,
        "units": units,
    }


def module_source_digest(*sources: ModuleType | NormalizedSource) -> str:
    parts = [
        s
        if isinstance(s, str)
        else _normalized_source(ast.parse(Path(str(s.__file__)).read_text("utf-8")))
        for s in sources
    ]
    return stable_hash(parts)


def stable_hash(value: Any, *, length: int = ID_HEX) -> str:
    # Every stored key is cut from this canonical form: a change here re-keys the archive.
    blob = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:length]


def _sorted_pairs(dataset: list[Any]) -> list[tuple[str, str]]:
    return sorted((d.query, d.ground_truth or "") for d in dataset)


def dataset_hash(dataset: list[Any]) -> str:
    return stable_hash({"pairs": _sorted_pairs(dataset)}, length=ADDRESS_HEX)


def content_hash(
    rendered_prompt: str,
    dataset: list[Any],
    pipeline_params: dict[str, Any] | None = None,
) -> str:
    blob_dict: dict[str, Any] = {
        "prompt": rendered_prompt,
        "pairs": _sorted_pairs(dataset),
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    return stable_hash(blob_dict, length=ADDRESS_HEX)
