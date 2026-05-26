"""Campaign registry + per-cycle live reads.

A campaign is a **forest**: ``campaign_id`` identifies one optimization
effort (a dataset + pipeline origin + context), and it holds N *sessions*
(re-runs of the same declaration) plus their fork descendants — every
cycle flat under ``campaigns/{campaign_id}/cycles/``. Every per-cycle path
resolution carries both ids.

``dashboard.json`` is session-scoped — one telemetry stream per session
(the session root + its forks share it) — and resolves at the session's
root cycle dir. The per-cycle dashboard route resolves
``root_cycle_id(cycle_id)`` server-side.

The route surface is split across five submodules, each decorating the
shared ``campaigns_router``: ``registry`` (campaign list + detail),
``cycles`` (cycle list/detail, rounds, dashboard), ``ledger`` (log.md,
ledger stream, decisions, forks, hard samples), ``lineage`` (the
campaign-wide cladogram), and ``files`` (file-tree reads).
"""

from promptpotter.presentation.api.routers.campaigns import (
    cycles,
    events,
    files,
    ledger,
    lineage,
    registry,
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
]
