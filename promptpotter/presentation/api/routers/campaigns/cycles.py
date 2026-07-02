"""Per-cycle reads — cycle list, cycle detail, rounds, dashboard.

All routes carry ``(campaign_id, cycle_id)``. ``dashboard.json`` is
per-cycle — the dashboard route serves the viewed cycle's own file, so a
fork's chart shows the fork's trajectory, not the session root's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from promptpotter.infrastructure.store import (
    build_stores,
    cycle_dir_for,
    read_active_pointer,
)
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.io import read_json_tolerant, validate_path_component
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.deps import StoreDep, warming_payload
from promptpotter.presentation.api.routers.active import CycleListEntry, CyclesResponse
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_seen_at_or_after,
    http_date,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError


class CycleSummary(BaseModel):
    campaign_id: str
    cycle_id: str
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    is_root: bool
    parent_cycle_id: str | None = None
    status: str = ""
    n_rounds: int = 0
    best_accuracy: float | None = None
    origin_accuracy: float | None = None
    created_at: str = ""
    updated_at: str = ""
    human_intervened: bool = False


class CampaignCyclesResponse(BaseModel):
    campaign_id: str
    cycles: list[CycleSummary] = Field(description="Every cycle in the campaign's lineage tree")


class CycleRoundEntry(BaseModel):
    round: int = Field(description="Round number within the cycle")
    label: str = Field(description="Human-readable label (winner's L1 description)")
    accuracy: float | None = Field(default=None, description="Round-level accuracy (winner)")
    improved: bool = Field(default=False, description="Whether this round improved over best")


class CycleDetailResponse(CycleSummary):
    backend_id: str = Field(default="", description="Backend this cycle optimizes against")
    rounds: list[CycleRoundEntry] = Field(description="Ordered round summaries")


@campaigns_router.get("/campaigns/{campaign_id}/cycles", response_model=CampaignCyclesResponse)
def list_campaign_cycles(store: StoreDep, campaign_id: str) -> CampaignCyclesResponse:
    """Every cycle in one campaign's lineage tree."""
    if store.campaigns.load_campaign(campaign_id) is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    cycles = [
        CycleSummary(
            campaign_id=e["campaign_id"],
            cycle_id=e["cycle_id"],
            sibling_kind=e["sibling_kind"],
            is_root=e["is_root"],
            parent_cycle_id=e["parent_cycle_id"],
            status=e["status"],
            n_rounds=e["n_rounds"],
            best_accuracy=e["best_accuracy"],
            created_at=e["created_at"],
            updated_at=e["updated_at"],
            human_intervened=e.get("human_intervened", False),
        )
        for e in store.campaigns.enumerate_cycles()
        if e["campaign_id"] == campaign_id
    ]
    return CampaignCyclesResponse(campaign_id=campaign_id, cycles=cycles)


def _round_summaries(index: dict[str, Any]) -> list[CycleRoundEntry]:
    out: list[CycleRoundEntry] = []
    rounds_raw = index.get("rounds")
    if not isinstance(rounds_raw, list):
        return out
    for r in rounds_raw:
        if not isinstance(r, dict):
            continue
        rn = r.get("round")
        if not isinstance(rn, int):
            continue
        out.append(
            CycleRoundEntry(
                round=rn,
                label=str(r.get("label") or ""),
                accuracy=(
                    float(r["accuracy"]) if isinstance(r.get("accuracy"), int | float) else None
                ),
                improved=bool(r.get("improved", False)),
            )
        )
    return out


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}", response_model=CycleDetailResponse
)
def get_cycle(store: StoreDep, campaign_id: str, cycle_id: str) -> CycleDetailResponse:
    """One cycle's ``index.json`` detail with round summaries."""
    index = store.campaigns.load(campaign_id, cycle_id)
    if index is None:
        raise NotFoundError(f"Cycle not found: {campaign_id}/{cycle_id}")
    kind = sibling_kind(cycle_id)
    return CycleDetailResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        sibling_kind=index.get("sibling_kind", kind),
        is_root=kind == "root",
        parent_cycle_id=index.get("parent_cycle_id"),
        status=str(index.get("status") or ""),
        n_rounds=int(index.get("n_rounds", 0)),
        best_accuracy=(
            float(index["best_accuracy"])
            if isinstance(index.get("best_accuracy"), int | float)
            else None
        ),
        origin_accuracy=origin_accuracy_of(index),
        created_at=str(index.get("created_at") or ""),
        updated_at=str(index.get("updated_at") or ""),
        backend_id=str((index.get("header") or {}).get("backend_id") or ""),
        rounds=_round_summaries(index),
    )


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}",
    response_model=dict[str, Any],
)
def get_round(store: StoreDep, campaign_id: str, cycle_id: str, round_num: int) -> dict[str, Any]:
    """Full round detail for one round of one cycle."""
    round_data = store.campaigns.load_round_file(campaign_id, cycle_id, round_num)
    if round_data is None:
        raise NotFoundError(f"Round {round_num} not found")
    return round_data


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
    stores, leaf_cmp, leaf_cyc = resolve_cycle_path(
        store, [(campaign_id, cycle_id), *_decode_descend(descend)]
    )
    return serve_dashboard_response(request, stores.base_dir, leaf_cmp, leaf_cyc)


def _inner_sandbox_store(store: Stores, outer_cycle_id: str) -> Stores | None:
    """A ``Stores`` rooted at the outer cycle's inner sandbox, or ``None``.

    L4 (``promptpotter-self``) runs each candidate as a real inner campaign under
    a flat, off-registry sandbox ``<workspace>/.inner/<outer_cycle_id>`` (keyed on
    the outer cycle id alone — see ``inner_recursion.publish_inner_spawn_context``).
    That sandbox is structurally a normal projects tree, so pointing
    ``build_stores`` at it lets every existing read work verbatim. Returns
    ``None`` when the viewed cycle spawned no inner campaigns (non-L4, or the
    loop hasn't recursed yet) — the caller degrades to an empty list / 404, never
    an error.
    """
    validate_path_component(outer_cycle_id)
    sandbox_root = store.projects_root.parent / ".inner" / outer_cycle_id
    if not (sandbox_root / store.tenant_id).is_dir():
        return None
    return build_stores(store.identity, projects_root=sandbox_root)


def _decode_descend(descend: str | None) -> list[tuple[str, str]]:
    """Parse the ``?descend=`` tail into ``(campaign, cycle)`` hops below the root.

    Mirrors the webapp's ``encodeDescend`` (``webapp/lib/ids.ts``): ``~``-joined
    ``campaign::cycle`` hops, empty/absent → no descent. Component-char validation
    is :func:`resolve_cycle_path`'s job; here we only reject a structurally
    malformed hop (missing ``::``) as a 400 rather than let it 500 downstream.
    """
    if not descend:
        return []
    hops: list[tuple[str, str]] = []
    for seg in descend.split("~"):
        cmp, sep, cyc = seg.partition("::")
        if not sep or not cmp or not cyc:
            raise BadRequestError(f"Malformed descend hop: {seg!r}")
        hops.append((cmp, cyc))
    return hops


def resolve_cycle_path(store: Stores, path: list[tuple[str, str]]) -> tuple[Stores, str, str]:
    """Resolve a cycle PATH (root → leaf hops) to ``(Stores, campaign, cycle)``.

    The single walk that makes an inner cycle addressable exactly like a
    top-level one: hop 0 is a cycle in the caller's own tree; each later hop lives
    in the previous hop's ``.inner/<previous cycle id>`` sandbox, so
    :func:`_inner_sandbox_store` IS the recursive step. Re-entrant by construction
    (L5+ nests the same way). Every component is char-validated before any
    filesystem touch (400 on bad chars); a missing sandbox is a 404.
    """
    if not path:
        raise BadRequestError("empty cycle path")
    cur = store
    for i, (cmp, cyc) in enumerate(path):
        try:
            validate_path_component(cmp)
            validate_path_component(cyc)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        if i == 0:
            continue
        nxt = _inner_sandbox_store(cur, path[i - 1][1])
        if nxt is None:
            raise NotFoundError(f"No inner sandbox for cycle '{path[i - 1][1]}'")
        cur = nxt
    return cur, path[-1][0], path[-1][1]


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/inner-cycles",
    response_model=CyclesResponse,
    tags=["Cycles"],
)
def get_inner_cycles(store: StoreDep, campaign_id: str, cycle_id: str) -> CyclesResponse:
    """Inner campaigns spawned by an L4 outer cycle — the fan-out this cycle ran.

    Keyed on the **viewed** outer ``cycle_id`` (the webapp can browse a past,
    non-active outer cycle), not the active pointer. Reuses the same webapp-picker
    shape as ``GET /cycles`` (one ``CycleListEntry`` per inner cycle); the
    ``active_campaign_id`` / ``active_cycle_id`` fields carry the **inner**
    sandbox's live pointer, so the sidebar can mark the inner loop running right
    now. A non-L4 cycle (no ``.inner/`` sandbox) returns an empty list — that
    absence is what tells the webapp to show no expand affordance.
    """
    inner = _inner_sandbox_store(store, cycle_id)
    if inner is None:
        return CyclesResponse(
            tenant_id=store.tenant_id,
            active_campaign_id=None,
            active_cycle_id=None,
            cycles=[],
        )
    _, live_cmp, live_cid = read_active_pointer(store.tenant_id, projects_root=inner.projects_root)
    entries = inner.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_campaign_id=live_cmp or None,
        active_cycle_id=live_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )
