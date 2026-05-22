"""HTTP read API — backend storage + campaign registry + per-cycle live reads.

Five router objects mounted at ``/api/v1`` from ``main.py``:

1. **Backend storage** (``backends_router``) — manages backend connections,
   syncs experiments from backends in their native format, and exposes
   pipeline discovery.

2. **Campaign registry + per-cycle live reads** (``campaigns_router``) —
   lists + views optimization campaigns (nested under
   ``/backends/{backend_id}/campaigns``) plus dashboard passthrough, log.md,
   ledger reads + filtered views (decisions, forks, files).

3. **Active-session + control plane** (``_active_router``) — active-session
   pointer, cycle list, the two sanctioned mutating endpoints
   (operator-initiated fork + stop flag), optimizer pipeline + evaluators
   meta.

4. **Datasets** (``_datasets_router``) — campaign-sourced dataset preview
   for the New Job view.

5. **Measurements** (``_measurements_router``) — cross-campaign measurement
   leverage aggregation for the M13 leverage panel.

``main.py`` imports the router names directly; this ``__init__.py``
re-exports them.
"""

from promptpotter.presentation.api.routers.active import _active_router
from promptpotter.presentation.api.routers.backends import backends_router
from promptpotter.presentation.api.routers.campaigns import campaigns_router
from promptpotter.presentation.api.routers.datasets import _datasets_router
from promptpotter.presentation.api.routers.measurements import _measurements_router

__all__ = [
    "_active_router",
    "_datasets_router",
    "_measurements_router",
    "backends_router",
    "campaigns_router",
]
