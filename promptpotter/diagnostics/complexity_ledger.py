"""Complexity ledger — one exact number per dimension of the package's surface.

Why this exists: an AI told to "simplify" reaches for *additive-but-safe* moves
(extract a helper, fold two copies into a shared util, split a big file). Each
adds a module header + an import line at every call site, so the total grows
while every commit says "refactor". The moves that actually shrink the mental
model — deleting a mechanism, re-inlining a single-use module, removing a dead
knob — are riskier, so they get skipped. This ledger makes accretion *visible*:
every dimension is computed exactly by introspection (no heuristics that can
drift from reality), and a ratchet test (`tests/test_complexity_ledger.py`)
fails CI if any dimension rises. A "simplification" pass that raises the ledger
is a feature wearing a refactor's label.

Run it: ``python -m promptpotter.diagnostics.complexity_ledger``

Each count is deliberately mechanical so two readers (and two LLM sessions) get
the same number:

- ``modules`` / ``init_files`` — ``.py`` file counts under ``promptpotter/``.
- ``reexport_shims`` — ``__init__.py`` files that BOTH declare ``__all__`` AND
  contain an import statement (i.e. they re-export, so a reader must hop through
  them). Empty namespace-marker ``__init__`` files are excluded.
- ``config_leaf_fields`` — ``len(KNOBS)``, the knob registry walked off
  ``CampaignConfig``'s own ``Knob`` metadata. The registry IS the leaf taxonomy, so
  the ledger reads it rather than carrying a fourth opinion on what a config leaf is.
- ``settings_env`` — ``len(Settings.model_fields)``.
- ``settings_const`` — module-level constants exported by ``settings`` (the
  upper-case names in its ``__all__``; excludes the ``Settings`` class + instance).
- ``opt_search_point_fields`` — recursive leaf count of ``OptSearchPoint``.
- ``any_params`` — bare ``x: Any`` named parameters (not ``*args``/``**kwargs``, not
  ``dict[str, Any]``). A type the checker cannot see is surface a reader must carry in
  their head, and it silently disarms ``warn_return_any`` — see ``_count_any_params``.
  Like ``reexport_shims``, this one is meant to march to a floor, not sit at one.
- ``prompt_string_fields`` — ``len(PROMPT_STRING_FIELDS)``.
- ``injections`` — ``len(INJECTIONS)`` (the dispatch-hub registry self-validates).
- ``escalation_rules`` — ``len(DEFAULT_ESCALATION_RULES)``, the policy surface
  that replaced the FSM.
- ``claude_md`` — ``CLAUDE.md`` count under ``promptpotter/``.
"""

from __future__ import annotations

import types
import typing
from pathlib import Path

from pydantic import BaseModel

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


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
    with a concrete type. ``resp.content or ""`` (`connectors/llm_only.py`, pre-2026-07-17)
    was exactly that: the ``or`` had no runtime job, only a type-checker one, and deleting
    it made the error fire instantly. Nothing recorded that, so a debt sweep read the
    guard as a cosmetic no-op. This count is the only thing that sees the declaration.
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


def _is_reexport_shim(init_file: Path) -> bool:
    text = init_file.read_text(encoding="utf-8")
    has_all = "__all__" in text
    has_import = any(line.lstrip().startswith(("import ", "from ")) for line in text.splitlines())
    return has_all and has_import


def compute_ledger() -> dict[str, int]:
    """Return the current surface count for every tracked dimension."""
    from promptpotter.application.knobs import KNOBS
    from promptpotter.application.optimization.dispatch.injections.registry import (
        INJECTIONS,
    )
    from promptpotter.application.optimization.escalation.rules import DEFAULT_ESCALATION_RULES
    from promptpotter.config import settings as settings_mod
    from promptpotter.config.settings import PROMPT_STRING_FIELDS, Settings
    from promptpotter.domain.opt_search_point import OptSearchPoint

    py_files = list(_PACKAGE_ROOT.rglob("*.py"))
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
        "prompt_string_fields": len(PROMPT_STRING_FIELDS),
        "injections": len(INJECTIONS),
        "escalation_rules": len(DEFAULT_ESCALATION_RULES),
        "claude_md": len(list(_PACKAGE_ROOT.rglob("CLAUDE.md"))),
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
