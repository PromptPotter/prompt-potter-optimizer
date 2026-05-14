"""HTTP read API — backend storage + campaign registry + per-cycle live reads.

Four router objects mounted at ``/api/v1`` from ``main.py``:

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

``main.py`` imports the four router names directly; this ``__init__.py``
re-exports them.
"""

from promptpotter.presentation.api.routers.active import _active_router
from promptpotter.presentation.api.routers.backends import backends_router
from promptpotter.presentation.api.routers.campaigns import (
    _MAX_FILE_ENTRIES,
    _MAX_PREVIEW_BYTES,
    campaigns_router,
)
from promptpotter.presentation.api.routers.datasets import _datasets_router

__all__ = [
    "_MAX_FILE_ENTRIES",
    "_MAX_PREVIEW_BYTES",
    "_active_router",
    "_datasets_router",
    "backends_router",
    "campaigns_router",
]
