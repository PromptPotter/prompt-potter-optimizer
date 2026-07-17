"""Per-cycle reads — the live dashboard and the lineage tree, for any cycle at any depth.

All routes carry ``(campaign_id, cycle_id)`` plus an optional ``descend``, so ONE route
serves a top-level cycle, an L4 inner cycle, or an L5+ descendant. ``dashboard.json`` is
per-cycle — the dashboard route serves the viewed cycle's own file, so a fork's chart
shows the fork's trajectory, not the session root's. The tree route is the same idea for
lineage: rooted at a COURSE, because a campaign is a bag of courses and a campaign-rooted
read cannot describe a leaf below depth 1.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from promptpotter.application.mask.divergence import Verdict, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRecord
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.application.scoring.metrics import value_with_mask_applied
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.lineage_views import (
    LineageDivergence,
    LineageNode,
    build_lineage_tree,
)
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
from promptpotter.shared.errors import BadRequestError, NotFoundError

# Abort-lens variants → the PoBB contributor(s) to switch off (the thin API-edge selector
# for the abort verdict; see docs/specs/mask-projection.md).
_ABORT_SUPPRESS: dict[str, frozenset[str]] = {
    "epsilon_off": frozenset({"epsilon"}),
    "lock_in_off": frozenset({"lock_in"}),
    "all_off": frozenset({"epsilon", "lock_in"}),
}


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
        store, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    return serve_dashboard_response(request, stores.base_dir, leaf.campaign_id, leaf.cycle_id)


def _subtree_mtime(base_dir: Path, campaign_id: str, cycle_id: str) -> float | None:
    """Newest mtime across this course's lineage inputs.

    The **ledger** is the validator that matters: it bumps the moment a candidate is
    minted, so the 2 s poll picks up an in-flight candidate at its start — which
    ``dashboard.json``'s mtime never guaranteed, because it only moved at round close.
    ``index.json`` covers a fork or an inner run appearing beneath. Scoped to this course:
    a child course's own poll validates itself.
    """
    cdir = cycle_dir_for(base_dir, campaign_id, cycle_id)
    newest: float | None = None
    for p in (cdir / ".runtime" / "ledger.jsonl", cdir / "index.json"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if newest is None or m > newest:
            newest = m
    return newest


def _resolve_verdict(lens: str | None) -> Verdict:
    """The thin API-edge selector: one ``lens`` value → its verdict strategy.
    ``abort:<variant>`` → the abort verdict; ``score:<formula>`` (or empty ⇒ the accuracy
    default, used by a samples-only mask) → the scoring verdict. A bad value is a clean 400."""
    if lens and lens.startswith("abort:"):
        variant = lens.removeprefix("abort:")
        suppress = _ABORT_SUPPRESS.get(variant)
        if suppress is None:
            raise BadRequestError(
                f"Unknown abort lens: {variant!r} (expected one of {sorted(_ABORT_SUPPRESS)})"
            )
        return make_abort_verdict(suppress)
    if lens and not lens.startswith("score:"):
        raise BadRequestError(
            f"Unknown lens: {lens!r} (expected 'score:<formula>' or 'abort:<variant>')"
        )
    formula = lens.removeprefix("score:") if lens else None
    try:
        return make_scoring_verdict(compile_round_scorer(formula))
    except (ValueError, SyntaxError) as exc:
        raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc


def _parse_samples(samples: str | None) -> frozenset[int] | None:
    """Parse the ``samples`` lens param — a comma-separated sample-id list — into a frozenset.
    Empty / unset ⇒ None (full-set mask). A non-integer token is a 400."""
    if not samples or not samples.strip():
        return None
    try:
        ids = frozenset(int(tok) for tok in samples.split(",") if tok.strip())
    except ValueError as exc:
        raise BadRequestError(f"Invalid samples list: {samples!r} ({exc})") from exc
    return ids or None


class _Overlay:
    """The lens folded over one campaign's record, indexed for the tree walk.

    The record is campaign-scoped, so this decorates the nodes of the courses it covers and
    leaves an inner run's own courses untouched — an inner campaign is a different campaign
    and the lens says nothing about it. Silence there is honest; fabricating it would not be.
    """

    def __init__(self, record: MaskRecord, lens: str | None, sample_ids: frozenset[int] | None):
        result = find_divergences(record, _resolve_verdict(lens))
        self.diverged: dict[tuple[str, int], LineageDivergence] = {
            (d.cycle_id, d.round): LineageDivergence(
                alternative_candidate_id=d.alternative_candidate_id
            )
            for d in result.divergences
        }
        # `node_key` is the mask's own `{cycle_id}::r{round}` key — consumed here and never
        # served: the tree carries the fact on the node instead.
        self.dimmed: frozenset[tuple[str, int]] = frozenset(
            (cyc.cycle_id, rnd.round)
            for cyc in record.cycles
            for rnd in cyc.rounds
            if f"{cyc.cycle_id}::r{rnd.round}" in set(result.divergent)
        )
        self.criterion = (
            compile_round_scorer(lens.removeprefix("score:"))
            if lens and lens.startswith("score:")
            else None
        )
        self.subset: dict[tuple[str, str], tuple[float | None, int]] = (
            {
                (cyc.cycle_id, cand.candidate_id): (
                    cand.accuracy if cand.n_scored > 0 else None,
                    cand.n_scored,
                )
                for cyc in record.cycles
                for rnd in cyc.rounds
                for cand in rnd.candidates
            }
            if sample_ids
            else {}
        )

    def apply(self, node: LineageNode) -> LineageNode:
        """Decorate one node, then its children — the tree walk."""
        kids = [self.apply(k) for k in node.children]
        if node.kind != "candidate" or node.round is None or not node.path:
            return node.model_copy(update={"children": kids})
        key = (node.path[-1].cycle_id, node.round)
        subset = self.subset.get((node.path[-1].cycle_id, node.id))
        return node.model_copy(
            update={
                "children": kids,
                # The marker sits on the SPINE node — the winner is who the lens would have
                # replaced, so it is the node the fork would have happened at.
                "divergence": self.diverged.get(key) if node.is_winner else None,
                "divergent": key in self.dimmed,
                "lens_value": (
                    value_with_mask_applied(node.evaluators, self.criterion)
                    if self.criterion
                    else None
                ),
                "sample_set_accuracy": subset[0] if subset else None,
                "sample_set_n": subset[1] if subset else None,
            }
        )


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/tree",
    response_model=LineageNode,
)
def get_lineage_tree(
    request: Request,
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    depth: int = Query(1, ge=0, le=8),
    lens: str | None = Query(None),
    samples: str | None = Query(None),
) -> Response:
    """The lineage tree rooted at this cycle — the single served genealogy.

    Nodes alternate ``course -> candidate -> (course | sample)`` forever, so an L4 inner
    run is the same shape one level down rather than a special case, and L5+ needs no new
    tier. ``depth`` expands N course-levels.

    An optional **lens** decorates the nodes with a counterfactual. ``lens=score:<formula>``
    = an alternative scoring formula (each candidate's ``lens_value``, plus a ``divergence``
    marker where that criterion would have elected someone else); ``lens=abort:<variant>``,
    variant ∈ {``epsilon_off``, ``lock_in_off``, ``all_off``} = switch off a PoBB abort
    contributor. ``samples`` = a comma-separated sample-id list (the **sample-set mask**):
    re-score over only those samples. No lens + no samples ⇒ the tree is the raw read.

    A shell, deliberately: resolve the path, build the view, serve it. The assembly rules
    live in ``store/lineage_views.py`` — they were re-derived per surface before, which is
    how five witnesses came to disagree about the same node.
    """
    path = (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    stores, leaf = resolve_cycle_path(store, path)
    if not cycle_dir_for(stores.base_dir, leaf.campaign_id, leaf.cycle_id).is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")

    # Conditional fast-path — only on the UNMASKED poll. A masked body depends on
    # `lens`/`samples`, which `If-Modified-Since` can't capture, so those always recompute;
    # the 2 s webapp poll never carries a lens.
    mtime_epoch = _subtree_mtime(stores.base_dir, leaf.campaign_id, leaf.cycle_id)
    headers = {"Last-Modified": http_date(mtime_epoch)} if mtime_epoch is not None else {}
    if (
        lens is None
        and samples is None
        and mtime_epoch is not None
        and client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch)
    ):
        return Response(status_code=304, headers=headers)

    tree = build_lineage_tree(store, path, depth=depth)
    sample_ids = _parse_samples(samples)
    if lens or sample_ids:
        # One record read: an `abort:` lens reads the firing log rather than evaluators, so it
        # loads the full set. The same record feeds the divergence fold AND the subset
        # re-score — no double-score.
        is_abort = bool(lens and lens.startswith("abort:"))
        record = load_mask_record(stores, leaf.campaign_id, sample_ids if not is_abort else None)
        tree = _Overlay(record, lens, sample_ids if not is_abort else None).apply(tree)
    return JSONResponse(tree.model_dump(mode="json"), headers=headers)
