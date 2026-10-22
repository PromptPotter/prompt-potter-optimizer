"""The package's conceptual surface, counted — and the ratchet that holds it where it stands.

    python scripts/complexity_ledger.py            # print the ledger
    python scripts/complexity_ledger.py --check    # the gate's `surface-ledger` check

Move a number in ``BASELINE`` and you owe a written reason — root ``CLAUDE.md`` §
``<surface-ledger>``, rules in ``docs/developer/reasoning-doctrine.md``. The reason goes in the
COMMIT BODY, and ``git log -p`` is the history layer: ``BASELINE`` is only where the surface
stands now, never a target to reach. The check asserts EQUALITY, so a win nobody re-pins cannot
become silent headroom for the next raise.

A script and not a module of the package: it counts every layer, the suite and the generated
contract beside them, none of which an installed wheel carries.
"""

from __future__ import annotations

import ast
import collections
import json
import sys
import typing
from collections.abc import Iterator
from pathlib import Path

import tests_owning
from pydantic import BaseModel

BASELINE = {
    # Every `.py` under the package.
    "modules": 449,
    # Every `__init__.py` among them.
    "init_files": 56,
    # The `__init__.py` files that re-export names instead of staying empty.
    "reexport_shims": 5,
    # Campaign-config knobs, plus the node knobs each optimizer member declares.
    "config_leaf_fields": 89,
    # `Settings` fields — what the environment can set.
    "settings_env": 43,
    # The upper-case constants `config/settings.py` exports.
    "settings_const": 3,
    # Leaves of `OptSearchPoint` — what a run is handed.
    "opt_search_point_fields": 10,
    # Leaves of `CycleResult` — what a run produces.
    "cycle_result_fields": 671,
    # Parameters annotated `Any`.
    "any_params": 46,
    # `dict[str, Any]` maps declared in `domain/`.
    "domain_any_maps": 66,
    # Models that opt out of `StrictModel`'s `extra="forbid"`.
    "models_lax": 3,
    # The fields a prompt node bears when its backend declares none (`DEFAULT_PROMPT_FIELDS`).
    "default_prompt_fields": 6,
    # Injections the optimizers' runtimes price — each a block an optimizer prompt can carry.
    "injections": 34,
    # Escalation rules, priced the same way: the round, climb and heal tables together, since a
    # transition written as a branch beside the tables was policy the count could not see.
    "escalation_rules": 12,
    # Function-local imports of the package's own modules. The bare invocation's first-run check
    # is an application read, and `--help` loads no use case, so it is named as a string in `main()`.
    "deferred_imports": 7,
    # `CLAUDE.md` files under the package.
    "claude_md": 21,
    # `tests/test_*.py` — `tests/CLAUDE.md` names what each one owns.
    "test_files": 6,
    # Test functions across them: one journey per `# N.` section, plus the forks a section names
    # (`tests/CLAUDE.md` § One journey per section).
    "test_functions": 25,
    # Modules a test run imports: our own, followed through their module-level imports, plus
    # each outside distribution's top name. A test that reaches the server app or an extra
    # moves it as one function does not.
    "test_reach": 363,
    # Every property of every schema the generated contract offers the browser.
    "served_fields": 1728,
}

_REPO = Path(__file__).resolve().parents[1]
_PACKAGE_ROOT = _REPO / "promptpotter"

# ``assets/`` is install DATA the package reads, never code it imports — the optimizer
# manifest, the exported dashboard, the benchmark dataset definitions. Counting it as
# conceptual surface is wrong on its own terms: the dashboard is STAGED there by
# ``scripts/build_release.py`` before a wheel is built, and ``assets/datasets/CLAUDE.md``
# documents data, not a layer.
_ASSETS_ROOT = _PACKAGE_ROOT / "assets"

# The suite beside the package. `tests/CLAUDE.md` fixes its files and admits a function only
# through three axes, and a rule that is prose alone is the mechanism of organic growth —
# four files landed in one arc against it. A raise costs the same written reason as any other
# row: name the invariant and the axis it clears.
_TESTS_ROOT = _REPO / "tests"


def _test_files() -> list[Path]:
    return sorted(_TESTS_ROOT.glob("test_*.py"))


def _count_test_functions(files: list[Path]) -> int:
    return sum(
        line.startswith(("def test_", "async def test_"))
        for f in files
        for line in f.read_text(encoding="utf-8").splitlines()
    )


def _count_test_reach(test_files: list[Path], graph: dict[str, set[str]]) -> int:
    local = {p.stem for p in _TESTS_ROOT.glob("*.py")} | {"tests", "__future__"}
    roots: set[str] = set()
    for path in [*test_files, _TESTS_ROOT / "conftest.py", _TESTS_ROOT / "factories.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name = tests_owning._module_name(path.relative_to(_REPO).as_posix())
        roots |= tests_owning._import_names(tree, name, False, deferred=True)
    reached: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in reached or name not in graph:
            continue
        reached.add(name)
        parts = name.split(".")
        todo.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        todo.extend(graph[name])
    outside = {
        top
        for name in roots.union(*(graph[m] for m in reached))
        if (top := name.split(".")[0]) not in local
        and top != "promptpotter"
        and top not in sys.stdlib_module_names
    }
    return len(reached) + len(outside)


# What the browser is OFFERED — every property of every schema in the generated contract, so the
# roster is the app's own answer and not a hand-kept list. An unread field costs a baseline edit
# like any other: writing one is a line, finding its reader is a grep, so it is added silently.
_CONTRACT = _REPO / "docs" / "specs" / "openapi.generated.json"


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


def _models_in(annotation: object) -> list[type[BaseModel]]:
    """Every model an annotation holds, through ``Annotated``, unions and container arguments."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    if typing.get_origin(annotation) is typing.Annotated:
        return _models_in(typing.get_args(annotation)[0])
    return [model for arg in typing.get_args(annotation) for model in _models_in(arg)]


def _count_leaves(model: type[BaseModel], _path: tuple[type[BaseModel], ...] = ()) -> int:
    """A field holding models — ``Model | None``, ``list[Model]``, ``dict[K, Annotated[A | B, …]]``
    — is each model's whole nested surface, not one leaf.
    The guard is the PATH, never a seen-set: one model at two sibling fields is two surfaces.

    REBUILD FIRST, or the count is a reading of what happened to be imported. Pydantic resolves a
    forward reference lazily, on first validation — so an unresolved annotation is no ``BaseModel``
    yet and prices as ONE leaf, and the same model prices its whole subtree once anything in the
    process has built one. ``RoundResult.health`` hid ``DegradationHealth``'s 17 leaves that way,
    visible or not depending on what the process had imported. A resolved model rebuilds to a
    no-op."""
    if model in _path:
        return 0
    model.model_rebuild()
    path = (*_path, model)
    total = 0
    for field in model.model_fields.values():
        nested = _models_in(field.annotation)
        total += sum(_count_leaves(n, path) for n in nested) if nested else 1
    return total


_Function = ast.FunctionDef | ast.AsyncFunctionDef


def _nodes(tree: ast.AST) -> Iterator[tuple[ast.AST, int]]:
    """Every node of *tree* with the number of functions enclosing it, in ``ast.walk``'s order."""
    todo: collections.deque[tuple[ast.AST, int]] = collections.deque([(tree, 0)])
    while todo:
        node, depth = todo.popleft()
        yield node, depth
        inside = depth + isinstance(node, _Function)
        todo.extend((child, inside) for child in ast.iter_child_nodes(node))


def _is_bare_any(annotation: ast.expr) -> bool:
    """``Any`` or ``Any | None``. Not a mypy flag: ``disallow_any_explicit`` rejects an honest
    ``dict[str, Any]`` just as hard, and ``warn_return_any`` cannot see a param at all — an
    ``Any`` IS a complete annotation."""
    if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
        right = annotation.right
        if not (isinstance(right, ast.Constant) and right.value is None):
            return False
        annotation = annotation.left
    return isinstance(annotation, ast.Name) and annotation.id == "Any"


_ANY_MAPS = ("dict[str, Any]", "Mapping[str, Any]")


def _declared_extra(model: ast.ClassDef) -> str | None:
    extra: str | None = None
    for stmt in model.body:
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
    return extra


def _count_lax_models(bases: dict[str, list[str]], declared: dict[str, str | None]) -> int:
    """Models that do NOT end up ``extra="forbid"`` — Pydantic's default returns a valid-looking
    instance from a misspelled keyword. AST, not import: eager package init makes import order a
    hazard."""

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


def _source_counts(py_files: list[Path], graph: dict[str, set[str]]) -> dict[str, int]:
    """The four counts read off source, in ONE parse and one walk per module.

    A deferred import counts once per function enclosing it, so a closure's import is charged to
    the closure and to the function that holds it. ``domain_any_maps`` is scoped to ``domain/``
    because package-wide the shape is mostly the backend's own runtime-keyed overlay, and a count
    that flags what nobody may fix gets muted."""
    domain_root = _PACKAGE_ROOT / "domain"
    any_params = any_maps = deferred = 0
    bases: dict[str, list[str]] = {}
    declared: dict[str, str | None] = {}
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        in_domain = domain_root in path.parents
        tree = ast.parse(source)
        name = tests_owning._module_name(path.relative_to(_REPO).as_posix())
        graph[name] = tests_owning._import_names(
            tree, name, path.name == "__init__.py", deferred=False
        )
        for node, depth in _nodes(tree):
            if isinstance(node, _Function):
                args = node.args
                annotations = [a.annotation for a in args.posonlyargs + args.args + args.kwonlyargs]
                any_params += sum(ann is not None and _is_bare_any(ann) for ann in annotations)
                if in_domain:
                    any_maps += sum(
                        ann is not None and any(s in ast.unparse(ann) for s in _ANY_MAPS)
                        for ann in (*annotations, node.returns)
                    )
            elif isinstance(node, ast.ClassDef):
                if names := [b.id for b in node.bases if isinstance(b, ast.Name)]:
                    bases[node.name] = names
                    declared[node.name] = _declared_extra(node)
            if depth and _imports_own_package(node):
                context = lines[max(0, node.lineno - 2) : node.end_lineno or node.lineno]
                deferred += depth * (not any(_EXTRAS_MARKER in line for line in context))
    return {
        "any_params": any_params,
        "domain_any_maps": any_maps,
        "models_lax": _count_lax_models(bases, declared),
        "deferred_imports": deferred,
    }


def _is_reexport_shim(init_file: Path) -> bool:
    """A TEXT test — it cannot see a body that also holds real code, or imports whose
    side effect IS the registry, so what it flags is named in the baseline, not emptied."""
    text = init_file.read_text(encoding="utf-8")
    has_all = "__all__" in text
    has_import = any(line.lstrip().startswith(("import ", "from ")) for line in text.splitlines())
    return has_all and has_import


def compute_ledger() -> dict[str, int]:
    from promptpotter.application import optimizers
    from promptpotter.application.knobs import KNOBS, member_knob_count
    from promptpotter.config import settings as settings_mod
    from promptpotter.config.settings import Settings
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.results import CycleResult
    from promptpotter.domain.search_point import DEFAULT_PROMPT_FIELDS

    py_files = _package_files("*.py")
    init_files = [p for p in py_files if p.name == "__init__.py"]
    graph: dict[str, set[str]] = {}
    source = _source_counts(py_files, graph)
    priced: collections.Counter[str] = collections.Counter()
    for runtime in optimizers.runtimes().values():
        priced.update(runtime.priced_surface)

    return {
        "modules": len(py_files),
        "init_files": len(init_files),
        "reexport_shims": sum(1 for p in init_files if _is_reexport_shim(p)),
        # The overlay leaf stands for the node knobs each member declares, counted instead.
        "config_leaf_fields": len(KNOBS) - 1 + member_knob_count(),
        "settings_env": len(Settings.model_fields),
        "settings_const": sum(1 for name in settings_mod.__all__ if name.isupper()),
        "opt_search_point_fields": _count_leaves(OptSearchPoint),
        # The mirror of the row above: what a run PRODUCES, rooted at the one model that
        # reaches every round and candidate, so no hand-listed roster can drift from it.
        "cycle_result_fields": _count_leaves(CycleResult),
        "any_params": source["any_params"],
        "domain_any_maps": source["domain_any_maps"],
        "models_lax": source["models_lax"],
        "default_prompt_fields": len(DEFAULT_PROMPT_FIELDS),
        **priced,
        "deferred_imports": source["deferred_imports"],
        "claude_md": len(_package_files("CLAUDE.md")),
        "test_files": len(test_files := _test_files()),
        "test_functions": _count_test_functions(test_files),
        "test_reach": _count_test_reach(test_files, graph),
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


def check(ledger: dict[str, int]) -> list[str]:
    """What stands between *ledger* and ``BASELINE`` — empty where they are equal."""
    if set(ledger) != set(BASELINE):
        moved = sorted(set(ledger) ^ set(BASELINE))
        return [f"complexity-ledger dimensions changed ({moved}); update BASELINE in this commit"]
    risen = {k: (v, BASELINE[k]) for k, v in ledger.items() if v > BASELINE[k]}
    fallen = {k: (v, BASELINE[k]) for k, v in ledger.items() if v < BASELINE[k]}
    failures = []
    if risen:
        failures.append(
            "conceptual surface grew (dimension: actual vs baseline) — a simplification "
            f"pass must lower the ledger, not raise it: {risen}. If this is a justified "
            "feature, raise the baseline deliberately; otherwise subtract instead of add."
        )
    if fallen:
        failures.append(
            "conceptual surface SHRANK while the baseline still reads the old number "
            f"(dimension: actual vs baseline): {fallen}. Lower it in this commit — a win "
            "nobody re-pins becomes silent headroom for the next raise, and the pass that "
            "earned it keeps no number to show for it."
        )
    return failures


if __name__ == "__main__":
    counted = compute_ledger()
    if "--check" not in sys.argv[1:]:
        print(_format(counted))
        raise SystemExit(0)
    if moved := check(counted):
        print("\n".join(moved) + "\nBASELINE lives in scripts/complexity_ledger.py.")
    raise SystemExit(bool(moved))
