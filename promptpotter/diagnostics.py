"""Complexity ledger — one exact number per dimension of the package's surface.

Run: ``python -m promptpotter.diagnostics``. What a raise obliges you to write down:
root ``CLAUDE.md`` § ``<surface-ledger>``. What a dimension counts — and which ones
march to a floor rather than sit at one — is on its ``_count_*`` function. Every
number is computed by introspection, never estimated, so two readers agree. Outside
the layer tree because it counts every layer: inside one, an import would invert.
"""

from __future__ import annotations

import types
import typing
from pathlib import Path

from pydantic import BaseModel

# The `promptpotter/` package dir — this module's OWN parent. Derived by name rather
# than by counting `parents[N]` hops: the hop count silently re-aims the whole ledger at
# whatever directory happens to sit N levels up, which is exactly what happened when this
# module moved up out of `diagnostics/` (it began counting the repo root, node_modules
# and all, reporting 4741 modules against a baseline of 296).
_PACKAGE_ROOT = Path(__file__).resolve().parent
assert _PACKAGE_ROOT.name == "promptpotter", f"ledger root is not the package: {_PACKAGE_ROOT}"

# ``assets/`` is install DATA the package reads, never code it imports — the optimizer
# manifest, the exported dashboard, the benchmark dataset definitions. Counting it as
# conceptual surface is wrong on its own terms, and it is not hypothetical: two of the
# three asset trees are STAGED there by ``scripts/build_release.py`` before a wheel is
# built, so a developer who has cut a release once carries ``datasets/CLAUDE.md`` inside
# the package and the ratchet goes red on a file nobody wrote.
_ASSETS_ROOT = _PACKAGE_ROOT / "assets"


def _package_files(pattern: str) -> list[Path]:
    """Files matching *pattern* under the package, excluding :data:`_ASSETS_ROOT`.

    The exclusion is sound only while ``assets/`` holds no code, so that is asserted
    rather than assumed: a ``.py`` landing there would be invisible to ``modules``,
    ``any_params`` and ``models_lax`` at once — a ratchet with a hole in it is worse than
    no ratchet. Two ways it could happen, and the message has to cover both: someone
    places a module under ``assets/``, or a benchmark dataset picks up a helper script and
    ``scripts/build_release.py`` stages it in.
    """
    stowaways = sorted(_ASSETS_ROOT.rglob("*.py")) if _ASSETS_ROOT.is_dir() else []
    assert not stowaways, (
        f"Python files under {_ASSETS_ROOT.name}/ are excluded from every ledger count, so "
        f"these are uncounted surface: {[str(p.relative_to(_PACKAGE_ROOT)) for p in stowaways]}. "
        "assets/ is install DATA — move code into the package proper, or out of the dataset "
        "that staged it."
    )
    return [p for p in _PACKAGE_ROOT.rglob(pattern) if _ASSETS_ROOT not in p.parents]


def _unwrap_optional(annotation: object) -> object:
    """Strip ``X | None`` / ``Optional[X]`` down to ``X``; pass anything else through."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _count_leaves(model: type[BaseModel], _seen: set[type[BaseModel]] | None = None) -> int:
    """Recursive leaf-field count. A nested ``BaseModel`` recurses; a scalar or a
    container (``list[Model]``, ``dict``) counts as one leaf. Cycle-safe."""
    seen = _seen if _seen is not None else set()
    if model in seen:
        return 0
    seen.add(model)
    total = 0
    for field in model.model_fields.values():
        inner = _unwrap_optional(field.annotation)
        if isinstance(inner, type) and issubclass(inner, BaseModel):
            total += _count_leaves(inner, seen)
        else:
            total += 1
    return total


def _count_any_params(py_files: list[Path]) -> int:
    """Bare ``x: Any`` / ``x: Any | None`` NAMED parameters across the package.

    Excludes ``*args``/``**kwargs: Any`` (idiomatic passthrough) and container values
    like ``dict[str, Any]`` (honest — raw JSON has no better type). What is left is the
    actionable shape: a parameter whose real type exists and simply was not written.

    Why it is a ledger dimension and not a mypy flag: ``disallow_any_explicit`` rejects
    ``dict[str, Any]`` just as hard, so it cannot express "Any is fine for JSON, not for
    a parameter with a known type". And ``strict``'s own ``warn_return_any`` does NOT
    cover it — an ``Any`` param is a complete annotation, so ``disallow_untyped_defs``
    is satisfied, and ``no-any-return`` is defeated by any expression that unions ``Any``
    with a concrete type. This count is the only thing that sees the declaration.
    """
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
    """String-keyed ``Any`` maps declared at a ``domain/`` signature — parameter or return.

    Package-wide there are ~830 and counting them all is unactionable: most are the
    node-keyed ``pipeline_params`` shape ``architecture.md`` declares, whose keys the backend
    invents at runtime, so a count flagging them gets muted. Scoped here instead — the layer
    contracted to "frozen models, pure types", at the position where a fact is PASSED between
    two pieces of our own code rather than held (a model *field* legitimately carries the
    backend's overlay). ``Mapping`` counts too, at any depth: same surface, and omitting it
    would let a rename retire the number.

    A high FLOOR. Honest: the pipeline overlay (``opt_search_point``, ``pipeline_parsing``,
    ``pipeline_schema``, ``search_point``, ``connector``), a node's output (``rendering``,
    ``validators``), a dataset row (``sample``). Retirable: ``results``, ``results_health``,
    ``l4.verdict``, ``measurement_provenance`` — rows of PP's OWN on-disk shapes, taken as bags.
    """
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


def _count_param_decls(py_files: list[Path]) -> int:
    """Every named parameter declared by every function in the package (``self``/``cls`` excluded).

    The dimension for values that travel by being RE-DECLARED at each level they pass
    through rather than riding a carrier. Nothing else here can see that: a value threaded
    six deep adds no module, no class, no config leaf and no ``Any``, so every other count
    reads flat while each new fact costs an edit at N signatures, N call sites and N
    docstrings. It is also the one shape ``<surface-ledger>`` rule 2 cannot satisfy by
    accident — moving code between files leaves this number exactly where it was.

    A TOTAL, not the >N tail this started as. The tail could not see the thing it was
    added for: bundling ``(campaign_id, cycle_id)`` onto ``CycleHop`` retired ~80
    declarations and the tail read flat, because pair-threading lives in three- and
    four-parameter functions, not in the handful of monsters. A total sees both — a
    fifteen-parameter function is fifteen of it — which is also why the threshold is
    gone: there is no cut-off left to re-aim the ratchet by moving.

    Marches to a FLOOR. Most of this number is functions taking their real arguments;
    width that ``conventions.md`` § Code shape REQUIRES — a parameter that changes what a
    number MEANS taking no default, so the signature does the enforcing — is the rule
    working, not debt. What a pass can retire is the rest: transport.
    """
    import ast

    total = 0
    for path in py_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            args = node.args
            total += sum(
                1
                for a in args.posonlyargs + args.args + args.kwonlyargs
                if a.arg not in ("self", "cls")
            )
    return total


def _count_lax_models(py_files: list[Path]) -> int:
    """Pydantic models that do NOT end up ``extra="forbid"`` — i.e. that silently drop
    unknown keys.

    Pydantic's default is ``extra="ignore"``, so a model built with a misspelled keyword
    returns a valid-looking instance. ``ObservationMapping(obs_key=…)`` — the field is
    ``output_field`` — rode a real ``pipeline.json`` that way for months with every gate
    green. Inheriting :class:`~promptpotter.domain.strict_model.StrictModel` inverts that
    default; this count is what stops the lax ones growing back.

    A FLOOR, and the 4 each name their reason on the model itself. ``RoundResult`` /
    ``ScoredCandidate``: a ``@computed_field`` round-trip — Pydantic serializes such a field
    OUT and rejects it back IN, so writing the round file and reading it back agree only
    while extras are ignored (forbid breaks every real round file on disk). ``NodePromptInfo``:
    the BACKEND owns that sub-object's vocabulary, and a backend PP does not own must be able
    to describe itself without crashing PP. ``Sample``: a dataset row carries whatever columns
    the operator's file had. ``ObservationMapping`` is deliberately NOT lax though it also
    parses ``pipeline.yaml`` — PP owns that vocabulary, so an unknown key there is a typo.

    Resolved by AST, not by import: enumerating models at runtime means importing every
    module, and the eager ``store/__init__`` aggregator makes import order a live hazard.
    """
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


def _is_reexport_shim(init_file: Path) -> bool:
    """An ``__init__`` that is only ``__all__`` + imports — a package indirecting to its leaves.

    A FLOOR, not debt: the 5 survivors are not shims and emptying them breaks the app.
    ``connectors`` IS the connector registry (import-time guards).
    ``presentation/api/routers/campaigns`` IS the route registry — its submodule imports run
    the ``@campaigns_router`` decorators, so emptying it mounts ZERO routes; it is the one
    that reads like a pure re-export and is not. ``shared``,
    ``application/scoring/formula`` and ``application/views/render`` have real code in the
    body, which this text test cannot see.
    """
    text = init_file.read_text(encoding="utf-8")
    has_all = "__all__" in text
    has_import = any(line.lstrip().startswith(("import ", "from ")) for line in text.splitlines())
    return has_all and has_import


def compute_ledger() -> dict[str, int]:
    from promptpotter.application.knobs import KNOBS
    from promptpotter.application.optimization.dispatch.injections.registry import (
        INJECTIONS,
    )
    from promptpotter.application.optimization.escalation.rules import DEFAULT_ESCALATION_RULES
    from promptpotter.config import settings as settings_mod
    from promptpotter.config.settings import PROMPT_STRING_FIELDS, Settings
    from promptpotter.domain.opt_search_point import OptSearchPoint

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
        "any_params": _count_any_params(py_files),
        "domain_any_maps": _count_domain_any_maps(py_files),
        "param_decls": _count_param_decls(py_files),
        "models_lax": _count_lax_models(py_files),
        "prompt_string_fields": len(PROMPT_STRING_FIELDS),
        "injections": len(INJECTIONS),
        "escalation_rules": len(DEFAULT_ESCALATION_RULES),
        "claude_md": len(_package_files("CLAUDE.md")),
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
