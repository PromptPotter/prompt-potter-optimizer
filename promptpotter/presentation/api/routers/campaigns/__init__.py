"""Campaign registry + per-cycle live reads.

**The imports below are the registration, not a re-export convenience** — each
submodule decorates the shared ``campaigns_router`` at import time, so emptying this
file to a namespace marker mounts zero routes and 404s the whole surface. ``tree``
and ``ray`` are not redundant: the tree answers *what descends from what*, the ray
*what happened when*, over one shared family walk so they cannot disagree about
which cycles a campaign holds.
"""

from promptpotter.presentation.api.routers.campaigns import (
    cycles,
    events,
    files,
    ray,
    registry,
    storage,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router

__all__ = [
    "campaigns_router",
    "cycles",
    "events",
    "files",
    "ray",
    "registry",
    "storage",
]
