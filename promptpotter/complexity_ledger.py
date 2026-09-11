"""Move a number here and you owe a written reason — root ``CLAUDE.md`` § ``<surface-ledger>``.
Outside the layer tree because it counts every layer: inside one, an import would invert.
"""

from __future__ import annotations

import ast
import json
import types
import typing
from pathlib import Path

from pydantic import BaseModel

# The `promptpotter/` package dir — this module's OWN parent, derived by NAME so that moving
# this file trips the assert below instead of silently re-aiming the ledger N levels up, where
# it counts the repo root, node_modules and all, and still reports a number.
_PACKAGE_ROOT = Path(__file__).resolve().parent
assert _PACKAGE_ROOT.name == "promptpotter", f"ledger root is not the package: {_PACKAGE_ROOT}"

# ``assets/`` is install DATA the package reads, never code it imports — the optimizer
# manifest, the exported dashboard, the benchmark dataset definitions. Counting it as
# conceptual surface is wrong on its own terms, and it is not hypothetical: two of the
# three asset trees are STAGED there by ``scripts/build_release.py`` before a wheel is
# built, so a developer who has cut a release once carries ``datasets/CLAUDE.md`` inside
# the package and the ratchet goes red on a file nobody wrote.
_ASSETS_ROOT = _PACKAGE_ROOT / "assets"

# The suite beside the package. `tests/CLAUDE.md` fixes it at six files and admits a function
# only through three axes, and a rule that is prose alone is the mechanism of organic growth —
# four files landed in one arc against it. A raise costs the same written reason as any other
# row: name the invariant and the axis it clears.
_TESTS_ROOT = _PACKAGE_ROOT.parent / "tests"


def _test_files() -> list[Path]:
    return sorted(_TESTS_ROOT.glob("test_*.py"))


def _count_test_functions(files: list[Path]) -> int:
    return sum(
        line.startswith(("def test_", "async def test_"))
        for f in files
        for line in f.read_text(encoding="utf-8").splitlines()
    )


# What the browser is OFFERED — every property of every schema in the generated contract, so the
# roster is the app's own answer and not a hand-kept list. An unread field costs a baseline edit
# like any other: writing one is a line, finding its reader is a grep, so it is added silently.
_CONTRACT = _PACKAGE_ROOT.parent / "docs" / "specs" / "openapi.generated.json"


def _count_served_fields() -> int:
    schemas = json.loads(_CONTRACT.read_text(encoding="utf-8"))["components"]["schemas"]
    return sum(len(s.get("properties", {})) for s in schemas.values())


def _package_files(pattern: str) -> list[Path]:
    stowaways = sorted(_ASSETS_ROOT.rglob("*.py")) if _ASSETS_ROOT.is_dir() else []
    assert not stowaways, (
        f"Python files under {_ASSETS_ROOT.name}/ are excluded from every ledger count, so "
        f"these are uncounted surface: {[str(p.relative_to(_PACKAGE_ROOT)) for p in stowaways]}. "
        "assets/ is install DATA — move code into the package proper, or out of the dataset "
        "that staged it."
    )
    return [p for p in _PACKAGE_ROOT.rglob(pattern) if _ASSETS_ROOT not in p.parents]


def _unwrap_optional(annotation: object) -> object:
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _count_leaves(model: type[BaseModel], _seen: set[type[BaseModel]] | None = None) -> int:
    """A ``list[Model]`` or ``dict[str, Model]`` field is a whole nested surface, not one leaf —
    counting it as one is how a model grows unwatched under a ratcheted row."""
    seen = _seen if _seen is not None else set()
    if model in seen:
        return 0
    seen.add(model)
    total = 0
    for field in model.model_fields.values():
        inner = _unwrap_optional(field.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            total += _count_leaves(inner, seen)
            continue
        nested = [
            arg
            for arg in typing.get_args(inner)
            if isinstance(arg, type) and issubclass(arg, BaseModel)
        ]
        total += sum(_count_leaves(n, seen) for n in nested) if nested else 1
    return total


def _count_any_params(py_files: list[Path]) -> int:
    """Not a mypy flag: ``disallow_any_explicit`` rejects an honest ``dict[str, Any]`` just as
    hard, and ``warn_return_any`` cannot see a param at all — an ``Any`` IS a complete annotation."""
    import ast

    total = 0
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = node.args
            for arg in args.posonlyargs + args.args + args.kwonlyargs:
                if arg.annotation is None:
                    continue
                if ast.unparse(arg.annotation) in ("Any", "Any | None"):
                    total += 1
    return total


def _count_domain_any_maps(py_files: list[Path]) -> int:
    """Scoped to ``domain/`` because package-wide the shape is mostly the backend's own
    runtime-keyed overlay, and a count that flags what nobody may fix gets muted."""
    import ast

    domain_root = _PACKAGE_ROOT / "domain"
    total = 0
    for path in (p for p in py_files if domain_root in p.parents):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = node.args
            annotations = [a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs]
            annotations.append(node.returns)
            total += sum(
                1
                for ann in annotations
                if ann is not None
                and any(
                    spelling in ast.unparse(ann)
                    for spelling in ("dict[str, Any]", "Mapping[str, Any]")
                )
            )
    return total


def _count_lax_models(py_files: list[Path]) -> int:
    """Models that do NOT end up ``extra="forbid"`` — Pydantic's default returns a valid-looking instance from a misspelled
    keyword. AST, not import: eager package init makes import order a hazard."""
    import ast

    bases: dict[str, list[str]] = {}
    declared: dict[str, str | None] = {}
    for path in py_files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            names = [b.id for b in node.bases if isinstance(b, ast.Name)]
            if not names:
                continue
            extra: str | None = None
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign) or not any(
                    isinstance(t, ast.Name) and t.id == "model_config" for t in stmt.targets
                ):
                    continue
                value = stmt.value
                keys: dict[object, ast.expr] = {}
                if isinstance(value, ast.Call):
                    keys = {kw.arg: kw.value for kw in value.keywords if kw.arg}
                elif isinstance(value, ast.Dict):
                    keys = {
                        k.value: v
                        for k, v in zip(value.keys, value.values, strict=True)
                        if isinstance(k, ast.Constant)
                    }
                node_extra = keys.get("extra")
                if isinstance(node_extra, ast.Constant):
                    extra = str(node_extra.value)
            bases[node.name] = names
            declared[node.name] = extra

    def is_model(name: str, seen: frozenset[str] = frozenset()) -> bool:
        if name == "BaseModel":
            return True
        if name in seen or name not in bases:
            return False
        return any(is_model(b, seen | {name}) for b in bases[name])

    def effective_extra(name: str, seen: frozenset[str] = frozenset()) -> str | None:
        if name in seen or name not in bases:
            return None
        if declared.get(name):
            return declared[name]
        for base in bases[name]:
            found = effective_extra(base, seen | {name})
            if found:
                return found
        return None

    return sum(
        1
        for name in bases
        if name != "BaseModel" and is_model(name) and effective_extra(name) != "forbid"
    )


# A function-local import of our OWN package, `importlib.import_module` included unless its
# argument is a literal naming another package. Sanctioned for one reason, which the import must
# declare: it gates an optional extra, so hoisting it would make the core un-importable without
# that extra (ADR-0006). Mark those `# extras: <name>` on the import line or the one above; the
# rest are debt and count here. Why startup and cycles do not excuse one:
# `docs/developer/conventions.md` § Code shape.
_EXTRAS_MARKER = "# extras:"


def _imports_own_package(node: ast.AST) -> typing.TypeGuard[ast.stmt | ast.expr]:
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").startswith("promptpotter")
    if isinstance(node, ast.Import):
        return any(a.name.startswith("promptpotter") for a in node.names)
    if not (isinstance(node, ast.Call) and node.args):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "import_module":
        return False
    target = node.args[0]
    return not (isinstance(target, ast.Constant) and isinstance(target.value, str)) or (
        target.value.startswith("promptpotter")
    )


def _count_deferred_imports(py_files: list[Path]) -> int:
    total = 0
    for path in py_files:
        try:
            tree = ast.parse(src := path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for inner in ast.walk(node):
                if not _imports_own_package(inner):
                    continue
                context = lines[max(0, inner.lineno - 2) : inner.end_lineno or inner.lineno]
                total += not any(_EXTRAS_MARKER in line for line in context)
    return total


def _is_reexport_shim(init_file: Path) -> bool:
    """A TEXT test — it cannot see a body that also holds real code, or imports whose
    side effect IS the registry, so what it flags is named in the baseline, not emptied."""
    text = init_file.read_text(encoding="utf-8")
    has_all = "__all__" in text
    has_import = any(line.lstrip().startswith(("import ", "from ")) for line in text.splitlines())
    return has_all and has_import


def compute_ledger() -> dict[str, int]:
    from promptpotter.application.knobs import KNOBS
    from promptpotter.application.optimization.dispatch.injections.registry import (
        injection_table,
    )
    from promptpotter.application.optimization.escalation.rules import DEFAULT_ESCALATION_RULES
    from promptpotter.config import settings as settings_mod
    from promptpotter.config.settings import PROMPT_STRING_FIELDS, Settings
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import CycleResult

    py_files = _package_files("*.py")
    init_files = [p for p in py_files if p.name == "__init__.py"]

    return {
        "modules": len(py_files),
        "init_files": len(init_files),
        "reexport_shims": sum(1 for p in init_files if _is_reexport_shim(p)),
        "config_leaf_fields": len(KNOBS),
        "settings_env": len(Settings.model_fields),
        "settings_const": sum(1 for name in settings_mod.__all__ if name.isupper()),
        "opt_search_point_fields": _count_leaves(OptSearchPoint),
        # The mirror of the row above: what a run PRODUCES, rooted at the one model that
        # reaches every round and candidate, so no hand-listed roster can drift from it.
        "cycle_result_fields": _count_leaves(CycleResult),
        "any_params": _count_any_params(py_files),
        "domain_any_maps": _count_domain_any_maps(py_files),
        "models_lax": _count_lax_models(py_files),
        "prompt_string_fields": len(PROMPT_STRING_FIELDS),
        "injections": len(injection_table()),
        "escalation_rules": len(DEFAULT_ESCALATION_RULES),
        "deferred_imports": _count_deferred_imports(py_files),
        "claude_md": len(_package_files("CLAUDE.md")),
        "test_files": len(test_files := _test_files()),
        "test_functions": _count_test_functions(test_files),
        "served_fields": _count_served_fields(),
    }


def _format(ledger: dict[str, int]) -> str:
    width = max(len(k) for k in ledger)
    rows = [f"  {k.ljust(width)}  {v:>4}" for k, v in ledger.items()]
    return (
        "complexity ledger\n"
        + "\n".join(rows)
        + f"\n  {'TOTAL'.ljust(width)}  {sum(ledger.values()):>4}"
    )


if __name__ == "__main__":
    print(_format(compute_ledger()))
