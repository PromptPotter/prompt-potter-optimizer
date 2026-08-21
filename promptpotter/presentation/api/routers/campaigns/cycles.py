"""Per-cycle reads at ANY depth — one route serves a top-level cycle, an L4 inner one, or an L5+ descendant. The dashboard
is per-cycle, so a fork's chart shows the fork's trajectory; the tree is rooted at a COURSE for the same reason."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from promptpotter.application.mask.divergence import Verdict, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRecord
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.application.scoring.metrics import value_with_mask_applied
from promptpotter.domain.cycle_paths import CycleHop, WorkspaceDir
from promptpotter.domain.results import EliminationGate
from promptpotter.domain.scoring import RoundScorer
from promptpotter.infrastructure.projections.live_dashboard.state import warming_payload
from promptpotter.infrastructure.runtime_flags import (
    derive_run_phase,
    read_spend_caps,
    run_phase_validator_epoch,
)
from promptpotter.infrastructure.store.io import newest_mtime_ns, read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.lineage_views import (
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
    weak_etag,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError

# Abort-lens variants → the PoBB gate(s) to switch off (the thin API-edge selector for the abort
# verdict; see docs/operations/mask-projection.md). DERIVED from `EliminationGate`, so a gate added
# there is switchable here rather than silently unsuppressable.
_ABORT_SUPPRESS: dict[str, frozenset[str]] = {
    **{f"{g.value}_off": frozenset({g.value}) for g in EliminationGate},
    "all_off": frozenset(g.value for g in EliminationGate),
}


def serve_dashboard_response(
    request: Request, base_dir: WorkspaceDir, campaign_id: str, cycle_id: str
) -> Response:
    """The single dashboard-serving path — the outer route passes the caller's ``base_dir``, the inner a sandbox's. One
    implementation, two roots, so 304 / warming / atomic-read semantics cannot drift between them."""
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
    # ``run_limits``' two spend arms are re-read for the SAME reason. The writer that
    # projects them into the file (`live_dashboard/view.py::_persist`) lives in the
    # runner's process, so on a HALTED cycle — precisely the one an operator raises a
    # cap on to continue — nothing re-persists and the raise would never reach the
    # browser. The `.runtime` dir mtime is already in the 304 validator, and
    # `spend_cap.json` is its child, so a change expires the cached answer on its own.
    body = read_json_tolerant(path) if present else None
    declared = str(body.get("declared_phase", "")) if isinstance(body, dict) else None
    run_phase = str(derive_run_phase(cycle_path, declared=declared))
    if body is None:
        # Missing OR corrupt (half-written / truncated): degrade to the warming
        # placeholder rather than 500 on the 2 s poll. A present-but-unreadable
        # file carries a reason so the panel can say so, matching the SSE tail.
        body = warming_payload(hop, run_phase=run_phase)
        if present:
            body["reason"] = "dashboard_unreadable"
    else:
        body["run_phase"] = run_phase
        _overlay_armed_ceilings(body, cycle_path)
    return JSONResponse(body, headers=headers)


def _overlay_armed_ceilings(body: dict[str, Any], cycle_path: Path) -> None:
    """Serve ``run_limits``' spend arms as what ``BudgetGate`` will actually enforce. Mutates in
    place; a body carrying no ``run_limits`` (warming) is left alone."""
    limits = body.get("run_limits")
    if not isinstance(limits, dict):
        return
    armed_usd, armed_tokens = read_spend_caps(cycle_path)
    if armed_usd is not None:
        limits["spend_budget_usd"] = armed_usd
    if armed_tokens is not None:
        limits["token_budget"] = armed_tokens


@campaigns_router.get("/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard")
def get_cycle_dashboard(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
) -> Response:
    """Live telemetry for the viewed cycle — its own ``dashboard.json``.

    ``dashboard.json`` is per-cycle: every cycle (root, fork, sweep, diag, or an
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
    """
    stores, leaf = resolve_cycle_path(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    return serve_dashboard_response(request, stores.base_dir, leaf.campaign_id, leaf.cycle_id)


def _subtree_mtime(base_dir: WorkspaceDir, campaign_id: str, cycle_id: str) -> int | None:
    """Newest mtime across this course's lineage inputs. The LEDGER is the validator that matters — it bumps when a candidate is
    minted, which ``dashboard.json`` never did, since that only moves at round close."""
    layout = CycleLayout(
        cycle_dir_for(base_dir, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id))
    )
    return newest_mtime_ns(layout.ledger, layout.manifest)


class _Lens(NamedTuple):
    verdict: Verdict
    criterion: RoundScorer | None


def _resolve_lens(lens: str | None) -> _Lens:
    """The API-edge selector: one ``lens`` value → its verdict strategy; a bad value is a clean 400. Parsed ONCE, so the
    criterion the fold asks and the criterion served per node are the same object."""
    if lens and lens.startswith("abort:"):
        variant = lens.removeprefix("abort:")
        suppress = _ABORT_SUPPRESS.get(variant)
        if suppress is None:
            raise BadRequestError(
                f"Unknown abort lens: {variant!r} (expected one of {sorted(_ABORT_SUPPRESS)})"
            )
        return _Lens(make_abort_verdict(suppress), None)
    if lens and not lens.startswith("score:"):
        raise BadRequestError(
            f"Unknown lens: {lens!r} (expected 'score:<formula>' or 'abort:<variant>')"
        )
    try:
        criterion = compile_round_scorer(lens.removeprefix("score:") if lens else None)
    except (ValueError, SyntaxError) as exc:
        raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc
    # No lens at all ⇒ a samples-only mask: the accuracy default folds, but nothing is served
    # as `lens_value` — the operator named no alternative to show it against.
    return _Lens(make_scoring_verdict(criterion), criterion if lens else None)


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


def _mask_records(
    stores: Stores, tree: LineageNode, samples: frozenset[int] | None
) -> dict[tuple[CycleHop, ...], MaskRecord]:
    """One ``MaskRecord`` per campaign the tree spans, keyed by the course's OWN PATH.

    Keyed on ``campaign_id`` this served the wrong sandbox's numbers: an inner campaign id is
    content-addressed on the CELL, not on who asked, so one id is minted into several sibling
    ``.inner/`` sandboxes and the first visited won. ``cycle_id`` is no safer; the path is the
    only address that separates them, which is what ``lineage_views`` already applies."""
    out: dict[tuple[CycleHop, ...], MaskRecord] = {}

    def visit(node: LineageNode) -> None:
        if node.kind == "course" and node.path:
            path = tuple(node.path)
            if path not in out:
                leaf_store, leaf = resolve_cycle_path(stores, path)
                out[path] = load_mask_record(leaf_store, leaf.campaign_id, samples)
        for kid in node.children:
            visit(kid)

    visit(tree)
    return out


class _Overlay:
    """The lens folded over the tree's records. Every index is keyed by ``(campaign_id, cycle_id, …)``, NEVER the cycle alone: a
    cycle_id is content-addressed, so it repeats across campaigns and inside one ``.inner/`` sandbox."""

    def __init__(
        self,
        records: dict[tuple[CycleHop, ...], MaskRecord],
        lens: str | None,
        sample_ids: frozenset[int] | None,
    ):
        verdict, self.criterion = _resolve_lens(lens)
        self.diverged: dict[tuple[str, str, int], LineageDivergence] = {}
        self.subset: dict[tuple[str, str, str], tuple[float | None, int]] = {}
        dimmed: set[tuple[str, str, int]] = set()
        for path, record in records.items():
            campaign_id = path[-1].campaign_id
            result = find_divergences(record, verdict)
            for d in result.divergences:
                self.diverged[(campaign_id, d.cycle_id, d.round)] = LineageDivergence(
                    alternative_candidate_id=d.alternative_candidate_id
                )
            dimmed.update((campaign_id, cid, rnd) for cid, rnd in result.divergent)
            if not sample_ids:
                continue
            for cyc in record.cycles:
                for rnd_rec in cyc.rounds:
                    for cand in rnd_rec.candidates:
                        self.subset[(campaign_id, cyc.cycle_id, cand.candidate_id)] = (
                            cand.accuracy if cand.n_scored > 0 else None,
                            cand.n_scored,
                        )
        self.dimmed: frozenset[tuple[str, str, int]] = frozenset(dimmed)

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
        if node.kind != "candidate" or node.round is None or not node.path:
            return node.model_copy(update={"children": kids})
        hop = node.path[-1]
        key = (hop.campaign_id, hop.cycle_id, node.round)
        subset = self.subset.get((hop.campaign_id, hop.cycle_id, node.id))
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
    and the recursion bound is ``lineage_views._MAX_COURSE_DEPTH``.

    An optional **lens** decorates the nodes with a counterfactual. ``lens=score:<formula>``
    = an alternative scoring formula (each candidate's ``lens_value``, plus a ``divergence``
    marker where that criterion would have elected someone else); ``lens=abort:<variant>``,
    variant ∈ {``epsilon_off``, ``lock_in_off``, ``all_off``} = switch off a PoBB abort
    contributor. ``samples`` = a comma-separated sample-id list (the **sample-set mask**):
    re-score over only those samples. No lens + no samples ⇒ the tree is the raw read.

    A shell, deliberately: resolve the path, build the view, serve it. The assembly rules
    live in ``store/lineage_views.py``.
    """
    path = (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    stores, leaf = resolve_cycle_path(stores, path)
    if not cycle_dir_for(stores.base_dir, leaf).is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")

    # ONE conditional path for every query: the validator folds the mask in with the
    # mtime, so a masked read gets its own 304 (see `_conditional.py`). A 304 costs two
    # `stat()`s.
    mtime_ns = _subtree_mtime(stores.base_dir, leaf.campaign_id, leaf.cycle_id)
    etag = weak_etag(mtime_ns, lens, samples)
    headers = {"ETag": etag}
    if mtime_ns is not None and client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    tree = build_lineage_tree(stores, path)
    sample_ids = _parse_samples(samples)
    if lens or sample_ids:
        # One record read per campaign: an `abort:` lens reads the firing log rather than
        # evaluators, so it loads the full set. The same records feed the divergence fold AND
        # the subset re-score — no double-score.
        is_abort = bool(lens and lens.startswith("abort:"))
        masked = sample_ids if not is_abort else None
        tree = _Overlay(_mask_records(stores, tree, masked), lens, masked).apply(tree)
    return JSONResponse(tree.model_dump(mode="json"), headers=headers)
