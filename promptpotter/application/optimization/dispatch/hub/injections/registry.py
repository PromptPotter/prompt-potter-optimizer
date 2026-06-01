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

from promptpotter.application.optimization.dispatch.hub.bundle import (
    _Injection,
    injection_registry,
)

# Imported for the @signal side effect — each module decorates its `_r_*` renderers, which
# registers them into the bundle-level registry that `injection_registry()` snapshots below.
from promptpotter.application.optimization.dispatch.hub.injections import (
    catalogues,  # noqa: F401
    layer_state,  # noqa: F401
    panels,  # noqa: F401
    wounds,  # noqa: F401
)

INJECTIONS: dict[str, _Injection] = injection_registry()
