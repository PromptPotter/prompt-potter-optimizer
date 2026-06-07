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

INJECTIONS: dict[str, _Injection] = injection_registry()


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
del _wired, _key, _inj, _orphans
