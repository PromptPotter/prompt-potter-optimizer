"""Content-addressed hashing for measurement deduplication. In ``shared/`` to avoid a circular import between the two
searchpoint modules."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType

# SHA256 truncated to 24 hex chars (96 bits) — sufficient for content-addressed
# deduplication across campaigns.  Birthday-bound collision probability stays
# negligible up to ~280 billion items.
HASH_TRUNCATE = 24

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]

__all__ = [
    "HASH_TRUNCATE",
    "content_hash",
    "dataset_hash",
    "module_source_digest",
    "optimizer_prompt_shapers",
    "shapes_optimizer_prompt",
]


def shapes_optimizer_prompt[T](obj: T) -> T:
    """Marks a definition as its decorator, a constant as ``Annotated`` metadata, or — called on
    ``__name__`` — a whole module, whose source decides an optimizer prompt's bytes."""
    return obj


def _import_bindings(stmt: ast.stmt) -> list[tuple[str, str]] | None:
    """``(bound, source)`` per name an import binds, or ``None`` for a statement that is not one."""
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


def _normalized_source(tree: ast.AST) -> str:
    """Docstrings dropped and every import reduced to the names it binds, sorted — so documenting,
    reformatting or relocating a name costs nothing, while rebinding one moves the digest."""
    bindings: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list):
                kept = []
                for s in stmts:
                    if (pairs := _import_bindings(s)) is None:
                        kept.append(s)
                    else:
                        bindings.update(pairs)
                stmts[:] = kept
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ) and (body := getattr(node, "body", None)):
            head = body[0]
            if (
                isinstance(head, ast.Expr)
                and isinstance(head.value, ast.Constant)
                and isinstance(head.value.value, str)
            ):
                del body[0]
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


# Plumbing hashed code calls without its source deciding a prompt's bytes. A CLASS is exempt by
# kind instead: its identity renders nothing, and a method or schema that renders is marked.
PLUMBING_MODULES = frozenset(
    {
        "promptpotter.infrastructure.llm.telemetry",  # records what a fill did
        "promptpotter.shared.hashing",  # the digest itself
        "promptpotter.infrastructure.store.io",  # reads files the identity hashes as data
        "promptpotter.config.paths",  # says where those files live
    }
)


class _Scope(NamedTuple):
    names: frozenset[str]
    classes: frozenset[str]
    # Bound name -> (module, name) per package import; name None where it binds a module.
    imports: dict[str, tuple[str, str | None]]


def _module_name(path: Path) -> str:
    parts = path.relative_to(_PACKAGE_ROOT.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _module_path(module: str) -> Path | None:
    base = _PACKAGE_ROOT.parent.joinpath(*module.split("."))
    return next((p for p in (base.with_suffix(".py"), base / "__init__.py") if p.is_file()), None)


def _package_imports(nodes: Iterable[ast.AST]) -> dict[str, tuple[str, str | None]]:
    table: dict[str, tuple[str, str | None]] = {}
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] != _PACKAGE_ROOT.name:
                continue
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                table[alias.asname or alias.name] = (
                    (full, None) if _module_path(full) else (node.module, alias.name)
                )
    return table


def _scope(tree: ast.Module) -> _Scope:
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign | ast.AnnAssign):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    classes = frozenset(s.name for s in tree.body if isinstance(s, ast.ClassDef))
    return _Scope(frozenset(names), classes, _package_imports(ast.walk(tree)))


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


class _Package:
    """The package's modules, parsed on demand, for resolving a name to where it is DEFINED."""

    def __init__(self) -> None:
        self._scopes: dict[str, _Scope | None] = {}

    def scope(self, module: str) -> _Scope | None:
        if module not in self._scopes:
            path = _module_path(module)
            self._scopes[module] = (
                _scope(ast.parse(path.read_text(encoding="utf-8"))) if path else None
            )
        return self._scopes[module]

    def is_class(self, module: str, name: str) -> bool:
        scope = self.scope(module)
        return scope is not None and name in scope.classes

    def resolve(self, module: str, name: str) -> tuple[str, str] | None:
        """Follows re-exports to the definition; ``None`` for a module, or outside the package."""
        seen: set[tuple[str, str]] = set()
        while (module, name) not in seen and (scope := self.scope(module)) is not None:
            seen.add((module, name))
            if name in scope.names:
                return module, name
            source = scope.imports.get(name)
            if source is None or source[1] is None:
                return None
            module, name = source[0], source[1]
        return None

    def reached(self, module: str, unit: ast.AST) -> set[tuple[str, str]]:
        """The package names *unit* reads outside its annotations — a whole module's own names
        excepted, since they are hashed with it."""
        scope = self.scope(module)
        assert scope is not None
        whole = isinstance(unit, ast.Module)
        imports = scope.imports
        if not whole:
            imports = {**imports, **_package_imports(n for n in ast.walk(unit) if n is not unit)}
        local = set() if whole else _bound_within(unit) - imports.keys()
        skip = _annotation_ids(unit)
        out: set[tuple[str, str] | None] = set()
        for node in ast.walk(unit):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                source = imports.get(node.value.id)
                if source and source[1] is None and node.value.id not in local:
                    out.add(self.resolve(source[0], node.attr))
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in local or (whole and node.id in scope.names):
                    continue
                source = imports.get(node.id)
                if source is None:
                    out.add(self.resolve(module, node.id))
                elif source[1] is not None:
                    out.add(self.resolve(source[0], source[1]))
        return {hit for hit in out if hit is not None}


def optimizer_prompt_shapers(
    hashed: Iterable[ModuleType], *, covered: Iterable[ModuleType] = ()
) -> tuple[ast.AST, ...]:
    """Every marked definition in the package, in path then source order — read off the source, so
    the set cannot depend on which modules a process happened to import. RAISES where *hashed* or
    marked code reads a package name that is not hashed, *covered*, marked, a class or plumbing: a
    helper it calls would shape the prompt for free."""
    units: list[tuple[str, ast.AST]] = [
        (_module_name(path), node)
        for path in sorted(_PACKAGE_ROOT.rglob("*.py"))
        if shapes_optimizer_prompt.__name__ in (text := path.read_text(encoding="utf-8"))
        for node in _marked(ast.parse(text))
    ]
    whole = {m for m, unit in units if isinstance(unit, ast.Module)}
    scanned = [m for m in hashed if m.__name__ not in whole]
    covered_names = whole | {m.__name__ for m in (*scanned, *covered)} | PLUMBING_MODULES
    marked = {(m, name) for m, unit in units if (name := _unit_name(unit))}
    package = _Package()
    checked = [
        *units,
        *((m.__name__, ast.parse(Path(str(m.__file__)).read_text("utf-8"))) for m in scanned),
    ]
    breaches = sorted(
        {
            f"{target[0]}.{target[1]} (read by {module})"
            for module, unit in checked
            for target in package.reached(module, unit)
            if target[0] not in covered_names
            and target not in marked
            and not package.is_class(*target)
        }
    )
    if breaches:
        raise RuntimeError(
            "hashed code reads package names nothing hashes — mark each one whose value reaches "
            f"prompt text `shapes_optimizer_prompt`: {breaches}"
        )
    return tuple(unit for _, unit in units)


def module_source_digest(*sources: ModuleType | ast.AST) -> str:
    """Hash what a set of modules and definitions DOES, for the identity of a measurement they
    decide: a docstring or an import path is free, a changed expression voids what it measured."""
    parts = [
        _normalized_source(
            s if isinstance(s, ast.AST) else ast.parse(Path(str(s.__file__)).read_text("utf-8"))
        )
        for s in sources
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def _sorted_pairs(dataset: list[Any]) -> list[tuple[str, str]]:
    """What the rows ARE, order-independent — the one definition both hashes below stand on.

    A verifier-graded row carries no label (``Sample.ground_truth is None``) and reads as ``""``:
    the sort has to be total, and a label that does not exist cannot be part of what was measured.
    """
    return sorted((d.query, d.ground_truth or "") for d in dataset)


def dataset_hash(dataset: list[Any]) -> str:
    """The rows alone, so two measurements can be asked whether they stand on the same ones.

    Deliberately NOT a slice of :func:`content_hash`, which mixes the rendered prompt and the
    pipeline config into the same digest: two campaigns over one dataset hash differently there,
    which is right for a measurement cache key and useless as an identity a consumer can compare.
    Exported beside a fitness number for exactly that comparison — the number means nothing
    without the identity of the rows it was measured on.
    """
    blob = json.dumps({"pairs": _sorted_pairs(dataset)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]


def content_hash(
    rendered_prompt: str,
    dataset: list[Any],
    pipeline_params: dict[str, Any] | None = None,
) -> str:
    """``sha256`` over rendered prompt + sorted query/ground-truth pairs + ``pipeline_params``. Sample ORDER does not affect
    it; ``pipeline_params`` is included when non-empty, so different pipeline configs hash distinctly."""
    blob_dict: dict[str, Any] = {
        "prompt": rendered_prompt,
        "pairs": _sorted_pairs(dataset),
    }
    if pipeline_params:
        blob_dict["pipeline_params"] = pipeline_params
    blob = json.dumps(blob_dict, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:HASH_TRUNCATE]
