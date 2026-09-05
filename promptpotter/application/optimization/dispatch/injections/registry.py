"""``char_cap`` is a runaway backstop, carried only by the panels the composition places WHOLE.
A divisible panel needs none — `compose.select` thins it to whatever the node ceiling affords.
"""

from __future__ import annotations

import functools
import importlib
import inspect
import pkgutil
from collections.abc import Mapping
from types import ModuleType

from promptpotter.application.optimization.dispatch import bundle, compose
from promptpotter.application.optimization.dispatch import injections as _injections_pkg
from promptpotter.application.optimization.dispatch.bundle import (
    _Injection,
    injection_registry,
)
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.l1_layout import NODE_LAYOUTS, L1Layout
from promptpotter.shared.hashing import module_source_digest

# The modules whose source DECIDES what an optimizer prompt contains. WALKED, never listed: the
# import is also the @signal side effect that registers each module's `_r_*` renderers, so a
# hand-authored tuple can omit a module three ways at once — its panels never register, the
# orphan check below never scans it, and the identity digest never hashes it, all in silence.
# Sorted by module name, which is the order the digest is taken in.
_RENDERER_MODULES: tuple[ModuleType, ...] = tuple(
    importlib.import_module(f"{_injections_pkg.__name__}.{_m.name}")
    for _m in sorted(pkgutil.iter_modules(_injections_pkg.__path__), key=lambda m: m.name)
    if _m.name != "registry"
)

INJECTIONS: dict[str, _Injection] = injection_registry()

# The one name in an `evidence_grounding` citation that is NOT a panel: the escape hatch a
# measured stall licenses ("no panel points anywhere — explore"). Offered only when the
# escalation panel's budget has widened past `tight`.
STALL_EXPLORATION = "stall_exploration"


def fingerprinted_modules() -> tuple[ModuleType, ...]:
    """Every module whose source shapes an optimizer prompt, in digest order. The panels' text is
    code, so it sits outside ``_identity_config``'s prompt templates and layouts; its estimator-side
    twin is ``connectors/promptpotter.py::measurement_modules``.

    ``bundle`` is hashed beside the renderers because the constants deciding how much of a panel a
    prompt receives live there rather than in the renderer that spends them, ``compose`` because
    it decides which of those panels a prompt receives AT ALL, and ``facade`` because it picks the
    allowance and derives the mandatory/exempt sets those two are handed. A module that shapes the
    prompt and is not hashed here pools corpora the fingerprint exists to keep apart — which is why
    the renderer half is WALKED rather than listed, and why what a move costs is counted at the mint
    (``jobs/mint.py::_warn_on_novel_instrument``) rather than pinned as a name census.

    ``domain.ruler`` because ``theta_caveat`` and the two collapse thresholds decide whether the
    ``confounds`` panel says a round's θ is ability at all — a verdict the served reading and the
    panel share, so it shapes the prompt from outside this package.

    ``facade`` imports this module, so it is resolved at call time rather than above.
    """
    from promptpotter.application.optimization.dispatch import facade
    from promptpotter.domain import ruler

    return (bundle, compose, facade, ruler, *_RENDERER_MODULES)


@functools.cache
def injection_source_digest() -> str:
    return module_source_digest(*fingerprinted_modules())


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
# an orphan renders nothing yet looks live. Scans the walked set, so a module
# added to the package is scanned without being named anywhere.
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
