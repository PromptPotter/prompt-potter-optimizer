"""``char_cap`` is a runaway backstop, carried only by the panels the composition places WHOLE.
A divisible panel needs none — `compose.select` thins it to whatever the node ceiling affords.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
from collections.abc import Mapping
from types import MappingProxyType, ModuleType
from typing import Annotated

from promptpotter.application.optimization.dispatch import injections as _injections_pkg
from promptpotter.application.optimization.dispatch.bundle import (
    _Injection,
    injection_registry,
)
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.l1_layout import NODE_LAYOUTS, L1Layout
from promptpotter.domain.opt_search_point import TEMPLATE_TOKEN_RE, PromptTemplate
from promptpotter.shared.hashing import shapes_optimizer_prompt

# The one name in an `evidence_grounding` citation that is NOT a panel: the escape hatch a
# measured stall licenses ("no panel points anywhere — explore"). Offered only when the
# escalation panel's budget has widened past `tight`.
STALL_EXPLORATION: Annotated[str, shapes_optimizer_prompt] = "stall_exploration"

# Caller-supplied `compile_prompt` extras (not signals). Anything outside
# `injection_table() ∪ extras` in a template body is a typo — `validate_template` raises.
_TEMPLATE_EXTRAS: Annotated[dict[str, set[str]], shapes_optimizer_prompt] = {
    "l1_generate": {"n_variants", "citable_fields"},
    "l1_critique": set(),
    "l2_context": set(),
    "l3_plan": set(),
    "checkin": {"consultation_instruction"},
}


@shapes_optimizer_prompt
@functools.cache
def renderer_modules() -> tuple[ModuleType, ...]:
    """Walked, never listed: a hand-kept tuple drops a module from registration, the orphan check
    and the digest at once, in silence. Name order is digest order."""
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
    # A `_r_*` renderer defined in a walked module but never wired (forgot the `@signal`
    # decorator) renders nothing yet looks live.
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
    # Every name any node's layout may pick (the union of every `NODE_LAYOUTS[node].possible`)
    # must resolve to a registered injection — else `DispatchHub.fill` would KeyError at fill
    # time. Checked here, the one place both the registry and the picklists are visible (the
    # domain layer that owns NODE_LAYOUTS must not import the application-side registry).
    possible = frozenset().union(*(spec.possible for spec in NODE_LAYOUTS.values()))
    if not set(table) >= possible:
        raise RuntimeError(
            f"NODE_LAYOUTS possible names with no registered injection: "
            f"{sorted(possible - set(table))}"
        )
    # The citation contract must be satisfiable from the GUARD RAIL alone. `l1_generate` is
    # required to cite an evidence panel, and L2/L4 may excise anything outside `mandatory` —
    # so if no mandatory placeholder were citable, a legal layout edit could leave every variant
    # with nothing to cite and fail the whole round's `evidence_grounding_present`.
    if not any(table[n].citable for n in NODE_LAYOUTS["l1_generate"].mandatory):
        raise RuntimeError(
            "l1_generate's mandatory placeholders render no citable panel — the "
            "evidence_grounding contract would be unsatisfiable under a legal layout edit."
        )
    return MappingProxyType(table)


@shapes_optimizer_prompt
def validate_template(name: str, template: PromptTemplate) -> None:
    """Raise KeyError if any ``{{slot}}`` isn't a signal or known extra (typo → silent empty render)."""
    extras = _TEMPLATE_EXTRAS.get(name, set())
    text = template.render()
    referenced = set(TEMPLATE_TOKEN_RE.findall(text))
    unknown = referenced - injection_table().keys() - extras
    if unknown:
        raise KeyError(
            f"Template {name!r} references unknown slot(s): {sorted(unknown)}. "
            f"Register a renderer (dispatch/injections/) or add it to "
            f"_TEMPLATE_EXTRAS[{name!r}] if the slot is a caller-supplied extra."
        )


@shapes_optimizer_prompt
def citable_fields(
    layout: L1Layout,
    *,
    exploration_budget: str | None = None,
    rendered: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Narrowed to what actually RENDERED — offering a panel that said nothing is the phantom
    citation one level down. Never empty: an empty ``evidence_grounding.field`` enum is unsatisfiable.

    **Citability is DERIVED — never re-introduce a citable-panel list.** ``EVIDENCE_GROUNDING_FIELDS``
    was a hand-maintained frozenset the validator checked *set membership* against, so a variant
    could cite a panel the prompt never rendered and pass clean. It drifted twice: the phantom
    ``parent_panel``/``sibling_yield`` names were excised, and by the time it was deleted four of its
    nine names rendered nothing on ``l1_generate``'s floor while two rendered panels were uncitable.
    ``@signal(citable=…)`` declares evidence-vs-menu at each renderer and this function intersects
    it with the node's LIVE layout — one derivation feeding the prompt's ``{{citable_fields}}`` menu,
    the wire-schema enum and ``evidence_grounding_present``. A citable panel that never renders
    invites a fabricated citation; deriving one from the other is the only defence that holds."""
    table = injection_table()
    names = [
        n
        for n in layout.all_placeholders()
        if table[n].citable and (rendered is None or rendered.get(n))
    ]
    if not names or exploration_budget != ExplorationBudget.TIGHT:
        names.append(STALL_EXPLORATION)
    return tuple(sorted(names))
