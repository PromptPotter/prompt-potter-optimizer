"""Campaign lineage — every cycle in a campaign + each cycle's rounds with
candidates + the parent-round where each fork was cut.

One round-trip from the webapp; per cycle, ``index.json`` gives the fork/topology
facts and ``dashboard.json`` gives the live round state (completed rounds + the
in-flight one). Backs the cross-cycle search-point cladogram in the dashboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from promptpotter.application.mask import (
    Verdict,
    find_divergences,
    make_abort_verdict,
    make_scoring_verdict,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import MaskRecord
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.application.scoring.metrics import value_with_mask_applied
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.rendering import display_fitness
from promptpotter.domain.run_records import (
    UNATTRIBUTED_OPERATOR,
    ForkTrigger,
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
)
from promptpotter.domain.scoring import RoundScorer
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.paths import sibling_kind
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_seen_at_or_after,
    http_date,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError

# An operator-steered fork is a clean offshoot: numbering restarts at 1, so all
# its rounds are post-divergence by definition and the lane sits one column past
# the parent's fork point.
_OPERATOR_RESTART_TRIGGER = ForkTrigger.OPERATOR_STEERED.value


class CampaignLineageCandidate(BaseModel):
    candidate_id: str = Field(description="Stable id assigned at L1-score time")
    label: str = Field(default="", description="Short L1-generated description")
    accuracy: float | None = Field(default=None, description="Per-candidate accuracy")
    composite_fitness: float | None = Field(
        default=None,
        description="The candidate's fitness under the run's active formula (equals accuracy "
        "when no formula was active) — served verbatim from the dashboard round summary so the "
        "lineage tree can honor the composite headline selection on settled/sibling cycles too, "
        "never recomputed client-side.",
    )
    rank: int | None = Field(default=None, description="Final rank within the round")
    is_winner: bool = Field(default=False, description="True for the round's elected winner")
    theta: float | None = Field(
        default=None,
        description="Difficulty-adjusted Rasch ability the winner was elected on (`elect_round_winner`) "
        "— the subset-invariant metric that explains a lower-accuracy winner. Null for candidates "
        "outside the round's election fit.",
    )
    theta_se: float | None = Field(
        default=None, description="Laplace SE on `theta` (for a CI on the ability)."
    )
    lens_value: float | None = Field(
        default=None,
        description="This candidate's fitness under the request's `score:` lens formula, "
        "recomputed from its stored evaluator namespace via the single scoring operation "
        "(the same one the mask divergence rides). Null without a `score:` lens, or when the "
        "candidate's namespace can't satisfy the formula.",
    )
    sample_set_accuracy: float | None = Field(
        default=None,
        description="Scorer-faithful accuracy over the request's `samples=` subset, re-scored "
        "server-side from this candidate's per-sample rows (the same `materialize_row_derivable` "
        "the mask divergence rides). Null without a `samples=` mask, or when the candidate never "
        "ran any selected sample.",
    )
    sample_set_n: int | None = Field(
        default=None,
        description="How many of the `samples=` subset this candidate actually ran (the honest "
        "'n of N' — older candidates that skipped some chosen samples read a smaller n). Null "
        "without a `samples=` mask.",
    )


class CampaignLineageRound(BaseModel):
    round: int = Field(description="Round number within the cycle (1-indexed)")
    label: str = Field(default="", description="Round label — winner's L1 description")
    accuracy: float | None = Field(default=None, description="Round-level accuracy (winner)")
    cumulative_accuracy: float | None = Field(
        default=None,
        description="The adopted lineage rescored over EVERY sample probed so far — the "
        "cross-round-comparable frontier (matches the trend chart). The webapp paints the "
        "WINNER node with this so the lineage spine reads as honest progress, not the per-round "
        "subset swing; sibling alternatives keep their own subset `accuracy`.",
    )
    candidates: list[CampaignLineageCandidate] = Field(
        description="All candidates scored this round, sorted by rank"
    )


class CampaignLineageCycle(BaseModel):
    cycle_id: str
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    # Immediate parent, read from index.json so sub-forks (forks of forks)
    # attach to their actual parent in the visual tree.
    immediate_parent_cycle_id: str | None
    # Round of the immediate parent at which this cycle's first round was
    # cut. None for roots; may be None for forks whose index didn't record it.
    fork_from_round: int | None
    # Candidate id at the parent's fork_from_round that this fork descends
    # from. Only set when index.json::fork carries from_candidate_id (operator
    # endorse/steered forks); divergence/sweep forks attach at round-level only.
    fork_from_candidate_id: str | None
    # Fork creation trigger — drives the round-numbering convention.
    trigger: str
    # Operator who steered the fork (ForkSpec.issued_by), when attributed.
    # None for non-operator forks and for the unattributed "operator" default.
    # Surfaced as the lineage "edited by {name}" badge on operator_steered forks.
    steered_by: str | None = None
    # X-axis offset for this cycle's rounds in the campaign cladogram —
    # add to each round's ``round`` number to get its absolute column.
    round_column_offset: int
    status: str
    dataset_name: str
    best_accuracy: float | None
    # Banked lives ("hearts") + the bank's ceiling, so a lineage row shows how much runway
    # each cycle has left. Both ``None`` when the cycle isn't in lives mode. The cap travels
    # WITH the count — a reader handed only the count cannot tell 3-of-4 from 3-of-7.
    hearts: int | None = None
    lives_cap: int | None = None
    # Origin is round 0 — it rides ``rounds[]`` like any round (no separate
    # trunk anchor). The cladogram renders it through the same round path.
    rounds: list[CampaignLineageRound]


class LineageDivergence(BaseModel):
    """A mask divergence point — the first node on a branch an alternative scoring
    criterion would have forked. Rendered as a marker on that node (not dimmed);
    nodes in ``divergent`` are its counterfactual descendant subtree (dimmed).
    Empty unless the request carried a ``mask`` criterion."""

    node_key: str = Field(description="Lineage node id, formatted `{cycle_id}::r{round}`")
    cycle_id: str
    round: int
    alternative_candidate_id: str | None = Field(
        default=None,
        description="The candidate the masked criterion would have elected instead "
        "(measured, so nameable); null when the round would simply have held on origin.",
    )


class CampaignLineageResponse(BaseModel):
    campaign_id: str
    cycles: list[CampaignLineageCycle] = Field(
        description="Every cycle in the campaign (root + forks + sweeps + diag). "
        "Sorted by cycle id; lay out via immediate_parent_cycle_id."
    )
    # Mask overlay — computed only when the request carries a ``mask`` scoring
    # criterion; both empty otherwise (the unmasked lineage is byte-identical to
    # before). Match a divergence / dimmed node to a tree node by `{cycle_id}::r{round}`.
    divergences: list[LineageDivergence] = Field(default_factory=list)
    divergent: list[str] = Field(
        default_factory=list,
        description="Node keys of the counterfactual subtree to render dimmed.",
    )


def _lens_value(evaluators: Any, criterion: RoundScorer | None) -> float | None:
    """A candidate's fitness under the request's ``score:`` lens — its stored evaluator
    namespace under the lens formula, through ``value_with_mask_applied`` (the single
    scoring operation the mask divergence also rides, so the served bar value and the
    served divergence agree by construction — R-36). ``None`` when there is no ``score:``
    lens (``criterion is None``) or the candidate carries no namespace."""
    if criterion is None or not isinstance(evaluators, dict):
        return None
    return value_with_mask_applied(evaluators, criterion)


def _to_lineage_candidate(
    *,
    pos: int,
    candidate_id: str,
    label: str,
    accuracy: Any,
    composite_fitness: Any,
    is_winner: bool,
    evaluators: Any,
    criterion: RoundScorer | None,
    theta: Any = None,
    theta_se: Any = None,
) -> CampaignLineageCandidate:
    """Build one lineage row — the shared construction behind the completed-round
    and in-flight mappers. ``rank`` rides ``pos`` (the webapp re-derives the
    ``C{r}.{n}`` label from position); numeric fields are float-coerced or ``None``."""
    _acc = float(accuracy) if isinstance(accuracy, int | float) else None
    _comp = float(composite_fitness) if isinstance(composite_fitness, int | float) else None
    return CampaignLineageCandidate(
        candidate_id=candidate_id,
        label=label,
        accuracy=_acc,
        # Resolve the composite-or-accuracy rule server-side (the one `display_fitness`),
        # so the wire carries a settled number and no client re-runs `?? accuracy`.
        # None-tolerant on accuracy (a malformed/in-flight row may lack it).
        composite_fitness=display_fitness(_comp, _acc) if _acc is not None else _comp,
        rank=pos,
        is_winner=is_winner,
        lens_value=_lens_value(evaluators, criterion),
        theta=float(theta) if isinstance(theta, int | float) else None,
        theta_se=float(theta_se) if isinstance(theta_se, int | float) else None,
    )


def _summary_candidates(
    cands: list[Any], criterion: RoundScorer | None
) -> list[CampaignLineageCandidate]:
    """Map ``dashboard.json::rounds[].candidates`` (``RoundSummaryCandidate``
    shape) to lineage candidates. List order is the round's display order."""
    return [
        _to_lineage_candidate(
            pos=pos,
            candidate_id=str(c.get("candidate_id") or ""),
            label=str(c.get("label") or ""),
            accuracy=c.get("accuracy"),
            composite_fitness=c.get("composite_fitness"),
            is_winner=bool(c.get("is_winner", False)),
            evaluators=c.get("evaluators"),
            criterion=criterion,
            theta=c.get("theta"),
            theta_se=c.get("theta_se"),
        )
        for pos, c in enumerate(cands, start=1)
        if isinstance(c, dict)
    ]


def _read_dashboard(cycle_dir: Path) -> dict[str, Any] | None:
    """The cycle's live projection, parsed once per cycle and split by the readers below."""
    dash = read_json_optional(cycle_dir / "dashboard.json")
    return dash if isinstance(dash, dict) else None


def _lives_from_dashboard(dash: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """``(hearts, lives_cap)`` — the cycle's ♥ bank and its ceiling, or ``(None, None)``.

    Served so the lineage tree can render each cycle's remaining life beside its rounds.
    The cap is the denominator: a bare ``hearts`` count is scaleless, and in lives mode
    ``max_rounds`` is null, so nothing else on the row carries the scale."""
    if dash is None:
        return None, None
    hearts = dash.get("hearts")
    limits = dash.get("run_limits")
    cap = limits.get("lives_cap") if isinstance(limits, dict) else None
    return (
        hearts if isinstance(hearts, int) else None,
        cap if isinstance(cap, int) else None,
    )


def _rounds_from_dashboard(
    dash: dict[str, Any] | None, criterion: RoundScorer | None
) -> list[CampaignLineageRound]:
    """The SETTLED rounds for a cycle from its live ``dashboard.json`` — the
    single round-state projection (``LiveDashboardView``). Takes the ALREADY-PARSED
    dashboard so the caller reads the file once and splits it into rounds + lives.

    Completed rounds ride ``rounds[]`` (the full per-candidate scoreboard). The
    in-flight ``current_round`` is deliberately NOT appended here: the active
    cycle's in-flight round has ONE owner — the webapp reads it live from the
    same ``dashboard.json`` the fitness bars use (``roundCandidates``) and
    overlays it onto the active lane. Duplicating it server-side was a second
    mechanism for the same data (the phantom-0% source). Round progress reads no
    ``index.json::rounds`` or audit scoreboard here. (Topology — parent / fork /
    sibling_kind — still rides ``index.json`` in the caller; write-once identity,
    not updating state.)
    """
    if dash is None:
        return []
    out: list[CampaignLineageRound] = []
    rounds_raw = dash.get("rounds")
    if isinstance(rounds_raw, list):
        for r in rounds_raw:
            if not isinstance(r, dict):
                continue
            rn = r.get("round")
            if not isinstance(rn, int):
                continue
            _rc = r.get("candidates")
            raw_cands: list[Any] = _rc if isinstance(_rc, list) else []
            winner = next(
                (c for c in raw_cands if isinstance(c, dict) and c.get("is_winner")), None
            )
            acc = r.get("accuracy")
            cum = r.get("cumulative_accuracy")
            out.append(
                CampaignLineageRound(
                    round=rn,
                    label=str(winner.get("changes_description") or "") if winner else "",
                    accuracy=float(acc) if isinstance(acc, int | float) else None,
                    cumulative_accuracy=float(cum) if isinstance(cum, int | float) else None,
                    candidates=_summary_candidates(raw_cands, criterion),
                )
            )
    out.sort(key=lambda r: r.round)
    return out


def _fork_from_round_from_ledger(parent_dir: Path, child_cycle_id: str) -> int | None:
    """Find the FORK_CUT record in *parent_dir* whose outcome is *child_cycle_id*.

    Final fallback when index.json::fork::from_round doesn't carry the
    value. Returns None if the parent's ledger is missing or the record
    isn't there.
    """
    if not (parent_dir / ".runtime" / "ledger.jsonl").is_file():
        return None
    try:
        ledger = CycleEventLog.open(CycleDir(parent_dir))
    except Exception:
        return None
    for rec in ledger.iter():
        if (
            isinstance(rec, ResumeCheckpointRecord)
            and rec.kind is ResumeCheckpointKind.FORK_CUT
            and str(rec.outcome) == child_cycle_id
        ):
            v = rec.inputs_ref.get("from_round")
            if isinstance(v, int):
                return v
    return None


def _filter_post_divergence_rounds(
    rounds: list[CampaignLineageRound], trigger: str, fork_from_round: int | None
) -> list[CampaignLineageRound]:
    """For divergence / sweep / diag forks, drop rounds inherited from the
    parent (round <= fork_from_round). Those rounds belong to the parent's
    lane and would visually overlap if rendered in the fork's lane.

    An operator-steered fork restarts numbering at 1 so all its rounds are
    post-divergence by definition — return as-is.
    """
    if trigger == _OPERATOR_RESTART_TRIGGER:
        return rounds
    if fork_from_round is None:
        return rounds
    return [r for r in rounds if r.round > fork_from_round]


# Abort-lens variants → the PoBB contributor(s) to switch off (the thin API-edge
# selector for the abort verdict; see docs/specs/mask-projection.md).
_ABORT_SUPPRESS: dict[str, frozenset[str]] = {
    "epsilon_off": frozenset({"epsilon"}),
    "lock_in_off": frozenset({"lock_in"}),
    "all_off": frozenset({"epsilon", "lock_in"}),
}


def _resolve_verdict(lens: str | None) -> Verdict:
    """The thin API-edge selector: one ``lens`` value → its verdict strategy.
    ``abort:<variant>`` → the abort verdict; ``score:<formula>`` (or empty ⇒ the
    accuracy default, used by a samples-only mask) → the scoring verdict. A bad value
    is a clean 400."""
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
    """Parse the ``samples`` lens param — a comma-separated sample-id list — into a
    frozenset. Empty / unset ⇒ None (full-set mask). A non-integer token is a 400."""
    if not samples or not samples.strip():
        return None
    try:
        ids = frozenset(int(tok) for tok in samples.split(",") if tok.strip())
    except ValueError as exc:
        raise BadRequestError(f"Invalid samples list: {samples!r} ({exc})") from exc
    return ids or None


def _mask_overlay(
    record: MaskRecord, lens: str | None
) -> tuple[list[LineageDivergence], list[str]]:
    """Fold the chosen lens over the pre-loaded record → served divergence overlay.

    Read-only: folds the selected verdict, returns the markers + the dimmed subtree. The
    caller loads the record (once, so a ``samples=`` mask's subset re-score is reused for the
    per-candidate ``sample_set_accuracy`` decoration too). An evaluator absent from an older
    round's stored namespace is resolved once, inside ``value_with_mask_applied`` — that
    candidate is skipped, the tree unaffected; no second backstop here.
    """
    result = find_divergences(record, _resolve_verdict(lens))
    divergences = [
        LineageDivergence(
            node_key=d.node_key,
            cycle_id=d.cycle_id,
            round=d.round,
            alternative_candidate_id=d.alternative_candidate_id,
        )
        for d in result.divergences
    ]
    return divergences, result.divergent


def _subset_accuracy_map(record: MaskRecord) -> dict[tuple[str, str], tuple[float | None, int]]:
    """Per-candidate subset accuracy + scored-sample count from a ``samples=``-masked record,
    keyed ``(cycle_id, candidate_id)``. The webapp's fixed-sample-set bars read these served
    values instead of recomputing hits/n from per-round files. Accuracy is ``None`` when the
    candidate ran zero selected samples (a blank slot, not a fabricated 0%)."""
    out: dict[tuple[str, str], tuple[float | None, int]] = {}
    for cyc in record.cycles:
        for rnd in cyc.rounds:
            for cand in rnd.candidates:
                acc = cand.accuracy if cand.n_scored > 0 else None
                out[(cyc.cycle_id, cand.candidate_id)] = (acc, cand.n_scored)
    return out


def _decorate_sample_set(
    cycle: CampaignLineageCycle, subset: dict[tuple[str, str], tuple[float | None, int]]
) -> CampaignLineageCycle:
    """Stamp each candidate's served ``sample_set_accuracy``/``sample_set_n`` from the
    subset-scored record (joined by ``candidate_id``). In-flight candidates carry no id and
    aren't in the record yet, so they pass through null — the slice view shows closed rounds."""

    def _stamp(cand: CampaignLineageCandidate) -> CampaignLineageCandidate:
        entry = subset.get((cycle.cycle_id, cand.candidate_id))
        if entry is None:
            return cand
        acc, n = entry
        return cand.model_copy(update={"sample_set_accuracy": acc, "sample_set_n": n})

    new_rounds = [
        rnd.model_copy(update={"candidates": [_stamp(c) for c in rnd.candidates]})
        for rnd in cycle.rounds
    ]
    return cycle.model_copy(update={"rounds": new_rounds})


def _lineage_mtime(cycle_dirs: list[Path]) -> float | None:
    """Newest mtime across the campaign's lineage inputs — each cycle's
    ``index.json`` (fork/topology facts) plus its ``dashboard.json`` (the live
    round state the tree now renders, bumped each round-state write). Drives the
    lineage poll's ``If-Modified-Since`` validator: dashboard.json bumping mid-
    round is exactly what makes the poll re-fetch an in-progress round. ``None``
    when nothing is on disk yet."""
    newest: float | None = None
    for cdir in cycle_dirs:
        for p in (cdir / "index.json", cdir / "dashboard.json"):
            try:
                m = p.stat().st_mtime
            except FileNotFoundError:
                continue
            if newest is None or m > newest:
                newest = m
    return newest


@campaigns_router.get(
    "/campaigns/{campaign_id}/lineage",
    response_model=CampaignLineageResponse,
)
def get_campaign_lineage(
    request: Request,
    store: StoreDep,
    campaign_id: str,
    lens: str | None = None,
    samples: str | None = None,
) -> Response:
    """Aggregated lineage for the whole campaign.

    One pass over every cycle in the campaign — ``index.json`` for the
    fork/topology facts (supplemented by a ledger scan for fork-cut rounds when
    the index doesn't carry them) and ``dashboard.json`` for the round state
    (completed + in-flight). The tree is built from each cycle's ``parent_cycle_id``.

    An optional **lens** selects a divergence overlay. ``lens=score:<formula>`` = an
    alternative scoring formula (where that criterion would have forked the record);
    ``lens=abort:<variant>``, variant ∈ {``epsilon_off``, ``lock_in_off``,
    ``all_off``} = switch off a PoBB abort contributor. ``samples`` = a
    comma-separated sample-id list (the **sample-set mask**): re-score accuracy over
    only those samples and mark where the subset-best diverges from the recorded
    winner — it composes with a ``score:`` lens (reweight the subset accuracy) and is
    ignored for an ``abort:`` lens. No lens + no samples ⇒ overlay empty, lineage
    byte-identical to the raw read.
    """
    if store.campaigns.load_campaign(campaign_id) is None:
        raise NotFoundError(f"Campaign not found: {campaign_id}")
    enum_entries = [
        e for e in store.campaigns.enumerate_cycles() if e["campaign_id"] == campaign_id
    ]

    # Conditional fast-path — only on the UNMASKED poll. A masked body depends on
    # `lens`/`samples`, which `If-Modified-Since` can't capture, so those always
    # recompute; the 2 s webapp poll never carries a lens.
    mtime_epoch = _lineage_mtime(
        [cycle_dir_for(store.base_dir, campaign_id, e["cycle_id"]) for e in enum_entries]
    )
    headers = {"Last-Modified": http_date(mtime_epoch)} if mtime_epoch is not None else {}
    if (
        lens is None
        and samples is None
        and mtime_epoch is not None
        and client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch)
    ):
        return Response(status_code=304, headers=headers)

    # A ``score:`` lens makes each candidate's fitness under that formula a served fact
    # (``lens_value``): compile the scorer once here and thread it into the round builders,
    # which apply the single scoring operation per candidate. None for no lens / an
    # ``abort:`` lens (no per-candidate value to project) — leaving the unmasked body
    # byte-identical. The mask overlay below compiles the same formula for the divergence
    # verdict; both ride one ``value_with_mask_applied``, so the bar and the tree agree.
    score_criterion: RoundScorer | None = None
    if lens and lens.startswith("score:"):
        try:
            score_criterion = compile_round_scorer(lens.removeprefix("score:"))
        except (ValueError, SyntaxError) as exc:
            raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc

    out_cycles: list[CampaignLineageCycle] = []
    for entry in sorted(enum_entries, key=lambda e: e["cycle_id"]):
        cid = entry["cycle_id"]
        cdir = cycle_dir_for(store.base_dir, campaign_id, cid)
        index = read_json_optional(cdir / "index.json")
        if not isinstance(index, dict):
            out_cycles.append(
                CampaignLineageCycle(
                    cycle_id=cid,
                    sibling_kind=sibling_kind(cid),
                    immediate_parent_cycle_id=entry["parent_cycle_id"],
                    fork_from_round=None,
                    fork_from_candidate_id=None,
                    trigger="",
                    round_column_offset=0,
                    status="missing",
                    dataset_name=entry["dataset_name"],
                    best_accuracy=None,
                    rounds=[],
                )
            )
            continue

        immediate_parent = index.get("parent_cycle_id") or None
        _fork = index.get("fork")
        fork_block: dict[str, Any] = _fork if isinstance(_fork, dict) else {}
        trigger = str(fork_block.get("trigger") or "")

        # Two sources for fork_from_round, tried in this order:
        #   1. index.json::fork::from_round
        #   2. parent ledger's FORK_CUT record (last-resort scan)
        from_round: int | None = None
        block_fr = fork_block.get("from_round")
        if isinstance(block_fr, int):
            from_round = block_fr
        elif immediate_parent:
            from_round = _fork_from_round_from_ledger(
                cycle_dir_for(store.base_dir, campaign_id, immediate_parent), cid
            )

        from_candidate = fork_block.get("from_candidate_id")
        from_candidate_str = (
            str(from_candidate) if isinstance(from_candidate, str) and from_candidate else None
        )

        # The operator who steered this fork (ForkSpec.issued_by). "operator"
        # is the unattributed default the dispatcher stamps when the client
        # sends no identity — surface only a real attribution, so the badge
        # reads "edited by {name}" or nothing.
        _issued_by = fork_block.get("issued_by")
        steered_by = (
            str(_issued_by)
            if isinstance(_issued_by, str) and _issued_by and _issued_by != UNATTRIBUTED_OPERATOR
            else None
        )

        # Round progress reads from the single live projection (dashboard.json),
        # NOT index.json::rounds — so a round in progress shows the instant its
        # first candidate is seeded, on the same cadence the chart updates. One read,
        # two facts: the settled rounds and the cycle's ♥ bank.
        dash = _read_dashboard(cdir)
        rounds_out = _filter_post_divergence_rounds(
            _rounds_from_dashboard(dash, score_criterion), trigger, from_round
        )
        hearts, lives_cap = _lives_from_dashboard(dash)
        col_offset = (
            from_round
            if trigger == _OPERATOR_RESTART_TRIGGER and isinstance(from_round, int)
            else 0
        )

        header_raw = index.get("header")
        header = header_raw if isinstance(header_raw, dict) else {}

        out_cycles.append(
            CampaignLineageCycle(
                cycle_id=cid,
                sibling_kind=str(index.get("sibling_kind") or sibling_kind(cid)),
                immediate_parent_cycle_id=immediate_parent,
                fork_from_round=from_round,
                fork_from_candidate_id=from_candidate_str,
                trigger=trigger,
                steered_by=steered_by,
                round_column_offset=col_offset,
                status=str(index.get("status") or ""),
                dataset_name=str(header.get("dataset_name") or entry["dataset_name"]),
                hearts=hearts,
                lives_cap=lives_cap,
                best_accuracy=(
                    float(index["best_accuracy"])
                    if isinstance(index.get("best_accuracy"), int | float)
                    else None
                ),
                rounds=rounds_out,
            )
        )

    sample_ids = _parse_samples(samples)
    divergences: list[LineageDivergence] = []
    divergent: list[str] = []
    if lens or sample_ids:
        # Load the realized record once. A ``samples=`` mask re-scores accuracy over the
        # subset at load time; an ``abort:`` lens reads the firing log, not evaluators, so
        # it loads the full set. The same record feeds both the divergence fold and the
        # per-candidate ``sample_set_accuracy`` decoration — one read, no double-score.
        is_abort = bool(lens and lens.startswith("abort:"))
        record = load_mask_record(store, campaign_id, sample_ids if not is_abort else None)
        divergences, divergent = _mask_overlay(record, lens)
        if sample_ids and not is_abort:
            subset = _subset_accuracy_map(record)
            out_cycles = [_decorate_sample_set(c, subset) for c in out_cycles]
    response = CampaignLineageResponse(
        campaign_id=campaign_id,
        cycles=out_cycles,
        divergences=divergences,
        divergent=divergent,
    )
    return JSONResponse(response.model_dump(mode="json"), headers=headers)
