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

The route surface is split across six submodules, each decorating the
shared ``campaigns_router``: ``registry`` (campaign list + detail),
``cycles`` (cycle list/detail, rounds, dashboard), ``ledger`` (log.md,
ledger stream, decisions, forks, hard samples), ``lineage`` (the
campaign-wide cladogram), ``files`` (file-tree reads), and ``events``
(the per-cycle SSE ledger stream).
"""

from promptpotter.presentation.api.routers.campaigns import (
    cycles,
    events,
    files,
    ledger,
    lineage,
    registry,
    storage,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router

__all__ = [
    "campaigns_router",
    "cycles",
    "events",
    "files",
    "ledger",
    "lineage",
    "registry",
    "storage",
]
