"""``char_cap`` is a runaway backstop, carried only by the panels the composition places WHOLE."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, ClassVar

from promptpotter.application.optimizers.nodes import LlmNode
from promptpotter.application.optimizers.potter.dispatch import injections as _injections_pkg
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    _Injection,
    injection_registry,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    NODE_LAYOUTS,
    layout_levers,
    resolve_layout_override,
)
from promptpotter.application.optimizers.potter.records import ExplorationBudget, L1Layout
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.shared.named_rows import walked

# The one citable name that is NOT a panel: the escape hatch a measured stall licenses.
STALL_EXPLORATION: str = "stall_exploration"


@functools.cache
def injection_table() -> Mapping[str, _Injection]:
    # Walked, never listed: a hand-kept tuple silently drops a module from registration.
    modules = walked(_injections_pkg, skip="registry")
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


class LayoutNode(LlmNode):
    name: ClassVar[str]
    outer_levers: ClassVar[Mapping[str, str]] = {"layout": "object"}

    def resolved_levers(self, declared: Mapping[str, Any]) -> dict[str, Any]:
        return layout_levers(self.name, declared)

    def lever_refusals(self, declared: Mapping[str, Any]) -> list[str]:
        _, breaches = resolve_layout_override(self.name, declared.get("layout"))
        return (
            [f"its layout edit breaks {sorted(o.validator_id for o in breaches)}"]
            if breaches
            else []
        )

    def unknown_slots(self, template: OptimizerPromptTemplate) -> set[str]:
        return template.unknown_slots(
            injection_table().keys() | NODE_LAYOUTS[self.name].caller_extras
        )


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
