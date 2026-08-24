"""What a SET of campaigns jointly says — the read that answers "I ran this four times, now
what?". Recomputed from disk per read, never persisted, zero LLM calls, nothing crowned.

Two halves, and the round-0 half is the one that keeps being needed: a campaign stopped before its
first L1 round still has an origin panel, a ruler and a spend, so the roster, the comparability
check, the variance decomposition and the confound flags all answer. The ranking needs a scored
candidate, walks every round document, and is therefore opt-in (``include_ranking``).

**No L4 anywhere in here.** A cell is whatever the campaign scored one row against — an inner
campaign on the recursion, a sample everywhere else — and `evidence_metrics.py::cell_channels` is
the only path from a row to a number, reading the L4 proxy where it exists and the row's own
fitness otherwise. Same arithmetic either way (`scoring/selection.py::matched_parent_lift` states
the same rule for the per-round interval).

Everything is in the SELECTED metric's own units, never composite fitness: fitness is what a
campaign's own `scoring` formula made of the measurand, so pooling on it averages numbers produced
by different formulas and calls the result one quantity.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from pydantic import Field

from promptpotter.application.evidence_metrics import (
    MEASURAND,
    MetricSpec,
    available_channels,
    catalogue_for,
    cell_channels,
    resolve_metric,
)
from promptpotter.application.scoring.formula.compiler import (
    CompiledExpression,
    ScoringFormulaError,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    origin_rows_from_disk,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.statistics import (
    cells_for_exact_verdict,
    exact_p_floor,
    exact_paired_reading,
    holm_adjusted,
    mean_ci_t,
    min_detectable_effect,
    paired_diff_posterior,
    rank_correlation,
    sample_sd,
    two_way_effect_sds,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

_ORIGIN_HASH = "origin"
# |rho| at or above this and the roster's ordering IS its chronology — see `OrderConfound`.
_ORDER_CONFOUND_RHO = 0.9

ComparabilityReason = Literal["one_ruler", "rulers_differ", "ruler_unstamped", "datasets_differ"]


# --- the ranking: what a scored EDIT is worth (needs round >= 1, and is opt-in) ---


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
    """One unique candidate state — a ``pipeline_params_override`` — aggregated across every
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


# --- the origin half: what the campaigns themselves say, before any candidate exists ---


class CampaignReading(StrictModel):
    """One campaign, read under the selected metric — its identity, its per-cell values and the one
    estimate they merge to. ONE row, because a roster row and a metric reading that live in separate
    lists can disagree about the same campaign, and under the default metric they held the same
    number reached two ways.

    ``values`` is keyed by the cell's QUERY, the identity that survives across campaigns; a cell the
    metric cannot read is ABSENT from it and counted in ``n_unscorable`` rather than scored.
    ``ci_lo``/``ci_hi`` are ``None`` below two scored cells — one reading has no spread, and a
    bracket drawn from it is a fiction.
    """

    campaign_id: str
    dataset_name: str
    created_at: str
    # The configuration the campaign RAN UNDER, hashed off round 0's `optimizer_prompt_hashes`.
    # Two campaigns sharing it are replicates of one arm however much else differs, which is the
    # fact a roster listing campaigns cannot show. `None` where round 0 carries no hashes: the arm
    # is UNKNOWN, which groups with nothing — least of all with every other unstamped campaign.
    arm_id: str | None
    # The connector's measurement-identity fingerprint — the RULER the arm was read against, moved
    # by any inner prompt, panel prose, layout or estimator edit. Two campaigns sharing an arm but
    # not this are NOT replicates: their spread is code drift wearing a noise label.
    instrument_id: str | None
    # This origin's own reading, carried for the scale on it: campaigns whose rulers differ
    # measured on different scales, so their ABSOLUTE levels are not one quantity however well
    # paired the cells are. `None` where round 0 carries no reading at all.
    ability: AbilityReading | None
    spend_usd: float | None
    rounds_scored: int
    values: dict[str, float]
    value: float | None
    ci_lo: float | None
    ci_hi: float | None
    n_cells: int
    n_unscorable: int


class PairwiseComparison(StrictModel):
    """One unordered pair, blocked on the cells BOTH campaigns scored — pairing removes cell
    difficulty instead of carrying it as noise, which is the same reason ``matched_parent_lift``
    pairs rather than differencing two means.

    ``a`` precedes ``b`` in the roster's oldest-first order, so ``median_shift = b - a`` has one
    reading across the whole table. The interval and both p-values are ``None`` below two shared
    cells: nothing was tested there, which a ``1.0`` would misreport as a test that found nothing.

    The test is EXACT (``exact_paired_reading``), never Student-t: at the widths a panel runs, a t
    p can sit below what any exact test on that many pairs is able to return, which is resolution
    taken from the assumed tail rather than from the cells. ``EvidencePower.exact_p_floor`` says
    which verdicts the width can reach at all, before a cell is spent.
    """

    campaign_a: str
    campaign_b: str
    # Hodges-Lehmann: the median of the pairwise Walsh averages, not the mean of the differences.
    # One outlier cell moves the mean by 1/n of itself and moves this by nothing.
    median_shift: float
    ci_lo: float | None
    ci_hi: float | None
    p_value: float | None
    # Holm-Bonferroni across every pair in THIS read that carries a p. Served beside the raw value
    # rather than replacing it, so the correction is visible instead of baked in.
    p_adjusted: float | None
    n_cells: int


class MetricReading(StrictModel):
    """The selection read under ONE metric, echoed back with the vocabulary it was chosen from — a
    stale render cannot then show new bars under the old metric's label."""

    spec: MetricSpec
    catalogue: list[MetricSpec]
    # The channel names an expression may use ON THIS SELECTION — served rather than documented,
    # so a composed metric cannot name a term the selection would silently read as zero. It is the
    # INTERSECTION: a channel only some campaigns carry would compare one side against nothing.
    namespace: list[str]
    # The cells EVERY campaign SCORED under this metric — a subset of the measured intersection,
    # because a cell can be measured and still be unreadable here. The charts share this axis.
    scored_cells: list[str]
    pairwise: list[PairwiseComparison]
    n_tests: int


class Comparability(StrictModel):
    """Whether the selection's ABSOLUTE levels are one quantity, and WHY — two ways to fail and a
    reader must be able to tell them apart.

    ``verdict`` is ``None`` for UNKNOWN, which is not ``True`` and must never render as it.
    ``datasets_differ`` dominates: campaigns on different datasets measure different things, so no
    ruler agreement could rescue the comparison. Their cells never intersect either, which is why
    the variance decomposition is correctly absent rather than empty.
    """

    verdict: bool | None
    reason: ComparabilityReason
    datasets: list[str]
    n_rulers: int
    # The sentence BOTH surfaces render, so neither keeps a per-reason map that can go an arm out
    # of step with the other's — including the one reason that QUALIFIES rather than disqualifies
    # the column, which a map indexed defensively drops in silence.
    note: str


class EvidenceVariance(StrictModel):
    """The additive cell + arm decomposition over the cells every campaign measured.

    ``arm_effect_sd`` alone decides nothing: under the null an arm mean still scatters by
    ``null_arm_scatter``, so an arm SD at or below it is noise wearing a ranking.
    """

    cell_effect_sd: float
    arm_effect_sd: float
    residual_sd: float
    null_arm_scatter: float
    arm_sd_below_noise: bool
    n_cells: int
    n_arms: int


class EvidencePower(StrictModel):
    """What this instrument can and cannot resolve, at the width it is currently run.

    ``cells_for_largest_gap`` prices the question actually on the table — how many cells per arm it
    would take to resolve the biggest gap the roster already shows.

    The last two are the harder limit and they answer a different question. Effect size decides the
    first three; ``exact_p_floor`` is what the WIDTH alone permits, so a panel below
    ``cells_for_corrected_verdict`` cannot produce a Holm-corrected result however large the effect —
    a clean sweep of every cell included. Buy width to that line before buying it for power.
    """

    paired_se: float
    min_detectable_effect: float
    largest_arm_gap: float
    cells_per_arm: int
    cells_for_largest_gap: int | None
    exact_p_floor: float
    cells_for_corrected_verdict: int


class ArmReplicate(StrictModel):
    """One arm that ran more than once. ``level_spread`` is the cheapest noise reading on the
    board — taken from campaigns already paid for, and invisible to a roster that lists campaigns
    rather than arms.

    It is a noise reading ONLY at ``n_instruments == 1``. Above that the arm was held constant while
    the INSTRUMENT moved underneath it, so the spread measures the engine's own drift and is served
    as that — a replicate that was never one is the more useful finding, but it is not noise."""

    arm_id: str
    campaign_ids: list[str]
    level_spread: float
    n_instruments: int


class OrderConfound(StrictModel):
    """Run order against outcome. A campaign-at-a-time comparison confounds the arm with WHEN it
    ran — the archive grows between runs, so the ruler and the caches both move with the calendar.
    """

    level_vs_order: float | None
    spend_vs_order: float | None
    n_campaigns: int
    # The VERDICT, served rather than left to a threshold each reader picks: a near-perfect rank
    # correlation means the roster's ordering is also its chronology and the two cannot be told
    # apart. Deliberately strict — this disqualifies a comparison, so it fires only on a monotone.
    order_confounded: bool


class Evidence(StrictModel):
    """The whole read for one selection of campaigns — recomputed on every fetch."""

    generated_at: str
    # Oldest first — the confound reads off this order. Identity AND the metric reading in one
    # row, so nothing downstream joins two lists on `campaign_id`.
    campaigns: list[CampaignReading]
    comparability: Comparability
    # WHICH number everything below is about — the picker's vocabulary, the merged per-campaign
    # intervals and every pairwise test, all under one selection. Non-optional: the default always
    # resolves, so there is no state where a chart is drawn under no named metric.
    metric: MetricReading
    # Ids that were ASKED for and answered nothing — no campaign of that name, no round-0 document
    # under its root cycle, or an origin whose rows carry no channel. Served because the roster is
    # otherwise the only evidence they were dropped, and a campaign that silently thins a
    # selection is the campaign-level twin of scoring an unread cell as zero.
    unread_campaigns: list[str] = Field(default_factory=list)
    replicates: list[ArmReplicate] = Field(default_factory=list)
    variance: EvidenceVariance | None = None
    power: EvidencePower | None = None
    order_confound: OrderConfound | None = None
    # Ranked desc by anchor_effect. Empty unless `include_ranking` was asked for — the only walk
    # here that opens a round document past round 0, and the reason it alone is opt-in.
    ranking_computed: bool = False
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


def campaign_evidence(
    stores: Stores,
    campaign_ids: list[str],
    *,
    include_ranking: bool = False,
    metric: str = MEASURAND,
) -> Evidence:
    """Reduce the named campaigns into one evidence read, under one metric. *campaign_ids* may span
    datasets, and may name a campaign that no longer exists — an absent one is simply not in the
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
    wanted = set(campaign_ids)
    channels_by_campaign: dict[str, dict[str, dict[str, float]]] = {}
    # campaign_id -> (cycle_dir, dataset_name, created_at), insertion-ordered.
    found: dict[str, tuple[Path, str, str]] = {}

    for campaign_dir in stores.campaigns.iter_campaign_dirs():
        if campaign_dir.name not in wanted:
            continue
        manifest = read_json_tolerant(campaign_dir / "campaign.json", {})
        # The campaign's ROOT cycle — C0 — named by the manifest, never whichever sibling a
        # directory walk happened to reach last. A forked campaign holds `cycle_x` beside
        # `cycle_x_fork_y`, so walking them read the FORK's origin under the campaign's name with
        # nothing on either surface saying which cycle the row came from.
        cycle_dir = campaign_cycles_dir(campaign_dir) / str(manifest.get("root_cycle_id", ""))
        if not CycleLayout(cycle_dir).rounds.is_dir():
            continue
        channels = cell_channels(origin_rows_from_disk(cycle_dir))
        if not channels:
            continue
        channels_by_campaign[campaign_dir.name] = channels
        found[campaign_dir.name] = (
            cycle_dir,
            _dataset_name(manifest),
            str(manifest.get("created_at", "")),
        )

    # An unmeasured selection is not a metric problem, and answering it as one is what two
    # ordinary actions — ticking a campaign whose origin has not run, mistyping an id — used to
    # get back: "Metric 'measurand' is not one this selection can answer", about a vocabulary,
    # when the fact is that there is nothing here to have a vocabulary over.
    if not found:
        raise ValueError(
            f"None of {', '.join(sorted(wanted)) or 'the campaigns named'} has a scored round-0 "
            "origin to read. A campaign answers here once its origin has run; one that does not "
            "exist under this tenant answers never."
        )
    # WHICH metric is decidable only once the rows are in hand: the measurand is the seed's own
    # lift where the cells carry one and the cell's own fitness where they do not, and a metric
    # nothing in this selection answers is never offered.
    available = available_channels(channels_by_campaign)
    spec, compiled = resolve_metric(metric, available)

    rows: list[CampaignReading] = []
    for campaign_id, (cycle_dir, dataset_name, created_at) in found.items():
        values, n_unscorable = _score_cells(compiled, channels_by_campaign[campaign_id])
        rows.append(
            _reading_row(cycle_dir, campaign_id, dataset_name, created_at, values, n_unscorable)
        )
    rows.sort(key=lambda r: (r.created_at, r.campaign_id))

    # The one expensive walk, and the only one that opens a round document past round 0.
    accums: dict[str, _Accum] = {}
    if include_ranking:
        for row in rows:
            cycle_dir = found[row.campaign_id][0]
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
    scored = {r.campaign_id: r.values for r in rows if r.values}
    # Read before the envelope: the width a corrected verdict needs depends on how many tests the
    # correction spans, so `power` cannot be built without the count `metric` arrives at.
    metric_reading = _metric_reading(spec, rows, available)
    return Evidence(
        generated_at=utcnow_iso(),
        campaigns=rows,
        comparability=_comparability(rows),
        metric=metric_reading,
        unread_campaigns=sorted(wanted - set(found)),
        replicates=_replicates(rows),
        variance=(variance := _variance(scored)),
        power=_power(variance, rows, n_tests=metric_reading.n_tests),
        order_confound=_order_confound(rows),
        ranking_computed=include_ranking,
        edits=edits,
        spread=_edit_spread(edits),
    )


def _score_cells(
    compiled: CompiledExpression, channels: dict[str, dict[str, float]]
) -> tuple[dict[str, float], int]:
    """``({cell: value}, n_unscorable)``. A cell the metric cannot read is dropped and COUNTED:
    ``ScoringTermMissingError`` is a term the row never carried and its parent a division by zero or
    a non-finite result, and both mean unscorable here — never a value of zero."""
    values: dict[str, float] = {}
    missed = 0
    for cell, row_channels in channels.items():
        try:
            values[cell] = compiled.evaluate(dict(row_channels), "this cell")
        except ScoringFormulaError:
            missed += 1
    return (values, missed)


def _metric_reading(
    spec: MetricSpec, rows: list[CampaignReading], available: frozenset[str]
) -> MetricReading:
    """The vocabulary the selection was read under, plus every pairwise test over it. The
    per-campaign half lives on the roster rows themselves."""
    scored = [r for r in rows if r.values]
    shared = sorted(set.intersection(*(set(r.values) for r in scored))) if scored else []
    pairwise = _pairwise(scored)
    return MetricReading(
        spec=spec,
        catalogue=list(catalogue_for(available)),
        namespace=sorted(available),
        scored_cells=shared,
        pairwise=pairwise,
        n_tests=sum(1 for p in pairwise if p.p_value is not None),
    )


def _pairwise(rows: list[CampaignReading]) -> list[PairwiseComparison]:
    """Every unordered pair, each blocked on the cells BOTH scored — strictly more evidence than
    the roster-wide intersection, and the honest paired n for that one comparison, which is why it
    is served per row."""
    out: list[PairwiseComparison] = []
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            cells = sorted(set(a.values) & set(b.values))
            if not cells:
                continue
            shift, lo, hi, p_value, n = exact_paired_reading(
                [b.values[c] for c in cells], [a.values[c] for c in cells]
            )
            out.append(
                PairwiseComparison(
                    campaign_a=a.campaign_id,
                    campaign_b=b.campaign_id,
                    median_shift=shift,
                    ci_lo=lo,
                    ci_hi=hi,
                    p_value=p_value,
                    p_adjusted=None,
                    n_cells=n,
                )
            )
    tested = [i for i, r in enumerate(out) if r.p_value is not None]
    adjusted = holm_adjusted([out[i].p_value or 0.0 for i in tested])
    for slot, i in enumerate(tested):
        out[i] = out[i].model_copy(update={"p_adjusted": adjusted[slot]})
    return out


def _reading_row(
    cycle_dir: Path,
    campaign_id: str,
    dataset_name: str,
    created_at: str,
    values: dict[str, float],
    n_unscorable: int,
) -> CampaignReading:
    layout = CycleLayout(cycle_dir)
    doc = read_json_tolerant(layout.round_file(0), {})
    dash = read_json_tolerant(layout.dashboard, {})
    spend = dash.get("spend")
    # The configuration the campaign ran under IS the arm — its hashes are stamped on round 0
    # precisely so a campaign paused before round 1 still names what it measured.
    hashes = doc.get("optimizer_prompt_hashes")
    # `None` on any backend declaring no measurement identity — every campaign shares that absence,
    # so the arm alone is the whole grouping there.
    params = doc.get("pipeline_params")
    generate = params.get("l1_generate") if isinstance(params, dict) else None
    instrument = generate.get("inner_origin") if isinstance(generate, dict) else None
    raw = doc.get("ability")
    ordered = [values[c] for c in sorted(values)]
    bracketed = mean_ci_t(ordered)
    return CampaignReading(
        campaign_id=campaign_id,
        dataset_name=dataset_name,
        created_at=created_at,
        # An UNSTAMPED round is not the origin arm — it is an UNKNOWN one, which groups with
        # nothing. Collapsing the two onto one hash makes `_replicates` report every unstamped
        # campaign as a replicate of the rest, spread and all, over a shared absence.
        arm_id=_state_hash({"": dict(hashes)}) if isinstance(hashes, dict) and hashes else None,
        instrument_id=str(instrument) if isinstance(instrument, str) else None,
        ability=(AbilityReading.model_validate(raw) if isinstance(raw, dict) else None),
        spend_usd=(spend or {}).get("total_used_usd") if isinstance(spend, dict) else None,
        rounds_scored=max(len(list(layout.rounds.glob(ROUND_GLOB))) - 1, 0),
        values=values,
        value=bracketed[0] if bracketed else (ordered[0] if ordered else None),
        ci_lo=bracketed[1] if bracketed else None,
        ci_hi=bracketed[2] if bracketed else None,
        n_cells=len(ordered),
        n_unscorable=n_unscorable,
    )


def _replicates(rows: list[CampaignReading]) -> list[ArmReplicate]:
    by_arm: dict[str, list[CampaignReading]] = {}
    for row in rows:
        if row.arm_id is not None:
            by_arm.setdefault(row.arm_id, []).append(row)
    out = []
    for arm, same in sorted(by_arm.items()):
        levels = [r.value for r in same if r.value is not None]
        if len(levels) > 1:
            out.append(
                ArmReplicate(
                    arm_id=arm,
                    campaign_ids=[r.campaign_id for r in same],
                    level_spread=max(levels) - min(levels),
                    n_instruments=len({r.instrument_id for r in same}),
                )
            )
    return out


def _comparability(rows: list[CampaignReading]) -> Comparability:
    datasets = sorted({r.dataset_name for r in rows if r.dataset_name})
    readings = [r.ability for r in rows]
    stamped = [a for a in readings if a is not None and a.ruler_id is not None]
    reason: ComparabilityReason
    verdict: bool | None
    note: str
    if len(datasets) > 1:
        # Different measurands entirely — no ruler agreement could rescue this, so it outranks
        # everything below.
        reason, verdict = "datasets_differ", False
        note = (
            "Comparability NO — this selection spans several datasets, which measure different "
            "things. The values are not one quantity and no pairing rescues them; the roster and "
            "spend still compare, the numbers do not."
        )
    elif not readings or len(stamped) != len(readings):
        reason, verdict = "ruler_unstamped", None
        note = (
            "Comparability UNKNOWN — at least one origin predates the ruler stamp, which is not "
            "the same as yes. Absolute levels above may sit on different δ scales: pair on cells, "
            "do not read the value column across campaigns."
        )
    elif not all(a.comparable_to(stamped[0]) for a in stamped):
        reason, verdict = "rulers_differ", False
        note = (
            "Comparability NO — these origins were measured on different δ rulers, so their "
            "absolute values are not one quantity. Only within-ruler comparisons hold."
        )
    else:
        reason, verdict = "one_ruler", True
        note = (
            "Comparability YES — these origins were measured on one δ ruler, so their values are "
            "directly comparable."
        )
    return Comparability(
        verdict=verdict,
        reason=reason,
        datasets=datasets,
        n_rulers=len({a.ruler_id for a in stamped}),
        note=note,
    )


def _variance(by_arm: dict[str, dict[str, float]]) -> EvidenceVariance | None:
    """One column per CAMPAIGN, not per arm id: two campaigns sharing an arm are replicates and
    each is its own reading, which is exactly what the residual is estimated from."""
    decomposed = two_way_effect_sds(by_arm)
    if decomposed is None:
        return None
    cell_sd, arm_sd, residual = decomposed
    n_cells = len(set.intersection(*(set(v) for v in by_arm.values())))
    null_scatter = residual / (n_cells**0.5)
    return EvidenceVariance(
        cell_effect_sd=cell_sd,
        arm_effect_sd=arm_sd,
        residual_sd=residual,
        null_arm_scatter=null_scatter,
        arm_sd_below_noise=arm_sd <= null_scatter,
        n_cells=n_cells,
        n_arms=len(by_arm),
    )


def _power(
    variance: EvidenceVariance | None, rows: list[CampaignReading], *, n_tests: int
) -> EvidencePower | None:
    """The paired SE of a two-arm contrast: pairing removes the cell effect — the term that is
    largest here — and leaves the residual on both arms, hence ``residual * sqrt(2 / cells)``.

    Beside it the width limit, which no SE can see: an exact test on this many cells has a smallest
    reachable p, and *n_tests* is the correction it has to clear."""
    levels = [r.value for r in rows if r.value is not None]
    if variance is None or len(levels) < 2 or variance.n_cells < 1:
        return None
    se = variance.residual_sd * (2.0 / variance.n_cells) ** 0.5
    mde = min_detectable_effect(se)
    gap = max(levels) - min(levels)
    # Same k the MDE used, re-solved for the cell count: n = 2 * (k * residual / gap)^2.
    needed = None
    if gap > 0.0 and se > 0.0:
        needed = math.ceil(2.0 * ((mde / se) * variance.residual_sd / gap) ** 2)
    return EvidencePower(
        paired_se=se,
        min_detectable_effect=mde,
        largest_arm_gap=gap,
        cells_per_arm=variance.n_cells,
        cells_for_largest_gap=needed,
        exact_p_floor=exact_p_floor(variance.n_cells),
        cells_for_corrected_verdict=cells_for_exact_verdict(n_tests),
    )


def _order_confound(rows: list[CampaignReading]) -> OrderConfound | None:
    """*rows* is already oldest-first, so the index IS the chronology."""
    if len(rows) < 3:
        return None
    order = [float(i) for i in range(len(rows))]

    def rho(values: list[float | None]) -> float | None:
        if any(v is None for v in values):
            return None
        return rank_correlation(order, [float(v) for v in values if v is not None])

    level_rho = rho([r.value for r in rows])
    return OrderConfound(
        level_vs_order=level_rho,
        spend_vs_order=rho([r.spend_usd for r in rows]),
        n_campaigns=len(rows),
        order_confounded=level_rho is not None and abs(level_rho) >= _ORDER_CONFOUND_RHO,
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
        prompt_state = _coerce_state(cand.get("pipeline_params_override"))
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
    "ArmReplicate",
    "CampaignReading",
    "Comparability",
    "EditSpread",
    "EffectProvenance",
    "Evidence",
    "EvidencePower",
    "EvidenceVariance",
    "OrderConfound",
    "RankedEdit",
    "campaign_evidence",
    "campaigns_on_dataset",
]
