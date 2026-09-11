from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from pydantic import Field

from promptpotter.application.evidence.comparison import (
    ArmReplicate,
    Comparability,
    EvidencePower,
    EvidenceVariance,
    MetricReading,
    OrderConfound,
    comparability,
    metric_reading,
    order_confound,
    power,
    replicates,
    stamp_comparable,
    variance,
)
from promptpotter.application.evidence.grid import (
    FactorGridReading,
    FactorReading,
    factors,
    grid_reading,
    levels_by_subject,
)
from promptpotter.application.evidence.metric_catalogue import (
    MEASURAND,
    available_channels,
    cell_channels,
    merge_cells,
    resolve_metric,
)
from promptpotter.application.evidence.subjects import (
    LENS_SCORE_PREFIX,
    ScenarioReading,
    SubjectMask,
    SubjectReading,
    SubjectSpec,
    WinnerChainPoint,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.scenario import scenario_spine
from promptpotter.application.scoring.formula import compile_round_scorer
from promptpotter.application.scoring.formula.compiler import ScoringFormulaError
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.inner_origin import instrument_of
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_elections
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, campaign_cycles_dir
from promptpotter.infrastructure.store.stores import descend_store
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.statistics import exact_paired_reading, paired_diff_posterior, sample_sd

if TYPE_CHECKING:
    from promptpotter.application.scoring.formula.compiler import CompiledExpression
    from promptpotter.infrastructure.store.stores import Stores

_ORIGIN_HASH = "origin"


class CellEffect(NamedTuple):
    """One environment cell's paired (candidate − origin) effect. INTERNAL to ``_finalize`` — the
    two numbers a surface renders, ``n_cells`` and ``n_measurements``, are folded from it here."""

    cell: str
    mean_d: float
    n: int


class EffectProvenance(StrictModel):
    """Where one occurrence of an edit was measured on disk."""

    campaign_id: str
    cycle_id: str
    round: int
    candidate_id: str


class RankedEdit(StrictModel):
    """One unique candidate state — a ``pipeline_overlay`` — aggregated across every
    occurrence in the selection. An L1 target-prompt edit on an ordinary campaign, an
    optimizer-prompt edit on the recursion; the arithmetic does not care which.

    It pools by EDIT IDENTITY, so it earns its keep where the same edit recurs across campaigns.
    That is routine on the recursion and rarer elsewhere, where most rows will carry one campaign's
    cells and an interval to match.
    """

    state_hash: str
    label: str
    # Neither the edit's own text nor its per-cell breakdown rides here: a {node: {field: prose}}
    # map per ranked edit is the largest thing this read can put on the wire, and no surface opens
    # it. `state_hash` names the edit and `provenance` says where to read it.
    provenance: list[EffectProvenance]
    anchor_effect: float  # mean of the PER-CELL paired diffs — one point per cell, not per
    # occurrence, so an over-measured cell cannot outweigh uniform goodness (see _finalize)
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int
    n_measurements: int


class EditSpread(StrictModel):
    """How far apart the ranked edits actually are — the SD of ``anchor_effect`` across them.

    **Not a signal-to-noise ratio, and deliberately not one.** The noise half would be repeated
    readings of ONE (edit, cell), and the instrument cannot produce them: measurements are
    content-addressed, so a second ask replays the first answer and its spread is zero by
    construction, which reads as a perfect instrument. A replicate ARM is the honest noise reading
    (see :class:`ArmReplicate`); measuring one candidate HARDER is ``verify``'s job. ``None`` when
    fewer than two edits have been measured.
    """

    edit_effect_sd: float | None = None
    n_edits: int = 0


class Evidence(StrictModel):
    """The whole read for one selection of subjects — recomputed on every fetch."""

    generated_at: str
    # Oldest first — the confound reads off this order. Identity AND the metric reading in one
    # row, so nothing downstream joins two lists on `key`.
    subjects: list[SubjectReading]
    comparability: Comparability
    # WHICH number everything below is about — the picker's vocabulary, the merged per-subject
    # intervals and every pairwise test, all under one selection. Non-optional: the default always
    # resolves, so there is no state where a chart is drawn under no named metric.
    metric: MetricReading
    # Subject keys that were ASKED for and answered nothing — nothing of that name, no round
    # document at the head they address, or a head whose rows carry no channel. Served because the
    # roster is otherwise the only evidence they were dropped, and a subject that silently thins a
    # selection is the channel-level twin of scoring an unread cell as zero.
    unread_subjects: list[str] = Field(default_factory=list)
    # What this selection VARIES on, discovered rather than declared, with the marginal at each
    # level. Empty where every subject ran the same way — which is a reading, not a gap: a roster
    # with no factor is a replicate set, and `replicates` below is the surface for that.
    factors: list[FactorReading] = Field(default_factory=list)
    # The one 2-D face asked for, cells pooled. `None` unless a projection was named — the factors
    # above already carry every marginal, and which pair to cross is the reader's question.
    grid: FactorGridReading | None = None
    replicates: list[ArmReplicate] = Field(default_factory=list)
    variance: EvidenceVariance | None = None
    power: EvidencePower | None = None
    order_confound: OrderConfound | None = None
    ranking_computed: bool = False
    # Ranked desc by anchor_effect.
    edits: list[RankedEdit] = Field(default_factory=list)
    spread: EditSpread = Field(default_factory=EditSpread)


def _state_hash(prompt_state: dict[str, dict[str, str]]) -> str:
    """Stable short hash of a candidate state; empty state ⇒ the origin sentinel."""
    if not prompt_state:
        return _ORIGIN_HASH
    canonical = json.dumps(prompt_state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


class _Accum:
    def __init__(self, prompt_state: dict[str, dict[str, str]], label: str) -> None:
        self.prompt_state = prompt_state
        self.label = label
        self.provenance: list[EffectProvenance] = []
        # cell -> paired (candidate_fit, origin_fit) lists across occurrences
        self.cand_by_cell: dict[str, list[float]] = {}
        self.orig_by_cell: dict[str, list[float]] = {}


def _dataset_name(manifest: dict[str, Any]) -> str:
    block_raw = manifest.get("campaign_config")
    block = block_raw if isinstance(block_raw, dict) else manifest
    return str(block.get("dataset_name", ""))


def _dataset_of(campaign_dir: Path) -> str:
    return _dataset_name(read_json_tolerant(campaign_dir / "campaign.json", {}))


def campaigns_on_dataset(stores: Stores, dataset_name: str) -> list[str]:
    """Campaign ids bound to *dataset_name*, read off each manifest rather than off the directory
    name — a name-shaped guess silently skips an A/B arm, a fork, a rename."""
    return [
        child.name
        for child in stores.campaigns.iter_campaign_dirs()
        if _dataset_of(child) == dataset_name
    ]


class _ChainPoint(NamedTuple):
    """One MEASURED step of a branch: which candidate, at which round, the rows it left, and its
    own ``candidate_scores`` entry — the document is already open, so carrying the entry costs a
    reference and saves every consumer a second read of the same file."""

    round: int
    candidate_id: str
    label: str
    rows: list[dict[str, Any]]
    scores: dict[str, Any]


class _Head(NamedTuple):
    """Where one subject's numbers come from — always ONE searchpoint's rows, whichever kind
    addressed it. ``label`` is what to call the CHANNEL, which is not the same thing: a course is
    named by its branch, and the searchpoint it currently reads at is ``point``.

    ``chain`` is the branch behind the head where a LENS produced one — the counterfactual winners,
    which are not the crowned ones and cannot be re-derived from the ledger. ``None`` means the
    winner-chain walk reads the crowns instead."""

    cycle_dir: Path
    label: str
    dataset_name: str
    created_at: str
    point: _ChainPoint
    scenario: ScenarioReading | None = None
    chain: list[_ChainPoint] | None = None


def subject_evidence(
    stores: Stores,
    specs: list[SubjectSpec],
    *,
    include_ranking: bool = False,
    include_winner_chain: bool = False,
    include_config: bool = False,
    metric: str = MEASURAND,
    grid: tuple[str, str] | None = None,
) -> Evidence:
    """Reduce the named subjects into one evidence read, under one metric. *specs* may span
    datasets, and may name something that no longer exists — an absent one is simply not in the
    roster. *metric* is a catalogue key or ``expr:<formula>``; one this selection cannot answer
    raises ``ValueError`` for the route to turn into a 400.

    EVERY metric is a fact about one CELL, read off the row that stands for it — on the recursion
    a cell is a whole inner campaign, so its lift, its origin, its cost and its round count are
    that seed's own. Nothing here opens a second document to derive a number the cell already
    carries.

    ONE metric then reaches everything: the roster's merged estimates, the pairwise tests, the
    variance decomposition, the resolving power, the run-order confound and the edit ranking, so
    no two numbers on the page can be about different quantities.
    """
    wanted: dict[str, SubjectSpec] = {s.key: s for s in specs}
    heads: dict[str, _Head] = {}
    channels_by_subject: dict[str, dict[str, dict[str, float]]] = {}
    for key, spec in wanted.items():
        resolved = _at(stores, spec)
        if resolved is None:
            continue
        leaf_stores, campaign_dir = resolved
        head = _resolve_head(leaf_stores, spec, campaign_dir)
        if head is None:
            continue
        channels = cell_channels(head.point.rows)
        if not channels:
            continue
        heads[key] = head
        channels_by_subject[key] = channels

    # An unmeasured selection is not a metric problem, so it may not be answered as one: two
    # ordinary actions reach here — ticking a campaign whose origin has not run, mistyping an id
    # — and neither has a vocabulary to be wrong about.
    if not heads:
        raise ValueError(
            f"None of {', '.join(sorted(wanted)) or 'the subjects named'} has scored rows to read. "
            "A campaign answers here once its origin has run, a course once its branch has, a "
            "candidate once it has been measured; one that does not exist answers never."
        )
    # WHICH metric is decidable only once the rows are in hand: the measurand is the seed's own
    # lift where the cells carry one and the cell's own fitness where they do not, and a metric
    # nothing in this selection answers is never offered.
    available = available_channels(channels_by_subject)
    spec_metric, compiled = resolve_metric(metric, available)

    rows = [
        _reading_row(
            wanted[key],
            head,
            compiled,
            channels_by_subject[key],
            include_winner_chain=include_winner_chain,
            include_config=include_config,
        )
        for key, head in heads.items()
    ]
    rows.sort(key=lambda r: (r.created_at, r.key))
    rows = stamp_comparable(rows)
    # Computed for EVERY read, not behind `include_config`: that flag gates serving the whole
    # resolved map on each row, which is bulk, while a coordinate over the varying keys alone is
    # small and is what makes the roster a grid. The source is the same `_config_of` either way,
    # off searchpoints already in hand — no document is opened for it.
    levels = levels_by_subject(rows, {k: _config_of(h.point) for k, h in heads.items()})
    rows = [r.model_copy(update={"levels": levels[r.key]}) for r in rows]

    # Campaign subjects only: an edit is ranked against its own campaign's ORIGIN, which is the
    # anchor a course or a candidate does not define — both sit inside a campaign whose origin is
    # already a subject the operator can tick.
    accums: dict[str, _Accum] = {}
    if include_ranking:
        for row in (r for r in rows if r.kind == "campaign"):
            cycle_dir = heads[row.key].cycle_dir
            hop = CycleHop(campaign_id=row.campaign_id, cycle_id=cycle_dir.name)
            for round_file in sorted(CycleLayout(cycle_dir).rounds.glob(ROUND_GLOB)):
                if round_file.name == "round_0000.json":
                    continue
                _accumulate_round(
                    read_json_tolerant(round_file, {}), row.values, hop, accums, compiled
                )

    edits = sorted(
        (_finalize(state_hash, acc) for state_hash, acc in accums.items()),
        key=lambda r: r.anchor_effect,
        reverse=True,
    )
    scored = {r.key: r.values for r in rows if r.values}
    # Read before the envelope: the width a corrected verdict needs depends on how many tests the
    # correction spans, so `power` cannot be built without the count `metric` arrives at.
    reading = metric_reading(spec_metric, rows, available)
    return Evidence(
        generated_at=utcnow_iso(),
        subjects=rows,
        comparability=comparability(rows),
        metric=reading,
        unread_subjects=sorted(set(wanted) - set(heads)),
        factors=factors(rows, levels, spec_metric),
        grid=grid_reading(rows, levels, spec_metric, grid) if grid else None,
        replicates=replicates(rows),
        variance=(decomposition := variance(scored)),
        power=power(decomposition, rows, n_tests=reading.n_tests),
        order_confound=order_confound(rows),
        ranking_computed=include_ranking,
        edits=edits,
        spread=_edit_spread(edits),
    )


# --- resolving a subject to the rows it stands for ---------------------------------------------


def _at(stores: Stores, spec: SubjectSpec) -> tuple[Stores, Path] | None:
    """The tree a subject lives in, and its campaign directory there. ``None`` where either is
    absent — a subject nothing answers rides ``unread_subjects``, so a mistyped id or a sandbox
    since deleted must not fail the whole read for every other channel in it.

    A sandbox is structurally an ordinary projects tree, so once ``descend_store`` has stepped
    into it every resolver below works unchanged — which is why an L4 inner run needed no second
    walker, only an address that could name one.
    """
    try:
        leaf = descend_store(stores, spec.inside)
    except (BadRequestError, NotFoundError):
        return None
    campaign_dir = next(
        (d for d in leaf.campaigns.iter_campaign_dirs() if d.name == spec.campaign_id), None
    )
    return None if campaign_dir is None else (leaf, campaign_dir)


def _resolve_head(stores: Stores, spec: SubjectSpec, campaign_dir: Path) -> _Head | None:
    """The rows a subject's numbers come from, or ``None`` where nothing on disk answers it."""
    manifest = read_json_tolerant(campaign_dir / "campaign.json", {})
    dataset_name = _dataset_name(manifest)
    if spec.kind == "campaign":
        # The campaign's ROOT cycle — C0 — named by the manifest, never whichever sibling a
        # directory walk happened to reach last. A forked campaign holds `cycle_x` beside
        # `cycle_x_fork_y`, so walking them read the FORK's origin under the campaign's name with
        # nothing on either surface saying which cycle the row came from. A fork is addressable
        # here as its own `course:` subject instead.
        cycle_dir = campaign_cycles_dir(campaign_dir) / str(manifest.get("root_cycle_id", ""))
        origin = _point_at(cycle_dir, 0, label="")
        if origin is None:
            return None
        return _Head(
            cycle_dir=cycle_dir,
            label=spec.campaign_id,
            dataset_name=dataset_name,
            created_at=str(manifest.get("created_at", "")),
            point=_masked(origin, spec.samples),
        )

    cycle_dir = campaign_cycles_dir(campaign_dir) / spec.cycle_id
    if not CycleLayout(cycle_dir).rounds.is_dir():
        return None
    index = read_json_tolerant(CycleLayout(cycle_dir).manifest, {})
    scenario, chain = (None, None)
    if spec.lens:
        # Under a lens the head is the last point the record still speaks for — the counterfactual
        # winner at the round the two readings part, or the branch's own if they never do. The
        # chain has to be walked to find it: each round's decision depends on the one before it.
        resolved = _scenario(stores, spec, cycle_dir)
        if resolved is None:
            return None
        scenario, chain = resolved
        point: _ChainPoint | None = chain[-1]
    else:
        point = (
            _course_head(cycle_dir)
            if spec.kind == "course"
            else _candidate_point(cycle_dir, spec.candidate_id)
        )
    if point is None:
        return None
    return _Head(
        cycle_dir=cycle_dir,
        # The cycle names the branch; the candidate names itself. A course's own `cycle_id` rather
        # than its campaign's, because two courses of one campaign are exactly what this compares.
        label=spec.cycle_id if spec.kind == "course" else point.label,
        dataset_name=dataset_name,
        created_at=str(index.get("created_at", "")),
        point=_masked(point, spec.samples),
        scenario=scenario,
        chain=chain,
    )


def _masked(point: _ChainPoint, samples: frozenset[int] | None) -> _ChainPoint:
    """The same searchpoint read over a SUBSET of what it was measured on — "seventeen of the
    twenty-eight", the operator's own question. Rows are dropped, never re-derived: every value
    that survives is one this candidate actually recorded on that sample."""
    if samples is None:
        return point
    return point._replace(rows=[r for r in point.rows if r.get("sample_id") in samples])


def _scenario(
    stores: Stores, spec: SubjectSpec, cycle_dir: Path
) -> tuple[ScenarioReading, list[_ChainPoint]] | None:
    """This branch as the lens would have run it: the counterfactual winner chain, plus what it
    says about the record. ``None`` where the branch has no readable chain at all.

    The chain's points are the arms the run MEASURED — the fold picks among them and invents
    none — so a channel plotted from it plots measurements, under a criterion that would have
    carried a different one of them forward.
    """
    criterion = compile_round_scorer(spec.lens.removeprefix(LENS_SCORE_PREFIX))
    record = load_mask_record(stores, spec.campaign_id, spec.samples)
    cycle = next((c for c in record.cycles if c.cycle_id == spec.cycle_id), None)
    if cycle is None:
        return None
    steps = scenario_spine(cycle, criterion)
    points = [p for s in steps if (p := _point_at(cycle_dir, s.round, candidate_id=s.candidate_id))]
    if not points:
        return None
    last = steps[-1]
    parted = last.candidate_id != last.recorded_id
    return (
        ScenarioReading(
            recorded_winner_id=last.recorded_id,
            scenario_winner_id=last.candidate_id,
            winner_changed=parted,
            first_divergent_round=last.round if parted else None,
            # Every step but the one the walk returned on — a round the fold could not decide is
            # still a round the two readings agreed about.
            invariant_rounds=len(steps) - 1 if parted else len(steps),
            # Off the CYCLE, not the chain: the chain stops at the parting, and the denominator is
            # how many rounds this branch has for that prefix to be a fraction of.
            total_rounds=len(cycle.rounds),
            n_samples_scored=len(_masked(points[-1], spec.samples).rows),
            note=_SCENARIO_NOTE,
        ),
        [_masked(p, spec.samples) for p in points],
    )


# Served rather than documented, on the `Comparability.note` pattern: the honest limit of a lens is
# exactly what a surface forgets to restate, and a chart of counterfactual winners is read as a
# counterfactual RUN unless the reading says otherwise.
_SCENARIO_NOTE = (
    "This re-ranks the RECORD under the formula you named — it does not re-run the campaign. "
    "θ is not re-fitted and no election is replayed, so the round named here is where the two "
    "readings first part, not a verdict the campaign reached. The chain STOPS at that round: past "
    "it the run would have stood on a parent it never had, and no measurement says what that "
    "produces. `ab` replay is what re-derives an election exactly."
)


def _crowns(cycle_dir: Path) -> dict[int, str]:
    """``round -> the label its election crowned``. A round that HELD (empty label) or never
    elected at all is absent: neither moved the branch's head, and only the ledger separates
    them from a round that crowned somebody."""
    return {
        rnd: election.winner_label
        for rnd, election in scan_ledger_elections(CycleLayout(cycle_dir).ledger).items()
        if election.winner_label
    }


def _point_in_doc(
    doc: dict[str, Any], round_num: int, *, label: str = "", candidate_id: str = ""
) -> _ChainPoint | None:
    """One arm of one round document, addressed the way its caller HAS it.

    By LABEL for a crown, because that is the key the ledger's election records and the one a
    resume does not re-mint — joining a crown on ``candidate_id`` resolves to nothing after one.
    By ID for a counterfactual or an addressed searchpoint. Neither takes the round's FIRST arm,
    which at round 0 is the origin and the only one there is.
    """
    rows_by_id = doc.get("all_candidate_results") or {}
    if not rows_by_id:
        return None
    entries = {
        str(cs.get("candidate_id") or ""): cs
        for cs in doc.get("candidate_scores") or []
        if isinstance(cs, dict)
    }
    if label:
        found = next(
            (cid for cid, cs in entries.items() if cid and str(cs.get("label") or "") == label),
            None,
        )
    else:
        found = candidate_id or next(iter(rows_by_id))
    rows = rows_by_id.get(found) if found else None
    if found is None or not isinstance(rows, list):
        return None
    entry = entries.get(found) or {}
    return _ChainPoint(
        round=round_num,
        candidate_id=found,
        label=str(entry.get("label") or "") or found,
        rows=list(rows),
        scores=entry,
    )


def _point_at(
    cycle_dir: Path, round_num: int, *, label: str = "", candidate_id: str = ""
) -> _ChainPoint | None:
    return _point_in_doc(
        read_json_tolerant(CycleLayout(cycle_dir).round_file(round_num), {}),
        round_num,
        label=label,
        candidate_id=candidate_id,
    )


def _course_head(cycle_dir: Path) -> _ChainPoint | None:
    """A branch reads at its HEAD — the last winner it elected, or its origin where no round has
    crowned one yet. Not the best candidate ever measured on it: the branch is what the run
    actually carried forward, and a course whose rounds all held is honestly still at C0."""
    crowns = _crowns(cycle_dir)
    last = max(crowns) if crowns else 0
    return _point_at(cycle_dir, last, label=crowns.get(last, ""))


def _candidate_point(cycle_dir: Path, candidate_id: str) -> _ChainPoint | None:
    """One searchpoint, read off the LAST round document carrying it — a repair re-measures a
    candidate in place without re-minting it, so the newest document holds the rows that stand."""
    for round_file in sorted(CycleLayout(cycle_dir).rounds.glob(ROUND_GLOB), reverse=True):
        doc = read_json_tolerant(round_file, {})
        point = _point_in_doc(doc, int(doc.get("round", 0) or 0), candidate_id=candidate_id)
        if point is not None and point.rows:
            return point
    return None


def _winner_chain(
    head: _Head, spec: SubjectSpec, compiled: CompiledExpression
) -> list[WinnerChainPoint]:
    """The branch standing behind a head: the origin, every winner before it, then the head itself.
    Each point is read on ITS OWN cells — the subsets move between rounds, so restricting the chain
    to the head's cells would redraw earlier rounds on evidence they never had.

    Under a lens the chain is already resolved (the counterfactual winners, which no ledger holds)
    and ENDS at the round the two readings part; otherwise it is the crowns, off the elections."""
    if head.chain is not None:
        return [_winner_chain_point(p, compiled) for p in head.chain]
    crowns = _crowns(head.cycle_dir)
    at = head.point.round
    rounds = [r for r in sorted({0, *(r for r in crowns if r < at)}) if r != at]
    points = [
        p
        for r in rounds
        if (p := _point_at(head.cycle_dir, r, label=crowns.get(r, ""))) is not None
    ]
    return [_winner_chain_point(_masked(p, spec.samples), compiled) for p in (*points, head.point)]


def _config_of(point: _ChainPoint) -> dict[str, str]:
    """One searchpoint as a flat ``key -> rendered value`` map, over the three disjoint keyspaces
    `build_candidate_flat` already owns: ``node.param`` from the RESOLVED config, then the bare
    prompt fields on top.

    Resolved, never the sparse ``pipeline_overlay``: a delta is relative to a parent, and
    two searchpoints from different campaigns share none — lined up on their deltas, a panel
    would show two lists with nothing in common and call it a comparison.

    ``lineage`` is dropped for the reason `results.py::_identity_config` drops it: it is IDENTITY,
    not configuration, and it differs between any two candidates by construction — carried, it
    would report a difference on every pair no matter what they were configured with.
    """
    entry = point.scores
    fields = {k: v for k, v in (entry.get("prompt_fields") or {}).items() if k != "lineage" and v}
    return build_candidate_flat(
        flatten_sp_summary(entry.get("resolved_pipeline_params")), {"prompt_fields": fields}
    )


def _winner_chain_point(point: _ChainPoint, compiled: CompiledExpression) -> WinnerChainPoint:
    values, _ = _score_cells(compiled, cell_channels(point.rows))
    value, ci_lo, ci_hi, n_cells = merge_cells(values)
    return WinnerChainPoint(
        candidate_id=point.candidate_id,
        round=point.round,
        label=point.label,
        value=value,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n_cells,
    )


def _score_cells(
    compiled: CompiledExpression, channels: dict[str, dict[str, float]]
) -> tuple[dict[str, float], list[str]]:
    """``({cell: value}, unscorable_cells)``. A cell the metric cannot read is dropped and NAMED:
    ``ScoringTermMissingError`` is a term the row never carried and its parent a division by zero or
    a non-finite result, and both mean unscorable here — never a value of zero.

    Named rather than counted, because the two ways a cell can be blank on a chart are different
    facts: this subject MEASURED it and the metric cannot read it, or this subject never measured
    it at all. A count answers neither per cell, and a surface with only a count has to render both
    the same."""
    values: dict[str, float] = {}
    missed: list[str] = []
    for cell, row_channels in channels.items():
        try:
            values[cell] = compiled.evaluate(dict(row_channels), "this cell")
        except ScoringFormulaError:
            missed.append(cell)
    return (values, sorted(missed))


def _spend_to_round(dash: dict[str, Any]) -> dict[str, float]:
    """``round -> USD this cycle had spent by the END of it``, cumulative and filled forward.

    Folded FORWARD off the served per-round atom (``dashboard.json::spend_by_round``), never off
    the ledger a second time: the projection banks every ``TokenUsageRecord`` into its own round as
    it banks it into the cycle total, so a walk here would be a second arithmetic for one number —
    and the one that disagrees is always the one nobody is watching.

    CUMULATIVE, where the served atom is PER ROUND, because cumulative is the number this reading
    shows and the atom is what it is summed from; the reverse does not hold. Filled forward so
    every round from 0 to the last that billed has an entry: a round that spent nothing still HAS a
    cost-to-here, and leaving it out would make a lookup miss where the honest answer is "the same
    as the round before".

    THIS CYCLE'S OWN spend, so on a fork it answers what the BRANCH has spent since it cut, not
    what the line cost from the origin — the inherited prefix lives in the parent's file and the
    read side cannot follow that link. ``cycle_spend_usd`` beside it is the roll-up that does
    include it, which is why both are served.
    """
    per_round: dict[int, float] = {}
    for key, rollup in (dash.get("spend_by_round") or {}).items():
        try:
            rnd = int(key)
        except (TypeError, ValueError):
            continue
        usd = (rollup or {}).get("total_used_usd")
        if isinstance(usd, int | float):
            per_round[rnd] = per_round.get(rnd, 0.0) + float(usd)
    if not per_round:
        return {}
    running = 0.0
    out: dict[str, float] = {}
    for rnd in range(max(per_round) + 1):
        running += per_round.get(rnd, 0.0)
        out[str(rnd)] = round(running, 6)
    return out


def _reading_row(
    spec: SubjectSpec,
    head: _Head,
    compiled: CompiledExpression,
    channels: dict[str, dict[str, float]],
    *,
    include_winner_chain: bool,
    include_config: bool,
) -> SubjectReading:
    """One roster row. The arm, the instrument, the ruler and the spend are facts about the CYCLE
    the subject sits in, so a course and a candidate read them off their own cycle rather than off
    the campaign's root — two courses of one campaign can sit on different rulers."""
    values, unscorable = _score_cells(compiled, channels)
    layout = CycleLayout(head.cycle_dir)
    doc = read_json_tolerant(layout.round_file(0), {})
    dash = read_json_tolerant(layout.dashboard, {})
    spend = dash.get("spend")
    # The configuration the cycle ran under IS the arm — its hashes are stamped on round 0
    # precisely so a campaign paused before round 1 still names what it measured.
    hashes = doc.get("optimizer_prompt_hashes")
    # `None` on any backend declaring no measurement identity — every campaign shares that absence,
    # so the arm alone is the whole grouping there.
    instrument = instrument_of(doc.get("pipeline_params"))
    raw = doc.get("ability")
    value, ci_lo, ci_hi, n_cells = merge_cells(values)
    return SubjectReading(
        key=spec.key,
        kind=spec.kind,
        inside=list(spec.inside),
        campaign_id=spec.campaign_id,
        cycle_id=spec.cycle_id or head.cycle_dir.name,
        candidate_id=head.point.candidate_id,
        label=head.label,
        dataset_name=head.dataset_name,
        created_at=head.created_at,
        # Stamped against the whole selection one pass later — a verdict about how this row sits
        # with the others cannot be reached while the others are still being built.
        comparable=None,
        comparable_note="",
        mask=(
            SubjectMask(
                lens=spec.lens or None,
                samples=sorted(spec.samples) if spec.samples else None,
            )
            if spec.lens or spec.samples
            else None
        ),
        scenario=head.scenario,
        # A campaign is its origin and nothing precedes it; the other two stand on a branch.
        winner_chain=(
            _winner_chain(head, spec, compiled)
            if include_winner_chain and spec.kind != "campaign"
            else None
        ),
        config=_config_of(head.point) if include_config else None,
        # An UNSTAMPED round is not the origin arm — it is an UNKNOWN one, which groups with
        # nothing. Collapsing the two onto one hash makes `replicates` report every unstamped
        # campaign as a replicate of the rest, spread and all, over a shared absence.
        arm_id=_state_hash({"": dict(hashes)}) if isinstance(hashes, dict) and hashes else None,
        instrument_id=str(instrument) if isinstance(instrument, str) else None,
        ability=(AbilityReading.model_validate(raw) if isinstance(raw, dict) else None),
        round=head.point.round,
        cycle_spend_usd=(spend or {}).get("total_used_usd") if isinstance(spend, dict) else None,
        cycle_rounds_scored=max(len(list(layout.rounds.glob(ROUND_GLOB))) - 1, 0),
        spend_to_round=_spend_to_round(dash),
        values=values,
        value=value,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n_cells,
        unscorable_cells=unscorable,
    )


def _edit_spread(rows: list[RankedEdit]) -> EditSpread:
    return EditSpread(edit_effect_sd=sample_sd([r.anchor_effect for r in rows]), n_edits=len(rows))


def _accumulate_round(
    doc: dict[str, Any],
    origin_values: dict[str, float],
    hop: CycleHop,
    accums: dict[str, _Accum],
    compiled: CompiledExpression,
) -> None:
    """An edit is worth whatever the SELECTED metric says it is — seconds, dollars, rounds, lift.
    A candidate row is a cell like any other (on the recursion, its own inner campaign), so every
    channel the roster can answer, the ranking can answer too."""
    round_num = int(doc.get("round", 0) or 0)
    for cand in doc.get("candidate_scores") or []:
        cand_id = str(cand.get("candidate_id", ""))
        if not cand_id:
            continue
        prompt_state = _coerce_state(cand.get("pipeline_overlay"))
        state_hash = _state_hash(prompt_state)
        if state_hash == _ORIGIN_HASH:
            continue  # the no-op arm anchors others; it is not itself a ranked candidate
        cand_cells, _ = _score_cells(
            compiled, cell_channels((doc.get("all_candidate_results") or {}).get(cand_id) or [])
        )
        paired = {c: cand_cells[c] for c in cand_cells if c in origin_values}
        if not paired:
            continue
        acc = accums.get(state_hash)
        if acc is None:
            acc = _Accum(prompt_state, str(cand.get("label") or state_hash))
            accums[state_hash] = acc
        acc.provenance.append(
            EffectProvenance(
                campaign_id=hop.campaign_id,
                cycle_id=hop.cycle_id,
                round=round_num,
                candidate_id=cand_id,
            )
        )
        for cell, cand_fit in paired.items():
            acc.cand_by_cell.setdefault(cell, []).append(cand_fit)
            acc.orig_by_cell.setdefault(cell, []).append(origin_values[cell])


def _coerce_state(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for node, fields in raw.items():
        if isinstance(fields, dict):
            out[str(node)] = {str(k): str(v) for k, v in fields.items()}
    return out


def _finalize(state_hash: str, acc: _Accum) -> RankedEdit:
    """Aggregate one edit into its ranked row — **per cell, then across cells**, so the SE comes from
    n = CELLS and a cell measured five times cannot outweigh five cells measured once."""
    per_cell: list[CellEffect] = []
    cell_cand: list[float] = []
    cell_orig: list[float] = []
    n_meas = 0
    for cell in sorted(acc.cand_by_cell):
        cand_vals = acc.cand_by_cell[cell]
        orig_vals = acc.orig_by_cell[cell]
        mean_d, _se_d, n = paired_diff_posterior(cand_vals, orig_vals)
        per_cell.append(CellEffect(cell=cell, mean_d=mean_d, n=n))
        n_meas += n
        # ONE paired point per cell — the cell's own mean level. Equal-length lists make
        # the elementwise paired mean identical to the difference of means, so this is
        # exactly ``mean_d`` re-expressed as a (candidate, origin) pair for stage two.
        cell_cand.append(sum(cand_vals) / len(cand_vals))
        cell_orig.append(sum(orig_vals) / len(orig_vals))

    # The same exact test the pairwise table runs, so the two readings on one page cannot hold an
    # edit to different standards — and a single wild cell cannot carry an edit up the ranking.
    anchor, ci_lo, ci_hi, _p, _n = exact_paired_reading(cell_cand, cell_orig)
    return RankedEdit(
        state_hash=state_hash,
        label=acc.label,
        provenance=acc.provenance,
        anchor_effect=anchor,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=len(per_cell),
        n_measurements=n_meas,
    )


__all__ = [
    "EditSpread",
    "EffectProvenance",
    "Evidence",
    "RankedEdit",
    "campaigns_on_dataset",
    "subject_evidence",
]
