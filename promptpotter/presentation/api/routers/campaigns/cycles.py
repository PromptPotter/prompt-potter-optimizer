"""Per-cycle reads at ANY depth — one route serves a top-level cycle, an L4 inner one, or an L5+ descendant. The dashboard
is per-cycle, so a fork's chart shows the fork's trajectory; the tree is rooted at a COURSE for the same reason."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from promptpotter.application import optimizers
from promptpotter.application.evidence.subjects import LENS_SCORE_PREFIX
from promptpotter.application.jobs.quota import next_launch_limits
from promptpotter.application.mask.divergence import Verdict, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskReading, MaskRecord, parse_sample_ids
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.scoring.formula import LENS_DIALS_PREFIX, ScoringFormulaError
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import Cut, CycleDir, CycleHop, CyclePath, WorkspaceDir
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.spend import CeilingMeter
from promptpotter.infrastructure.projections.live_dashboard.projection import fold_at
from promptpotter.infrastructure.projections.live_dashboard.state import (
    LiveDashboardState,
    overlay_criterion_dials,
    overlay_spend_metered,
    overlay_verify,
    warming_payload,
)
from promptpotter.infrastructure.runtime_flags import (
    derive_run_phase,
    overlay_armed_controls,
    run_phase_validator_epoch,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    course_validator_ns,
    cycle_dir_for,
)
from promptpotter.infrastructure.store.lineage_queries import (
    LineageDivergence,
    LineageNode,
    build_lineage_tree,
)
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
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

if TYPE_CHECKING:
    from collections.abc import Callable

    from promptpotter.application.optimizers.nodes import Eliminator


def _abort_lenses() -> dict[str, frozenset[str]]:
    """Every registered eliminator's abort-lens variants, read per request: the member table
    completes at a declared step, never at import."""
    return {
        variant: gates
        for member in optimizers.registered().values()
        if member.kind is NodeKind.ELIMINATOR
        for variant, gates in cast("Eliminator", member).abort_lenses.items()
    }


def serve_dashboard_response(
    request: Request,
    base_dir: WorkspaceDir,
    campaign_id: str,
    cycle_id: str,
    *,
    meter: CeilingMeter | None,
    next_launch: Callable[[], dict[str, float | int | None]] | None = None,
    at: int | None = None,
) -> Response:
    """The single dashboard-serving path — the outer route passes the caller's ``base_dir``, the inner a sandbox's. One
    implementation, two roots, so 304 / warming / atomic-read semantics cannot drift between them.

    ``at`` asks for a PAST moment: the same state replayed to that ledger offset instead of the
    head's materialized file. One route rather than two, because "the dashboard" and "the
    dashboard at a moment" are one question with a default, and a second route would be a second
    answer to it."""
    hop = CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
    cycle_path = cycle_dir_for(base_dir, hop)
    # WARMING is "no dashboard YET"; a cycle dir that isn't there is GONE. Answering
    # both with the warming placeholder conflates a transient state with a terminal
    # one: a deleted campaign then reads "initialising" forever, and the webapp gets
    # no signal that it is polling an address which will never resolve. Every sibling
    # read (`/tree`, `/ray`, `/events:subscribe`, `/file`) already 404s here.
    if not cycle_path.is_dir():
        raise NotFoundError(f"Cycle '{campaign_id}/{cycle_id}' not found")
    path = CycleLayout(cycle_path).dashboard
    present = path.is_file()

    # Conditional-GET once, before reading the body — the read only happens after
    # the 304 check passes, which keeps the 2 s poll cheap. The validator covers
    # every input to the served phase, not just this file's mtime: the phase turns
    # `detached` on the CLOCK, with nothing written, so an mtime-only validator
    # would 304 a dead producer at "running" for as long as the browser polled.
    try:
        mtime_epoch = run_phase_validator_epoch(cycle_path)
        if mtime_epoch is None:
            raise FileNotFoundError(cycle_path)
        headers = {"Last-Modified": http_date(mtime_epoch)}
        if client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    except FileNotFoundError:
        headers = {}

    # ``run_phase`` is DERIVED here, never served as stored. The file carries the
    # runner's last declaration, written only by that process, so a kill / restart /
    # orphan reap left it claiming "running" forever while `/cycles` and `/tree` —
    # which have always re-derived — said terminal. One authority, every surface.
    #
    # The ARMED run-control values are re-read for the SAME reason, and it is one reason rather
    # than a per-field judgement — `runtime_flags.py::overlay_armed_controls` states it and owns
    # the set.
    body = read_json_tolerant(path) if present else None
    run_phase = str(derive_run_phase(cycle_path))
    if at is not None:
        # A replay. Two overlays, for the same reason and neither optional: `run_phase` answers
        # what the producer is doing NOW, a clock fact rather than a property of the moment; the
        # wiring constants ride no record, so the fold returns them at their defaults and only
        # this file has the live ones. The armed controls deliberately get NEITHER — they are what
        # is in force now, and restating one as a past moment's value would be a fabrication.
        replay = fold_at(Cut(cycle=CycleDir(cycle_path), hop=hop, offset=at)).model_dump(
            mode="json"
        )
        replay["run_phase"] = run_phase
        overlay_criterion_dials(replay)
        if isinstance(body, dict):
            for field in LiveDashboardState.WIRING_FIELDS:
                if field in body:
                    replay[field] = body[field]
        if meter is not None:
            overlay_spend_metered(replay, meter)
        return JSONResponse(replay, headers=headers)
    if body is None:
        # Missing OR corrupt (half-written / truncated): degrade to the warming
        # placeholder rather than 500 on the 2 s poll. A present-but-unreadable
        # file carries a reason so the panel can say so, matching the SSE tail.
        body = warming_payload(hop, run_phase=run_phase)
        if present:
            body["reason"] = "dashboard_unreadable"
    else:
        body["run_phase"] = run_phase
        limits = body.get("run_limits")
        if next_launch is not None and run_phase != RunPhase.RUNNING and isinstance(limits, dict):
            limits.update(next_launch())
        overlay_armed_controls(body, cycle_path)
        overlay_criterion_dials(body)
        overlay_verify(body, cycle_path)
        if meter is not None:
            overlay_spend_metered(body, meter)
    return JSONResponse(body, headers=headers)


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
    campaign = stores.campaigns.load_campaign(leaf.campaign_id)
    return serve_dashboard_response(
        request,
        stores.base_dir,
        leaf.campaign_id,
        leaf.cycle_id,
        meter=None if campaign is None else ceiling_meter(campaign.arm),
        next_launch=None
        if campaign is None or not campaign.config
        else lambda: next_launch_limits(
            resolve_campaign_config(stores, campaign, leaf), stores=stores, hop=leaf
        ),
        at=at,
    )


class _Lens(NamedTuple):
    verdict: Verdict
    # The `per_cell` formula (or `dials:` weights) every record is read under; `None` reads each
    # cycle's own scorer.
    formula: str | None


def _resolve_lens(lens: str | None) -> _Lens:
    """The API-edge selector: one ``lens`` value → its verdict strategy; a bad value is a clean 400."""
    if lens and lens.startswith("abort:"):
        variant = lens.removeprefix("abort:")
        lenses = _abort_lenses()
        suppress = lenses.get(variant)
        if suppress is None:
            raise BadRequestError(
                f"Unknown abort lens: {variant!r} (expected one of {sorted(lenses)})"
            )
        return _Lens(make_abort_verdict(suppress), None)
    if lens and not lens.startswith((LENS_SCORE_PREFIX, LENS_DIALS_PREFIX)):
        raise BadRequestError(
            f"Unknown lens: {lens!r} (expected '{LENS_SCORE_PREFIX}<formula>', "
            f"'{LENS_DIALS_PREFIX}<term=weight,…>' or 'abort:<variant>')"
        )
    # A `dials:` lens keeps its prefix: the record read realizes it, against its own campaign.
    return _Lens(make_scoring_verdict(), lens.removeprefix(LENS_SCORE_PREFIX) if lens else None)


def _mask_records(
    stores: Stores, tree: LineageNode, samples: frozenset[int] | None, formula: str | None
) -> dict[CyclePath, MaskRecord]:
    """One ``MaskRecord`` per campaign the tree spans, keyed by the course's OWN PATH.

    Keyed on ``campaign_id`` this served the wrong sandbox's numbers: an inner campaign id is
    content-addressed on the CELL, not on who asked, so one id is minted into several sibling
    ``.inner/`` sandboxes and the first visited won. ``cycle_id`` is no safer; the path is the
    only address that separates them, which is what ``lineage_queries`` already applies."""
    out: dict[CyclePath, MaskRecord] = {}

    def visit(node: LineageNode) -> None:
        if node.kind == "course" and node.path:
            path = tuple(node.path)
            if path not in out:
                leaf_store, leaf = resolve_cycle_path(stores, path)
                try:
                    out[path] = load_mask_record(
                        leaf_store, leaf.campaign_id, samples, lens=formula
                    )
                except (ValueError, SyntaxError, ScoringFormulaError) as exc:
                    raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc
        for kid in node.children:
            visit(kid)

    visit(tree)
    return out


class _Overlay:
    """The lens folded over the tree's records, keyed by the COURSE PATH the tree already serves —
    exactly as :func:`_mask_records` keys the records it folds. ``(campaign_id, cycle_id)`` repeats
    across sibling ``.inner/`` sandboxes, so re-keying on the pair collapses two of them onto one
    node; a record is loaded AT one course, so every cycle it names shares that course's prefix."""

    def __init__(
        self,
        records: dict[CyclePath, MaskRecord],
        verdict: Verdict,
        *,
        serves_value: bool,
        serves_subset: bool,
    ):
        self.diverged: dict[tuple[CyclePath, int], LineageDivergence] = {}
        self.readings: dict[tuple[CyclePath, str], MaskReading | None] = {}
        self.serves_value = serves_value
        self.serves_subset = serves_subset
        self.criteria = {path: record.criterion for path, record in records.items()}
        dimmed: set[tuple[CyclePath, int]] = set()
        for path, record in records.items():
            sandbox, campaign_id = path[:-1], path[-1].campaign_id
            result = find_divergences(record, verdict)
            for d in result.divergences:
                hop = CycleHop(campaign_id=campaign_id, cycle_id=d.cycle_id)
                self.diverged[((*sandbox, hop), d.round)] = LineageDivergence(
                    alternative_candidate_id=d.alternative_candidate_id
                )
            dimmed.update(
                ((*sandbox, CycleHop(campaign_id=campaign_id, cycle_id=cid)), rnd)
                for cid, rnd in result.divergent
            )
            for cyc in record.cycles:
                course = (*sandbox, CycleHop(campaign_id=campaign_id, cycle_id=cyc.cycle_id))
                for rnd_rec in cyc.rounds:
                    for cand in rnd_rec.candidates:
                        self.readings[(course, cand.candidate_id)] = cand.reading
        self.dimmed: frozenset[tuple[CyclePath, int]] = frozenset(dimmed)

    @staticmethod
    def _rank_by_lens(kids: list[LineageNode]) -> list[LineageNode]:
        """`lens_rank`'s half of the sibling ordering — the twin of `rank_by_composite`, which
        stamps the un-lensed rank during the tree build. It cannot ride along there: the lens
        is a property of the REQUEST, so its values only exist once the overlay has folded."""
        scored = {k.id: v for k in kids if (v := k.lens_value) is not None}
        if not scored:
            return kids
        # Ties break on id so N bars read 1..N — matching `rank_by_composite` exactly, or the
        # two ranks would disagree about a tie and read as a rank-shift that never happened.
        position = {
            cid: i + 1
            for i, (cid, _) in enumerate(sorted(scored.items(), key=lambda kv: (-kv[1], kv[0])))
        }
        return [k.model_copy(update={"lens_rank": position.get(k.id)}) for k in kids]

    def apply(self, node: LineageNode) -> LineageNode:
        kids = self._rank_by_lens([self.apply(k) for k in node.children])
        if node.kind == "course" and node.path:
            criterion = self.criteria.get(tuple(node.path)) if self.serves_value else None
            return node.model_copy(update={"children": kids, "lens_criterion": criterion})
        if node.kind != "candidate" or node.round is None or not node.path:
            return node.model_copy(update={"children": kids})
        course = tuple(node.path)
        key = (course, node.round)
        read = (course, node.id) in self.readings
        reading = self.readings.get((course, node.id))
        return node.model_copy(
            update={
                "children": kids,
                # The marker sits on the SPINE node — the winner is who the lens would have
                # replaced, so it is the node the fork would have happened at.
                "divergence": self.diverged.get(key) if node.is_selected else None,
                "divergent": key in self.dimmed,
                "lens_value": (
                    reading.composite_fitness if reading and self.serves_value else None
                ),
                "sample_set_accuracy": (
                    reading.accuracy if reading and self.serves_subset else None
                ),
                "sample_set_n": (
                    (reading.n_scored if reading else 0) if read and self.serves_subset else None
                ),
            }
        )


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
    stores, leaf = resolve_cycle_path(stores, path)
    if not cycle_dir_for(stores.base_dir, leaf).is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")

    # ONE conditional path for every query: the validator folds the mask in with the
    # mtime, so a masked read gets its own 304 (see `_conditional.py`). A 304 costs two
    # `stat()`s.
    mtime_ns = course_validator_ns(cycle_dir_for(stores.base_dir, leaf))
    etag = weak_etag(mtime_ns, lens, samples)
    headers = {"ETag": etag}
    if mtime_ns is not None and client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    tree = build_lineage_tree(stores, path)
    try:
        sample_ids = parse_sample_ids(samples)
    except ValueError as exc:
        raise BadRequestError(f"Invalid samples list: {samples!r} ({exc})") from exc
    if lens or sample_ids:
        # One record read per campaign, every arm's rows graded once under the lens: an `abort:`
        # lens reads the firing log rather than a score, so it loads the full set. The same
        # readings feed the divergence fold, `lens_value` and the subset — no double-score.
        verdict, formula = _resolve_lens(lens)
        is_abort = bool(lens and lens.startswith("abort:"))
        masked = sample_ids if not is_abort else None
        tree = _Overlay(
            _mask_records(stores, tree, masked, formula),
            verdict,
            serves_value=formula is not None,
            serves_subset=bool(masked),
        ).apply(tree)
    return model_json(tree, headers=headers)
