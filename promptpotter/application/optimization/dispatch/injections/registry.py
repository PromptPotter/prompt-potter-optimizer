"""``char_cap`` is a runaway backstop, carried only by the panels the composition places WHOLE.
A divisible panel needs none — `compose.select` thins it to whatever the node ceiling affords.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Mapping

from promptpotter.application.optimization.dispatch import bundle
from promptpotter.application.optimization.dispatch.bundle import (
    _Injection,
    injection_registry,
)

# Imported for the @signal side effect — each module decorates its `_r_*` renderers, which
# registers them into the bundle-level registry that `injection_registry()` snapshots below.
from promptpotter.application.optimization.dispatch.injections import (
    catalogues,
    layer_state,
    panels,
    wounds,
)
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.l1_layout import NODE_LAYOUTS, L1Layout
from promptpotter.shared.hashing import module_source_digest

INJECTIONS: dict[str, _Injection] = injection_registry()

# The one name in an `evidence_grounding` citation that is NOT a panel: the escape hatch a
# measured stall licenses ("no panel points anywhere — explore"). Offered only when the
# escalation panel's budget has widened past `tight`.
STALL_EXPLORATION = "stall_exploration"

# The modules whose source DECIDES what an optimizer prompt contains. One tuple, because the
# orphan check below and the identity digest above must never disagree about the set: a renderer
# module missing from the digest changes every prompt and voids nothing.
_RENDERER_MODULES = (catalogues, layer_state, panels, wounds)


@functools.cache
def injection_source_digest() -> str:
    """The panels' text is code, so it sits outside ``_identity_config``'s prompt templates and
    layouts. Its estimator-side twin is ``measurement_source_digest``.

    ``bundle`` is hashed beside the renderers because the constants deciding how much of a panel a
    prompt receives live there rather than in the renderer that spends them. A module that shapes
    the prompt and is not hashed here pools corpora the fingerprint exists to keep apart.
    """
    return module_source_digest(bundle, *_RENDERER_MODULES)


def citable_fields(
    layout: L1Layout,
    *,
    exploration_budget: str | None = None,
    rendered: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Narrowed to what actually RENDERED — offering a panel that said nothing is the phantom
    citation one level down. Never empty: an empty ``evidence_grounding.field`` enum is unsatisfiable."""
    names = [
        n
        for n in layout.all_placeholders()
        if INJECTIONS[n].citable and (rendered is None or rendered.get(n))
    ]
    if not names or exploration_budget != ExplorationBudget.TIGHT:
        names.append(STALL_EXPLORATION)
    return tuple(sorted(names))


# Fail import if a slot's name drifts from its key, or a ``_r_*`` renderer is
# defined in a source module but never wired (forgot the ``@signal`` decorator):
# an orphan renders nothing yet looks live. The renderer modules are already
# imported above, so this needs no package walk.
_wired = {inj.render for inj in INJECTIONS.values()}
for _key, _inj in INJECTIONS.items():
    if _inj.name != _key:
        raise RuntimeError(f"INJECTIONS[{_key!r}] has mismatched name {_inj.name!r}.")
_orphans = [
    f"{_mod.__name__}.{_name}"
    for _mod in _RENDERER_MODULES
    for _name, _fn in inspect.getmembers(_mod, inspect.isfunction)
    if _name.startswith("_r_") and _fn.__module__ == _mod.__name__ and _fn not in _wired
]
if _orphans:
    raise RuntimeError(
        "Orphaned injection renderers — defined but never wired into INJECTIONS "
        f"(missing an @signal decorator?): {sorted(_orphans)}"
    )

# Every name any node's layout may pick (the union of every `NODE_LAYOUTS[node].possible`)
# must resolve to a registered injection — else `DispatchHub.fill` would KeyError at fill
# time. Asserted here, the one place both the registry and the picklists are visible (the
# domain layer that owns NODE_LAYOUTS must not import the application-side INJECTIONS).
_all_possible = frozenset().union(*(spec.possible for spec in NODE_LAYOUTS.values()))
if not set(INJECTIONS) >= _all_possible:
    raise RuntimeError(
        f"NODE_LAYOUTS possible names with no registered injection: "
        f"{sorted(_all_possible - set(INJECTIONS))}"
    )
del _all_possible

# The citation contract must be satisfiable from the GUARD RAIL alone. `l1_generate` is
# required to cite an evidence panel, and L2/L4 may excise anything outside `mandatory` —
# so if no mandatory placeholder were citable, a legal layout edit could leave every variant
# with nothing to cite and fail the whole round's `evidence_grounding_present`.
if not any(INJECTIONS[n].citable for n in NODE_LAYOUTS["l1_generate"].mandatory):
    raise RuntimeError(
        "l1_generate's mandatory placeholders render no citable panel — the "
        "evidence_grounding contract would be unsatisfiable under a legal layout edit."
    )

del _wired, _key, _inj, _orphans
