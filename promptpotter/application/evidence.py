"""What a SET of campaigns jointly says — the read that answers "I ran this four times, now
what?". Recomputed from disk per read, never persisted, zero LLM calls, nothing crowned.

Two halves, and the round-0 half is the one that keeps being needed: a campaign stopped before its
first L1 round still has an origin panel, a ruler and a spend, so the roster, the comparability
check, the variance decomposition and the confound flags all answer. The ranking needs a scored
candidate, walks every round document, and is therefore opt-in (``include_ranking``).

**No L4 anywhere in here.** A cell is whatever the campaign scored one row against — an inner
campaign on the recursion, a sample everywhere else — and `_cell_levels` reads the L4 proxy where
it exists and the row's own fitness otherwise. Same arithmetic either way
(`scoring/selection.py::matched_parent_lift` states the same rule for the per-round interval).

Everything is in the measurand's own units, never composite fitness: fitness is what a campaign's
own `scoring` formula made of the measurand, so pooling on it averages numbers produced by
different formulas and calls the result one quantity.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.proxies import OUTER_PROXY_KEYS
from promptpotter.domain.results import CalibrationModel
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    origin_rows_from_disk,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.statistics import (
    min_detectable_effect,
    paired_diff_posterior,
    rank_correlation,
    t_critical,
    two_way_effect_sds,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

_ORIGIN_HASH = "origin"
# |rho| at or above this and the roster's ordering IS its chronology — see `OrderConfound`.
_ORDER_CONFOUND_RHO = 0.9
# The L4 recursion's measurand, in logits. Absent on an ordinary campaign, where a row's own
# fitness is the level — one lookup, not a branch on what kind of campaign this is.
_LEVEL_KEY = OUTER_PROXY_KEYS[0]

ComparabilityReason = Literal["one_ruler", "rulers_differ", "ruler_unstamped", "datasets_differ"]


# --- the ranking: what a scored EDIT is worth (needs round >= 1, and is opt-in) ---


class CellEffect(StrictModel):
    """One environment cell's paired (candidate − origin) effect for an edit."""

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
    prompt_state: dict[str, dict[str, str]]  # {node: {field: value}} — the edit itself
    provenance: list[EffectProvenance]
    per_cell_effects: list[CellEffect]
    anchor_effect: float  # mean of the PER-CELL paired diffs — one point per cell, not per
    # occurrence, so an over-measured cell cannot outweigh uniform goodness (see _finalize)
    ci_lo: float
    ci_hi: float
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


class CampaignOrigin(StrictModel):
    """One campaign's origin panel — the roster row and the comparability check in one shape."""

    campaign_id: str
    cycle_id: str
    dataset_name: str
    created_at: str
    # The configuration the campaign RAN UNDER, hashed off round 0's `optimizer_prompt_hashes`.
    # Two campaigns sharing it are replicates of one arm however much else differs, which is the
    # fact a roster listing campaigns cannot show.
    arm_id: str
    # Which δ scale this origin's θ was read on. Campaigns whose ids differ measured on different
    # rulers, so their ABSOLUTE levels are not comparable however well paired the cells are.
    ruler_id: str | None
    calibration_model: CalibrationModel | None
    n_cells: int
    # Mean over the origin's cells, in the measurand's own units. ``None`` when no cell priced.
    origin_level: float | None
    origin_accuracy: float | None
    spend_usd: float | None
    rounds_scored: int
    stop_reason: str | None


class CampaignCells(StrictModel):
    """One campaign's per-cell origin levels, served so the browser can plot them without
    re-deriving one. Keyed by the cell's QUERY, the identity that survives across campaigns."""

    campaign_id: str
    levels: dict[str, float]


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
    """

    paired_se: float
    min_detectable_effect: float
    largest_arm_gap: float
    cells_per_arm: int
    cells_for_largest_gap: int | None


class ArmReplicate(StrictModel):
    """One arm that ran more than once. ``level_spread`` is a NOISE reading taken from campaigns
    already paid for — the cheapest one on the board, and invisible to a roster that lists
    campaigns rather than arms."""

    arm_id: str
    campaign_ids: list[str]
    level_spread: float


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
    n_cycles_scanned: int
    origins: list[CampaignOrigin]  # oldest first — the confound reads off this order
    comparability: Comparability
    # Per-campaign per-cell levels: the plotting surface, served rather than re-derived.
    cells: list[CampaignCells] = Field(default_factory=list)
    # The cells EVERY campaign measured, in one order, so a grouped bar chart and a line chart
    # agree on the axis without either picking one.
    shared_cells: list[str] = Field(default_factory=list)
    replicates: list[ArmReplicate] = Field(default_factory=list)
    variance: EvidenceVariance | None = None
    power: EvidencePower | None = None
    order_confound: OrderConfound | None = None
    # Ranked desc by anchor_effect. Empty unless `include_ranking` was asked for — the only walk
    # here that opens a round document past round 0.
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


def _cell_levels(rows: list[dict[str, Any]]) -> dict[str, float]:
    """``{cell: mean level}``, keyed by QUERY — the cell identity that survives across campaigns,
    where a per-campaign ``sample_id`` names a different cell."""
    acc: dict[str, list[float]] = {}
    for r in rows:
        cell = r.get("query")
        if not isinstance(cell, str):
            continue
        pd = r.get("pipeline_data")
        value = pd.get(_LEVEL_KEY) if isinstance(pd, dict) else None
        if not isinstance(value, int | float):
            value = r.get("fitness")
        if isinstance(value, int | float):
            acc.setdefault(cell, []).append(float(value))
    return {cell: sum(v) / len(v) for cell, v in acc.items()}


def _dataset_of(campaign_dir: Path) -> str:
    cfg = read_json_tolerant(campaign_dir / "campaign.json", {})
    block_raw = cfg.get("campaign_config")
    block = block_raw if isinstance(block_raw, dict) else cfg
    return str(block.get("dataset_name", ""))


def campaigns_on_dataset(stores: Stores, dataset_name: str) -> list[str]:
    """Campaign ids bound to *dataset_name*, read off each manifest rather than off the directory
    name — a name-shaped guess silently skips an A/B arm, a fork, a rename."""
    return [
        child.name
        for child in stores.campaigns.iter_campaign_dirs()
        if _dataset_of(child) == dataset_name
    ]


def campaign_evidence(
    stores: Stores, campaign_ids: list[str], *, include_ranking: bool = False
) -> Evidence:
    """Reduce the named campaigns into one evidence read. *campaign_ids* may span datasets, and
    may name a campaign that no longer exists — an absent one is simply not in the roster.

    ``include_ranking`` is the only expensive half: everything else opens one round-0 document per
    campaign, while the ranking walks every round document of every one of them.
    """
    wanted = set(campaign_ids)
    accums: dict[str, _Accum] = {}
    origins: list[CampaignOrigin] = []
    cells_by_campaign: dict[str, dict[str, float]] = {}
    n_cycles = 0

    for campaign_dir in stores.campaigns.iter_campaign_dirs():
        if campaign_dir.name not in wanted:
            continue
        cycles_dir = campaign_cycles_dir(campaign_dir)
        if not cycles_dir.is_dir():
            continue
        dataset_name = _dataset_of(campaign_dir)
        created_at = str(
            read_json_tolerant(campaign_dir / "campaign.json", {}).get("created_at", "")
        )
        for cycle_dir in sorted(cycles_dir.iterdir()):
            layout = CycleLayout(cycle_dir)
            if not layout.rounds.is_dir():
                continue
            origin_cells = _cell_levels(origin_rows_from_disk(cycle_dir))
            if not origin_cells:
                continue
            n_cycles += 1
            hop = CycleHop(campaign_id=campaign_dir.name, cycle_id=cycle_dir.name)
            origins.append(_origin_row(cycle_dir, hop, dataset_name, created_at, origin_cells))
            cells_by_campaign[campaign_dir.name] = origin_cells
            if not include_ranking:
                continue
            for round_file in sorted(layout.rounds.glob(ROUND_GLOB)):
                if round_file.name == "round_0000.json":
                    continue
                _accumulate_round(read_json_tolerant(round_file, {}), origin_cells, hop, accums)

    origins.sort(key=lambda o: (o.created_at, o.campaign_id))
    edits = sorted(
        (_finalize(state_hash, acc) for state_hash, acc in accums.items()),
        key=lambda r: r.anchor_effect,
        reverse=True,
    )
    shared = (
        sorted(set.intersection(*(set(v) for v in cells_by_campaign.values())))
        if cells_by_campaign
        else []
    )
    return Evidence(
        generated_at=utcnow_iso(),
        n_cycles_scanned=n_cycles,
        origins=origins,
        comparability=_comparability(origins),
        cells=[
            CampaignCells(campaign_id=o.campaign_id, levels=cells_by_campaign[o.campaign_id])
            for o in origins
            if o.campaign_id in cells_by_campaign
        ],
        shared_cells=shared,
        replicates=_replicates(origins),
        variance=(variance := _variance(cells_by_campaign)),
        power=_power(variance, origins),
        order_confound=_order_confound(origins),
        ranking_computed=include_ranking,
        edits=edits,
        spread=_edit_spread(edits),
    )


def _origin_row(
    cycle_dir: Path, hop: CycleHop, dataset_name: str, created_at: str, cells: dict[str, float]
) -> CampaignOrigin:
    layout = CycleLayout(cycle_dir)
    doc = read_json_tolerant(layout.round_file(0), {})
    dash = read_json_tolerant(layout.dashboard, {})
    spend = dash.get("spend")
    # The configuration the campaign ran under IS the arm — its hashes are stamped on round 0
    # precisely so a campaign paused before round 1 still names what it measured.
    hashes = doc.get("optimizer_prompt_hashes")
    return CampaignOrigin(
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        dataset_name=dataset_name,
        created_at=created_at,
        arm_id=_state_hash({"": dict(hashes)} if isinstance(hashes, dict) and hashes else {}),
        ruler_id=doc.get("ruler_id"),
        calibration_model=doc.get("calibration_model"),
        n_cells=len(cells),
        origin_level=(sum(cells.values()) / len(cells)) if cells else None,
        origin_accuracy=doc.get("accuracy"),
        spend_usd=(spend or {}).get("total_used_usd") if isinstance(spend, dict) else None,
        rounds_scored=max(len(list(layout.rounds.glob(ROUND_GLOB))) - 1, 0),
        stop_reason=dash.get("stop_reason"),
    )


def _replicates(origins: list[CampaignOrigin]) -> list[ArmReplicate]:
    by_arm: dict[str, list[CampaignOrigin]] = {}
    for o in origins:
        by_arm.setdefault(o.arm_id, []).append(o)
    out = []
    for arm, same in sorted(by_arm.items()):
        levels = [o.origin_level for o in same if o.origin_level is not None]
        if len(levels) > 1:
            out.append(
                ArmReplicate(
                    arm_id=arm,
                    campaign_ids=[o.campaign_id for o in same],
                    level_spread=max(levels) - min(levels),
                )
            )
    return out


def _comparability(origins: list[CampaignOrigin]) -> Comparability:
    datasets = sorted({o.dataset_name for o in origins if o.dataset_name})
    rulers = {o.ruler_id for o in origins}
    known = {r for r in rulers if r is not None}
    reason: ComparabilityReason
    verdict: bool | None
    if len(datasets) > 1:
        # Different measurands entirely — no ruler agreement could rescue this, so it outranks
        # everything below.
        reason, verdict = "datasets_differ", False
    elif not rulers or None in rulers:
        reason, verdict = "ruler_unstamped", None
    elif len(known) > 1:
        reason, verdict = "rulers_differ", False
    else:
        reason, verdict = "one_ruler", True
    return Comparability(verdict=verdict, reason=reason, datasets=datasets, n_rulers=len(known))


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
    variance: EvidenceVariance | None, origins: list[CampaignOrigin]
) -> EvidencePower | None:
    """The paired SE of a two-arm contrast: pairing removes the cell effect — the term that is
    largest here — and leaves the residual on both arms, hence ``residual * sqrt(2 / cells)``."""
    levels = [o.origin_level for o in origins if o.origin_level is not None]
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
    )


def _order_confound(origins: list[CampaignOrigin]) -> OrderConfound | None:
    """*origins* is already oldest-first, so the index IS the chronology."""
    if len(origins) < 3:
        return None
    order = [float(i) for i in range(len(origins))]

    def rho(values: list[float | None]) -> float | None:
        if any(v is None for v in values):
            return None
        return rank_correlation(order, [float(v) for v in values if v is not None])

    level_rho = rho([o.origin_level for o in origins])
    return OrderConfound(
        level_vs_order=level_rho,
        spend_vs_order=rho([o.spend_usd for o in origins]),
        n_campaigns=len(origins),
        order_confounded=level_rho is not None and abs(level_rho) >= _ORDER_CONFOUND_RHO,
    )


def _sample_sd(xs: list[float]) -> float | None:
    """Sample SD (n−1). ``None`` below two points — one reading has no spread, and reporting
    0.0 for it would claim perfect precision from a single measurement."""
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    return float((sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5)


def _edit_spread(rows: list[RankedEdit]) -> EditSpread:
    return EditSpread(edit_effect_sd=_sample_sd([r.anchor_effect for r in rows]), n_edits=len(rows))


def _accumulate_round(
    doc: dict[str, Any],
    origin_cells: dict[str, float],
    hop: CycleHop,
    accums: dict[str, _Accum],
) -> None:
    round_num = int(doc.get("round", 0) or 0)
    for cand in doc.get("candidate_scores") or []:
        cand_id = str(cand.get("candidate_id", ""))
        if not cand_id:
            continue
        prompt_state = _coerce_state(cand.get("pipeline_params_override"))
        state_hash = _state_hash(prompt_state)
        if state_hash == _ORIGIN_HASH:
            continue  # the no-op arm anchors others; it is not itself a ranked candidate
        cand_cells = _cell_levels((doc.get("all_candidate_results") or {}).get(cand_id) or [])
        paired = {c: cand_cells[c] for c in cand_cells if c in origin_cells}
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
            acc.orig_by_cell.setdefault(cell, []).append(origin_cells[cell])


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

    anchor, anchor_se, n_cells = paired_diff_posterior(cell_cand, cell_orig)
    # Student-t on the CELL count, not z: the SE is estimated from the same handful of
    # cells it widens (≈7 cells → 2.45, not 1.96). Same rule as the per-round interval
    # (`scoring/selection.py::matched_parent_lift`), so a corpus leader and a round verdict
    # cannot disagree by bracketing the same evidence two ways.
    crit = t_critical(max(n_cells - 1, 1))
    return RankedEdit(
        state_hash=state_hash,
        label=acc.label,
        prompt_state=acc.prompt_state,
        provenance=acc.provenance,
        per_cell_effects=per_cell,
        anchor_effect=anchor,
        ci_lo=anchor - crit * anchor_se,
        ci_hi=anchor + crit * anchor_se,
        n_cells=len(per_cell),
        n_measurements=n_meas,
    )


__all__ = [
    "ArmReplicate",
    "CampaignCells",
    "CampaignOrigin",
    "CellEffect",
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
