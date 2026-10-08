"""Per-cycle reads at ANY depth — one route serves a top-level cycle, an L4 inner one, or an L5+ descendant. The dashboard
is per-cycle, so a fork's chart shows the fork's trajectory; the tree is rooted at a COURSE for the same reason."""

from __future__ import annotations

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from promptpotter.application.mask.record import parse_lens, parse_sample_ids
from promptpotter.application.mask.tree_lens import lensed_tree
from promptpotter.application.served_dashboard import served_dashboard
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.runtime_flags import (
    run_phase_validator_epoch,
)
from promptpotter.infrastructure.store.layout import (
    course_validator_ns,
    cycle_dir_for,
)
from promptpotter.infrastructure.store.lineage_queries import LineageNode
from promptpotter.infrastructure.store.stores import resolve_cycle_path
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_has_etag,
    client_seen_at_or_after,
    http_date,
    model_json,
    weak_etag,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
def get_cycle_dashboard(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    at: int | None = Query(None, ge=0),
) -> Response:
    """Live telemetry for the viewed cycle — its own ``dashboard.json``.

    ``dashboard.json`` is per-cycle: every cycle (root, fork, diag, or an
    L4 inner descendant) owns its own live file, stamped with its own
    ``cycle_id``. The path ids address the top-level (root) cycle; the optional
    ``descend`` query walks into the previous hop's ``.inner/<key>`` sandbox one
    ``campaign::cycle`` hop at a time, so ONE route serves a top-level cycle, an
    inner cycle, or an L5+ descendant (:func:`resolve_cycle_path`). Absent/empty
    ``descend`` is a plain per-cycle read — no session-root collapse.

    Honors ``If-Modified-Since`` and returns ``304 Not Modified`` when nothing the
    body depends on has advanced — keeps the 2 s webapp poll cheap during quiescent
    stretches. The validator is :func:`run_phase_validator_epoch`, which covers the
    derived phase: it turns ``detached`` on the clock, so an mtime-only validator
    would 304 a dead producer at ``running``. A cycle that exists but has not yet flushed its first
    ``dashboard.json`` answers ``warming_up`` at 200 so the webapp renders
    "initialising" rather than appearing offline; a cycle that does not exist
    answers 404, because those are different facts.

    ``at`` is a ledger offset in the LEAF cycle's own ledger and asks for the state as of
    that moment — the same fold, replayed off disk. It is the address a ray item carries,
    so a chronology step and a dashboard are the same coordinate rather than two.
    """
    stores, leaf = resolve_cycle_path(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    cycle_path = cycle_dir_for(stores.base_dir, leaf)
    # WARMING is "no dashboard YET"; a cycle dir that isn't there is GONE, and answering both
    # with the warming placeholder leaves a deleted campaign reading "initialising" forever.
    if not cycle_path.is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")
    # Conditional-GET before the body is read, which keeps the 2 s poll cheap.
    headers: dict[str, str] = {}
    mtime_epoch = run_phase_validator_epoch(cycle_path)
    if mtime_epoch is not None:
        headers["Last-Modified"] = http_date(mtime_epoch)
        if client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    return JSONResponse(served_dashboard(stores, leaf, at=at), headers=headers)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/tree",
    response_model=LineageNode,
)
def get_lineage_tree(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    lens: str | None = Query(None),
    samples: str | None = Query(None),
) -> Response:
    """The lineage tree rooted at this cycle — the single served genealogy.

    Nodes alternate ``course -> candidate -> (course | sample)`` forever, so an L4 inner
    run is the same shape one level down rather than a special case, and L5+ needs no new
    tier. There is no ``depth`` parameter: one tree per campaign serves every consumer,
    and the recursion bound is ``lineage_queries._MAX_COURSE_DEPTH``.

    An optional **lens** decorates the nodes with a counterfactual. ``lens=score:<formula>``
    = an alternative ``per_cell`` composite, each candidate's rows re-graded under it and folded
    (its ``lens_value``, plus a ``divergence`` marker where that criterion would have elected
    someone else) — the number a fresh run under that formula reports; ``lens=dials:<term=weight,…>``
    = the same said as weights, realized on each campaign's anchors and served back as the
    course's ``lens_criterion``; ``lens=abort:<variant>``,
    variant ∈ the registered eliminators' abort lenses (one ``<gate>_off`` per gate, plus
    ``all_off``) = switch off an elimination gate's abort contribution. ``samples`` = a comma-separated sample-id list (the **sample-set mask**):
    re-score over only those samples. No lens + no samples ⇒ the tree is the raw read.

    A shell, deliberately: resolve the path, build the view, serve it. The assembly rules
    live in ``store/lineage_queries.py``.
    """
    path = (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    leaf_stores, leaf = resolve_cycle_path(stores, path)
    cycle_dir = cycle_dir_for(leaf_stores.base_dir, leaf)
    if not cycle_dir.is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")

    # ONE conditional path for every query: the validator folds the mask in with the
    # mtime, so a masked read gets its own 304 (see `_conditional.py`). A 304 costs two
    # `stat()`s.
    mtime_ns = course_validator_ns(cycle_dir)
    etag = weak_etag(mtime_ns, lens, samples)
    headers = {"ETag": etag}
    if mtime_ns is not None and client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    try:
        selected = parse_lens(lens, allow_abort=True) if lens else None
        sample_ids = parse_sample_ids(samples)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    return model_json(lensed_tree(stores, path, selected, sample_ids), headers=headers)
