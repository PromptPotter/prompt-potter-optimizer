"""`INJECTIONS` registry — the one dict every prompt site resolves against.

Each entry registers itself via the ``@signal("<name>", …)`` decorator at its renderer's
definition site (``injections/panels.py``, ``layer_state.py``, ``catalogues.py``, ``wounds.py``).
This module imports those modules to trigger registration, then snapshots the result. So
``grep "<slot>"`` lands on the decorated renderer in one hop — key and body are co-located,
and there is no separate dict literal to keep in sync.

`char_cap` policy (set in each ``@signal``): truncate + warn on overrun. Set for LLM-authored
text AND for the large derived/measurement panels that feed L2/L3 (diagnostics, axis_memory,
the wound channels) — a backstop so a deep-stall round (when L3 fires and these panels are
largest) can't balloon the optimizer prompt past its budget. Caps sit above the typical render
size, so healthy rounds are untouched. ``None`` only for the small, internally-capped renderers
(*_RENDER_CAP, top-K digests) and ``task_context`` (its per-field cap is finer).
"""

from __future__ import annotations

import inspect

from promptpotter.application.optimization.dispatch.hub.bundle import (
    _Injection,
    injection_registry,
)

# Imported for the @signal side effect — each module decorates its `_r_*` renderers, which
# registers them into the bundle-level registry that `injection_registry()` snapshots below.
from promptpotter.application.optimization.dispatch.hub.injections import (
    catalogues,
    layer_state,
    panels,
    wounds,
)
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.l1_layout import NODE_LAYOUTS, L1Layout

INJECTIONS: dict[str, _Injection] = injection_registry()

# The one name in an `evidence_grounding` citation that is NOT a panel: the escape hatch a
# measured stall licenses ("no panel points anywhere — explore"). Offered only when the
# escalation panel's budget has widened past `tight`.
STALL_EXPLORATION = "stall_exploration"


def citable_fields(layout: L1Layout, *, exploration_budget: str | None = None) -> tuple[str, ...]:
    """What an ``l1_generate`` variant may cite THIS round: the evidence-bearing panels the
    layout actually renders into its prompt, plus the stall escape hatch when licensed.

    Derived, never declared. A citable panel must be renderable into L1's prompt or the
    contract invites fabricated citations — so citability is the layout, filtered by
    ``_Injection.citable``, and the same call feeds the prompt's menu, the wire schema's
    enum, and the behaviour check. ``exploration_budget=None`` (the wiring layer couldn't
    determine it) offers the hatch — fail open, as the check has always done."""
    names = [n for n in layout.all_placeholders() if INJECTIONS[n].citable]
    if exploration_budget != ExplorationBudget.TIGHT:
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
    for _mod in (catalogues, layer_state, panels, wounds)
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
