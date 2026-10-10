"""``char_cap`` is a runaway backstop, carried only by the panels the composition places WHOLE."""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
from collections.abc import Mapping
from types import MappingProxyType, ModuleType
from typing import Annotated

from promptpotter.application.optimizers.potter.dispatch import injections as _injections_pkg
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    _Injection,
    injection_registry,
)
from promptpotter.application.optimizers.potter.dispatch.layout import NODE_LAYOUTS
from promptpotter.application.optimizers.potter.escalation.state import ExplorationBudget
from promptpotter.application.optimizers.potter.records import L1Layout
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, PromptTemplate
from promptpotter.shared.hashing import shapes_optimizer_prompt

# The one citable name that is NOT a panel: the escape hatch a measured stall licenses.
STALL_EXPLORATION: Annotated[str, shapes_optimizer_prompt] = "stall_exploration"


@shapes_optimizer_prompt
@functools.cache
def renderer_modules() -> tuple[ModuleType, ...]:
    """Walked, never listed: a hand-kept tuple silently drops a module from registration and the digest. Name order is digest order."""
    return tuple(
        importlib.import_module(f"{_injections_pkg.__name__}.{m.name}")
        for m in sorted(pkgutil.iter_modules(_injections_pkg.__path__), key=lambda m: m.name)
        if m.name != "registry"
    )


@shapes_optimizer_prompt
@functools.cache
def injection_table() -> Mapping[str, _Injection]:
    modules = renderer_modules()
    table = injection_registry()
    for key, inj in table.items():
        if inj.name != key:
            raise RuntimeError(f"injection {key!r} has mismatched name {inj.name!r}.")
    # A `_r_*` renderer missing its `@signal` renders nothing yet looks live.
    wired = {inj.render for inj in table.values()}
    orphans = [
        f"{mod.__name__}.{name}"
        for mod in modules
        for name, fn in inspect.getmembers(mod, inspect.isfunction)
        if name.startswith("_r_") and fn.__module__ == mod.__name__ and fn not in wired
    ]
    if orphans:
        raise RuntimeError(
            "Orphaned injection renderers — defined but never registered "
            f"(missing an @signal decorator?): {sorted(orphans)}"
        )
    possible = frozenset().union(*(spec.possible for spec in NODE_LAYOUTS.values()))
    if not set(table) >= possible:
        raise RuntimeError(
            f"NODE_LAYOUTS possible names with no registered injection: "
            f"{sorted(possible - set(table))}"
        )
    # L2/L4 may excise anything outside `mandatory`, so the citation contract must hold on the rail ALONE.
    if not any(table[n].citable for n in NODE_LAYOUTS["l1_generate"].mandatory):
        raise RuntimeError(
            "l1_generate's mandatory placeholders render no citable panel — the "
            "evidence_grounding contract would be unsatisfiable under a legal layout edit."
        )
    return MappingProxyType(table)


@shapes_optimizer_prompt
def validate_template(name: str, template: PromptTemplate) -> None:
    """A typo'd ``{{slot}}`` is otherwise a silent empty render."""
    spec = NODE_LAYOUTS.get(name)
    extras = spec.caller_extras if spec is not None else frozenset()
    text = template.render()
    referenced = set(TEMPLATE_TOKEN_RE.findall(text))
    unknown = referenced - injection_table().keys() - extras
    if unknown:
        raise KeyError(
            f"Template {name!r} references unknown slot(s): {sorted(unknown)}. "
            f"Register a renderer (dispatch/injections/) or add it to "
            f"NODE_LAYOUTS[{name!r}].caller_extras if the slot is a caller-supplied extra."
        )


@shapes_optimizer_prompt
def citable_fields(
    layout: L1Layout,
    *,
    exploration_budget: str | None,
    rendered: Mapping[str, str],
) -> tuple[str, ...]:
    """Narrowed to what RENDERED; never empty, since an empty ``evidence_grounding.field`` enum is unsatisfiable."""
    table = injection_table()
    names = [n for n in layout.all_placeholders() if table[n].citable and rendered.get(n)]
    if not names or exploration_budget != ExplorationBudget.TIGHT:
        names.append(STALL_EXPLORATION)
    return tuple(sorted(names))
