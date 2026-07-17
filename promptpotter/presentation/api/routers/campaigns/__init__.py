"""Campaign registry + per-cycle live reads.

A campaign is a **forest**: ``campaign_id`` identifies one optimization
effort (a dataset + pipeline origin + context), and it holds N *sessions*
(re-runs of the same declaration) plus their fork descendants — every
cycle flat under ``campaigns/{campaign_id}/cycles/``. Every per-cycle path
resolution carries both ids.

``dashboard.json`` is per-cycle — every cycle (root, fork, sweep, diag)
owns its own live telemetry file, stamped with its own ``cycle_id``. The
per-cycle dashboard route serves the file for the cycle passed in (no
session-root collapse).

**The imports below are the registration, not a re-export convenience.** Each
submodule decorates the shared ``campaigns_router`` at import time, so emptying
this file to a namespace marker — the fate of the package ``__init__`` files
that only re-exported — mounts a router with zero routes and 404s the whole
surface. It stays.

The route surface is split across submodules, each decorating the shared
``campaigns_router``: ``registry`` (campaign list + detail), ``cycles``
(cycle list/detail, rounds, dashboard, and the ``tree`` — the one served
genealogy), ``files`` (file-tree reads — every on-disk artifact, ``log.md``
and ``hard_samples.json`` among them), ``storage``, and ``events`` (the
per-cycle SSE ledger stream, the one live tail).
"""

from promptpotter.presentation.api.routers.campaigns import (
    cycles,
    events,
    files,
    registry,
    storage,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router

__all__ = [
    "campaigns_router",
    "cycles",
    "events",
    "files",
    "registry",
    "storage",
]
