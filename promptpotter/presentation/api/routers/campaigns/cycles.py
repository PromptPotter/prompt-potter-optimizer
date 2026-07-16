"""Per-cycle reads — the live dashboard for any cycle at any depth.

All routes carry ``(campaign_id, cycle_id)``. ``dashboard.json`` is
per-cycle — the dashboard route serves the viewed cycle's own file, so a
fork's chart shows the fork's trajectory, not the session root's.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.stores import resolve_cycle_path
from promptpotter.presentation.api.deps import (
    StoreDep,
    decode_descend,
    warming_payload,
)
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_seen_at_or_after,
    http_date,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


def serve_dashboard_response(
    request: Request, base_dir: Path, campaign_id: str, cycle_id: str
) -> Response:
    """304 / warming / atomic ``dashboard.json`` read under any tenant ``base_dir``.

    The single dashboard-serving path: the outer per-cycle route passes the
    caller's ``store.base_dir``; the inner-cycle route passes a sandbox store's
    ``base_dir`` (``.inner/<outer_cycle_id>/<tenant>``). One implementation, two
    roots — so the 304 / warming-fallback / atomic-read semantics can't drift
    between the outer and inner dashboards.
    """
    cycle_path = cycle_dir_for(base_dir, campaign_id, cycle_id)
    path = cycle_path / "dashboard.json"
    present = path.is_file()

    # Conditional-GET once, before reading the body: Last-Modified rides the
    # dashboard mtime when present, else the cycle dir's mtime (so polling
    # clients still get cheap 304s while a fresh campaign warms up). The read
    # only happens after the 304 check passes — keeps the 2 s poll cheap.
    try:
        mtime_epoch = (path if present else cycle_path).stat().st_mtime
        headers = {"Last-Modified": http_date(mtime_epoch)}
        if client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    except FileNotFoundError:
        headers = {}

    # ``run_phase`` rides dashboard.json itself (declared by the runner, projected
    # by LiveDashboardView) — the webapp reads it straight off the 2 s poll, so a
    # paused run reads "paused" with no separate /runstate round-trip. The spend
    # cap rides ``dashboard.json::run_limits.spend_budget_usd`` (the single
    # authoritative budget source); the deleted /runstate ``spend_cap_usd`` was unused.
    body = read_json_tolerant(path) if present else None
    if body is None:
        # Missing OR corrupt (half-written / truncated): degrade to the warming
        # placeholder rather than 500 on the 2 s poll. A present-but-unreadable
        # file carries a reason so the panel can say so, matching the SSE tail.
        body = warming_payload(campaign_id, cycle_id)
        if present:
            body["reason"] = "dashboard_unreadable"
    return JSONResponse(body, headers=headers)


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
def get_cycle_dashboard(
    request: Request,
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
) -> Response:
    """Live telemetry for the viewed cycle — its own ``dashboard.json``.

    ``dashboard.json`` is per-cycle: every cycle (root, fork, sweep, diag, or an
    L4 inner descendant) owns its own live file, stamped with its own
    ``cycle_id``. The path ids address the top-level (root) cycle; the optional
    ``descend`` query walks into the ``.inner/<previous cycle id>`` sandbox one
    ``campaign::cycle`` hop at a time, so ONE route serves a top-level cycle, an
    inner cycle, or an L5+ descendant (:func:`resolve_cycle_path`). Absent/empty
    ``descend`` is a plain per-cycle read — no session-root collapse.

    Honors ``If-Modified-Since`` and returns ``304 Not Modified`` when the
    on-disk mtime hasn't advanced — keeps the 2 s webapp poll cheap during
    quiescent stretches. When ``dashboard.json`` does not yet exist (fresh
    campaign before origin has flushed its first snapshot), returns a
    ``warming_up`` payload at 200 instead of 404 so the webapp can render a
    "campaign initialising" placeholder rather than appear offline.
    """
    stores, leaf = resolve_cycle_path(
        store, (CycleHop(campaign_id, cycle_id), *decode_descend(descend))
    )
    return serve_dashboard_response(request, stores.base_dir, leaf.campaign_id, leaf.cycle_id)
