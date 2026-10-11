from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from functools import partial
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
    SubjectMember,
    comparability,
    metric_measurand,
    metric_reading,
    order_confound,
    pair_subjects,
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
from promptpotter.application.evidence.head_to_head import (
    HeadToHead,
    HeadToHeadEntry,
    comparison_grader,
    head_to_head,
)
from promptpotter.application.evidence.metric_catalogue import (
    MEASURAND,
    available_channels,
    cell_channels,
    merge_cells,
    resolve_metric,
)
from promptpotter.application.evidence.subjects import (
    ScenarioReading,
    SubjectMask,
    SubjectReading,
    SubjectSpec,
    WinnerChainPoint,
    authorship_of,
    parse_subject,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.scenario import scenario_spine
from promptpotter.application.scoring.closed_rounds import (
    campaign_scorer,
    cycle_instrument,
    walked_rows,
)
from promptpotter.application.scoring.formula.compiler import ScoringFormulaError
from promptpotter.application.scoring.paired import MemberRows, as_family
from promptpotter.domain.candidate_diff import build_candidate_flat, flatten_sp_summary
from promptpotter.domain.cycle_listing import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, CyclePath
from promptpotter.domain.paired_reading import (
    ArmPointer,
    Measurand,
    MemberAddress,
    PairedReading,
)
from promptpotter.domain.results import IndividualWalk, individual_cells
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.scoring import CellSheet, GradedCell, WalkedCell
from promptpotter.domain.spend import SpendRollup
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.cycle_index import read_cycle_index
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    ArmWalk,
    StandingRounds,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
    scan_ledger_walks,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    campaign_cycles_dir,
)
from promptpotter.infrastructure.store.read_model import derived, file_sig
from promptpotter.infrastructure.store.stores import descend_store
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import BadRequestError, NotFoundError
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.measurement_context import RoleScope
from promptpotter.shared.statistics import sample_sd

if TYPE_CHECKING:
    from promptpotter.application.scoring.formula.compiler import CompiledExpression
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.scoring import Scorer
    from promptpotter.infrastructure.store.stores import Stores


class EffectProvenance(StrictModel):
    """Where one occurrence of an edit was measured on disk."""

    campaign_id: str
    cycle_id: str
    round: int
    candidate_id: str


class RankedEdit(StrictModel):
    """One SEARCHPOINT measured against its own campaign's origin — a prompt edit, a node-config
    edit, an optimizer-prompt edit on the recursion; the arithmetic does not care which.

    **The identity is ``sp_hash``**, the archive join already stamped on every candidate row, so a
    prose-only edit ranks like any other. Keying the sparse ``pipeline_overlay`` instead cannot see
    one at all: that overlay is empty for a prompt-only candidate, which is the whole of what L1
    proposes on a campaign optimizing prompts.

    **Rows POOL WITHIN ONE CAMPAIGN and never across two.** A
    searchpoint is read through a delta against its parent, and two campaigns share no parent to
    take one against — so a cross-campaign pool is a number lined up on nothing, and that is a
    claim withdrawn rather than a capability lost. Do not re-add it as an improvement. Within one
    campaign the pool is real: individuals sharing a searchpoint share its cells, which are filed
    by content, so they are one member of the pair.
    """

    sp_hash: str
    campaign_id: str
    label: str
    provenance: list[EffectProvenance]
    reading: PairedReading = Field(
        description="The searchpoint over its campaign's origin under the selected metric, on the "
        "cells both scored. Its headline's `family` is Holm across the edits ranked here. An "
        "edit whose reading is not `read` is listed after them, unranked.",
    )


class EditSpread(StrictModel):
    """How far apart the ranked edits actually are — the SD of their lifts over the origin.

    **Not a signal-to-noise ratio, and deliberately not one.** The noise half would be repeated
    readings of ONE (edit, cell), and the instrument cannot produce them: measurements are
    content-addressed, so a second ask replays the first answer and its spread is zero by
    construction, which reads as a perfect instrument. A replicate ARM is the honest noise reading
    (see :class:`ArmReplicate`); measuring one candidate HARDER is ``verify``'s job. ``None`` when
    fewer than two edits have been measured.
    """

    edit_effect_sd: float | None = None
    n_edits: int = 0


class ConfigKeys(StrictModel):
    """Every configured key across the subjects that carry a config, in exactly one band."""

    differs: list[str] = Field(description="Every subject configures it, and not all alike")
    one_sided: list[str] = Field(
        description="Not every subject configures it: a different pipeline, not a disagreement"
    )
    same: list[str] = Field(description="Every subject configures it alike")

    @classmethod
    def of(cls, configs: list[dict[str, str]]) -> ConfigKeys:
        bands = cls(differs=[], one_sided=[], same=[])
        for key in sorted({k for config in configs for k in config}):
            if any(key not in config for config in configs):
                bands.one_sided.append(key)
            elif len({config[key] for config in configs}) > 1:
                bands.differs.append(key)
            else:
                bands.same.append(key)
        return bands


class Evidence(StrictModel):
    """The whole read for one selection of subjects — recomputed on every fetch."""

    generated_at: str
    scorer_id: str
    subjects: list[SubjectReading]
    comparability: Comparability
    head_to_head: HeadToHead | None = None
    metric: MetricReading
    unread_subjects: list[str] = Field(default_factory=list)
    config_keys: ConfigKeys | None = None
    factors: list[FactorReading] = Field(default_factory=list)
    grid: FactorGridReading | None = None
    replicates: list[ArmReplicate] = Field(default_factory=list)
    variance: EvidenceVariance | None = None
    power: EvidencePower | None = None
    order_confound: OrderConfound | None = None
    ranking_computed: bool = False
    edits: list[RankedEdit] = Field(default_factory=list)
    spread: EditSpread = Field(default_factory=EditSpread)


class _Accum:
    def __init__(self, arm: ArmPointer) -> None:
        self.arm = arm
        self.provenance: list[EffectProvenance] = []
        self.cells: dict[str, GradedCell] = {}


def _dataset_name(manifest: dict[str, Any]) -> str:
    block_raw = manifest.get("campaign_config")
    block = block_raw if isinstance(block_raw, dict) else manifest
    return str(block.get("dataset_name", ""))


def _dataset_of(campaign_dir: Path) -> str:
    return _dataset_name(read_json_tolerant(CampaignLayout(campaign_dir).manifest, {}))


def campaigns_on_dataset(stores: Stores, dataset_name: str) -> list[str]:
    """Read off each manifest: a directory-name match skips an A/B arm, a fork, a rename."""
    return [
        child.name
        for child in stores.campaigns.iter_campaign_dirs()
        if _dataset_of(child) == dataset_name
    ]


def _named(stores: Stores, spec: SubjectSpec) -> SubjectSpec:
    if spec.inside:
        return spec
    matches = stores.campaigns.match_campaign_ids(spec.campaign_id)
    if len(matches) > 1:
        raise ValueError(
            f"{spec.campaign_id!r} in subject {spec.key!r} matches {len(matches)} campaigns: "
            f"{', '.join(matches[:5])}. Pass the full id."
        )
    return spec._replace(campaign_id=matches[0]) if matches else spec


def select_evidence(
    stores: Stores,
    *,
    subjects: Sequence[str],
    dataset: str = "",
    grid: str = "",
    include_ranking: bool = False,
    include_winner_chain: bool = False,
    include_config: bool = False,
    metric: str = MEASURAND,
) -> Evidence:
    specs = [_named(stores, parse_subject(raw)) for raw in subjects]
    if dataset:
        named = {s.key for s in specs}
        specs += [
            spec
            for cid in campaigns_on_dataset(stores, dataset)
            if (spec := SubjectSpec("campaign", cid)).key not in named
        ]
    axes = [a.strip() for a in grid.split(",") if a.strip()]
    if grid and len(axes) != 2:
        raise ValueError(
            f"A grid takes exactly two factor names separated by a comma, got {grid!r}. It has two "
            "axes at any number of factors — the rest are marginalised into the cells."
        )
    return subject_evidence(
        stores,
        specs,
        include_ranking=include_ranking,
        include_winner_chain=include_winner_chain,
        include_config=include_config,
        metric=metric,
        grid=(axes[0], axes[1]) if axes else None,
    )


class _ChainPoint(NamedTuple):
    round: int
    candidate_id: str
    label: str
    sheet: CellSheet
    arm: ArmWalk


class _Cycle(NamedTuple):
    stores: Stores
    walked: StandingRounds
    walks: list[IndividualWalk[WalkedCell]]
    scorer: Scorer

    def sheet(self, individual_id: str) -> CellSheet:
        measured = individual_cells(self.walks, individual_id, RoleScope.REPORT)
        return walked_rows(self.stores, measured, self.scorer).standing()

    def point(
        self, round_num: int, *, label: str = "", candidate_id: str = ""
    ) -> _ChainPoint | None:
        for (rnd, _), arm in self.walked.arms.items():
            found, named = arm.candidate_id, arm.label
            if rnd != round_num or not found:
                continue
            if (label and named != label) or (candidate_id and found != candidate_id):
                continue
            return _ChainPoint(
                round=round_num,
                candidate_id=found,
                label=named or found,
                sheet=self.sheet(found),
                arm=arm,
            )
        return None


def _open_cycle(stores: Stores, campaign_id: str, cycle_dir: Path) -> _Cycle | None:
    graded_under = campaign_scorer(stores, campaign_id)
    if graded_under is None:
        return None
    chain = list(ledger_chain(CycleDir(cycle_dir)))
    return _Cycle(stores, scan_standing_rounds(chain), scan_ledger_walks(chain), graded_under)


class _Head(NamedTuple):
    cycle_dir: Path
    label: str
    dataset_name: str
    created_at: str
    point: _ChainPoint
    authorship: str
    human_intervened: bool
    scenario: ScenarioReading | None = None
    chain: list[_ChainPoint] | None = None


class _CycleFacts(NamedTuple):
    arm_id: str | None
    instrument_id: str
    ability: AbilityReading | None
    spend_usd: float | None
    rounds_scored: int
    spend_to_round: dict[str, float]


class _SubjectRead(NamedTuple):
    """Memoised across fetches (`derived`): never mutate one."""

    head: _Head
    channels: dict[str, dict[str, float]]
    cycle: _CycleFacts


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
    wanted: dict[str, SubjectSpec] = {s.key: s for s in specs}
    located = {key: at for key, spec in wanted.items() if (at := _at(stores, spec)) is not None}
    answering = {
        key: (leaf_stores, campaign_dir)
        for key, (leaf_stores, campaign_dir) in located.items()
        if derived(
            ("evidence_answers", campaign_dir, key),
            sig=_files_read(leaf_stores, wanted[key], campaign_dir),
            compute=partial(_answers, leaf_stores, wanted[key], campaign_dir),
        )
    }
    owners: dict[tuple[tuple[tuple[str, str], ...], str], tuple[Stores, Campaign]] = {}
    for key, (leaf_stores, _) in answering.items():
        spec = wanted[key]
        campaign = leaf_stores.campaigns.load_campaign(spec.campaign_id)
        if campaign is not None:
            inside = tuple((h.campaign_id, h.cycle_id) for h in spec.inside)
            owners.setdefault((inside, spec.campaign_id), (leaf_stores, campaign))
    grader = (
        comparison_grader(sorted(owners.values(), key=lambda o: o[1].created_at))
        if owners
        else None
    )
    reads: dict[str, _SubjectRead] = {}
    leaves: dict[str, Stores] = {}
    for key, (leaf_stores, campaign_dir) in answering.items():
        spec = wanted[key]
        read = partial(_read_subject, leaf_stores, spec, campaign_dir)
        # A lens grades through the campaign's resolved config, which no signature here covers.
        found = (
            read()
            if spec.lens
            else derived(
                ("evidence_subject", campaign_dir, spec.key),
                sig=_files_read(leaf_stores, spec, campaign_dir),
                compute=read,
            )
        )
        if found is None:
            continue
        reads[key] = found
        leaves[key] = leaf_stores
    heads = {key: found.head for key, found in reads.items()}
    channels_by_subject = {key: found.channels for key, found in reads.items()}

    if grader is None or not heads:
        raise ValueError(
            f"None of {', '.join(sorted(wanted)) or 'the subjects named'} has scored rows to read. "
            "A campaign answers here once its origin has run, a course once its branch has, a "
            "candidate once it has been measured; one that does not exist answers never."
        )
    available = available_channels(channels_by_subject)
    spec_metric, compiled = resolve_metric(metric, available)

    rows = [
        _reading_row(
            leaves[key],
            wanted[key],
            head,
            compiled,
            channels_by_subject[key],
            reads[key].cycle,
            include_winner_chain=include_winner_chain,
            include_config=include_config,
        )
        for key, head in heads.items()
    ]
    rows.sort(key=lambda r: (r.created_at, r.round, r.key))
    rows = stamp_comparable(rows)
    # Not behind `include_config`: that flag gates serving the map, and the factors need levels on every read.
    levels = levels_by_subject(rows, {k: _config_of(h.point) for k, h in heads.items()})
    rows = [r.model_copy(update={"levels": levels[r.key]}) for r in rows]

    members = {key: _member(wanted[key], found.head, found.cycle) for key, found in reads.items()}
    measurand = metric_measurand(spec_metric, grader.scorer.id)
    unranked: list[RankedEdit] = []
    if include_ranking:
        for row in (r for r in rows if r.kind == "campaign"):
            head = heads[row.key]
            report = head.point.arm.report
            anchor = "" if report is None else report.sp_hash
            cycle = _open_cycle(leaves[row.key], row.campaign_id, head.cycle_dir)
            if cycle is not None:
                unranked += _campaign_edits(cycle, members[row.key], measurand, anchor_hash=anchor)

    edits = _ranked(unranked, f"edits:{measurand.key}")
    scored = {r.key: r.values for r in rows if r.values}
    reading = metric_reading(spec_metric, rows, available, members=members, measurand=measurand)
    return Evidence(
        generated_at=utcnow_iso(),
        scorer_id=grader.scorer.id,
        subjects=rows,
        comparability=comparability(rows),
        head_to_head=head_to_head(
            [HeadToHeadEntry(r, leaves[r.key]) for r in _campaign_channels(rows)], grader, wanted
        ),
        metric=reading,
        unread_subjects=sorted(set(wanted) - set(heads)),
        config_keys=(
            ConfigKeys.of([r.config for r in rows if r.config is not None])
            if include_config
            else None
        ),
        factors=factors(rows, levels, spec_metric),
        grid=grid_reading(rows, levels, spec_metric, grid) if grid else None,
        replicates=replicates(rows),
        variance=(decomposition := variance(scored)),
        power=power(decomposition, rows),
        order_confound=order_confound(rows),
        ranking_computed=include_ranking,
        edits=edits,
        spread=_edit_spread(edits),
    )


def _campaign_channels(rows: list[SubjectReading]) -> list[SubjectReading]:
    """One row per campaign: each reads that campaign's result, so a second pairs it with itself."""
    chosen: dict[tuple[str, ...], SubjectReading] = {}
    for r in rows:
        if r.kind == "candidate" or r.mask is not None:
            continue
        ident = (*(f"{h.campaign_id}/{h.cycle_id}" for h in r.inside), r.campaign_id)
        if ident not in chosen or (r.kind == "campaign" and chosen[ident].kind != "campaign"):
            chosen[ident] = r
    picked = {r.key for r in chosen.values()}
    return [r for r in rows if r.key in picked]


def _at(stores: Stores, spec: SubjectSpec) -> tuple[Stores, Path] | None:
    """`None`, never a raise: one mistyped id or deleted sandbox must not fail every other subject."""
    try:
        leaf = descend_store(stores, spec.inside)
    except (BadRequestError, NotFoundError):
        return None
    campaign_dir = next(
        (d for d in leaf.campaigns.iter_campaign_dirs() if d.name == spec.campaign_id), None
    )
    return None if campaign_dir is None else (leaf, campaign_dir)


def _files_read(stores: Stores, spec: SubjectSpec, campaign_dir: Path) -> Hashable:
    """A fork's inherited prefix is unsigned on purpose: nothing before a cut is rewritten."""
    manifest = CampaignLayout(campaign_dir).manifest
    cycle_id = spec.cycle_id or str(read_json_tolerant(manifest, {}).get("root_cycle_id", ""))
    layout = CycleLayout(campaign_cycles_dir(campaign_dir) / cycle_id)
    signed = (manifest, layout.ledger)
    return (stores.archive.signature(), *(file_sig(path) for path in signed))


def _answers(stores: Stores, spec: SubjectSpec, campaign_dir: Path) -> bool:
    head = _resolve_head(stores, spec, campaign_dir)
    return head is not None and bool(head.point.sheet)


def _read_subject(stores: Stores, spec: SubjectSpec, campaign_dir: Path) -> _SubjectRead | None:
    head = _resolve_head(stores, spec, campaign_dir)
    if head is None:
        return None
    channels = cell_channels(head.point.sheet)
    if not channels:
        return None
    return _SubjectRead(head, channels, _cycle_facts(head.cycle_dir, head.dataset_name))


def _member(spec: SubjectSpec, head: _Head, cycle: _CycleFacts) -> SubjectMember:
    hop = CycleHop(campaign_id=spec.campaign_id, cycle_id=head.cycle_dir.name)
    point = head.point
    arm = ArmPointer(round=point.round, label=point.label, candidate_id=point.candidate_id)
    return SubjectMember(
        _member_rows((*spec.inside, hop), arm, point.sheet, cycle.instrument_id),
        masked=spec.samples is not None,
    )


def _member_rows(
    path: CyclePath, arm: ArmPointer, sheet: CellSheet, instrument_id: str
) -> MemberRows:
    return MemberRows(
        address=MemberAddress(
            path=path,
            individual_id=arm.candidate_id,
            arm=arm,
            pass_role=None,
        ),
        sheet=sheet,
        bought=0,
        cut=False,
        scope=RoleScope.REPORT,
        instrument_id=instrument_id,
        dataset_hash=None,
        cell_set_id=None,
    )


def _resolve_head(stores: Stores, spec: SubjectSpec, campaign_dir: Path) -> _Head | None:
    manifest = read_json_tolerant(CampaignLayout(campaign_dir).manifest, {})
    dataset_name = _dataset_name(manifest)
    if spec.kind == "campaign":
        # The root cycle by the manifest, never a directory walk: a fork sits beside it as `cycle_x_fork_y`.
        cycle_dir = campaign_cycles_dir(campaign_dir) / str(manifest.get("root_cycle_id", ""))
        cycle = _open_cycle(stores, spec.campaign_id, cycle_dir)
        origin = cycle.point(0) if cycle is not None else None
        if origin is None:
            return None
        c0 = _masked(origin, spec.samples)
        c0_index = read_cycle_index(cycle_dir)
        return _Head(
            cycle_dir=cycle_dir,
            label=spec.campaign_id,
            dataset_name=dataset_name,
            created_at=str(manifest.get("created_at", "")),
            point=c0,
            authorship=_authorship(cycle_dir, c0, c0_index),
            human_intervened=c0_index is not None and c0_index.human_intervened,
        )

    cycle_dir = campaign_cycles_dir(campaign_dir) / spec.cycle_id
    cycle = _open_cycle(stores, spec.campaign_id, cycle_dir)
    if cycle is None:
        return None
    index = read_cycle_index(cycle_dir)
    scenario, chain = (None, None)
    if spec.lens:
        resolved = _scenario(stores, spec, cycle)
        if resolved is None:
            return None
        scenario, chain = resolved
        point: _ChainPoint | None = chain[-1]
    elif spec.kind == "course":
        stands = cycle.walked.standing
        picked = None if stands is None else stands.selection
        point = cycle.point(0) if picked is None else cycle.point(picked.round, label=picked.label)
    else:
        point = _candidate_point(cycle, spec.candidate_id)
    if point is None:
        return None
    masked = _masked(point, spec.samples)
    return _Head(
        cycle_dir=cycle_dir,
        label=spec.cycle_id if spec.kind == "course" else point.label,
        dataset_name=dataset_name,
        created_at="" if index is None else index.created_at,
        point=masked,
        authorship=_authorship(cycle_dir, masked, index),
        human_intervened=index is not None and index.human_intervened,
        scenario=scenario,
        chain=chain,
    )


def _authorship(cycle_dir: Path, point: _ChainPoint, index: CycleIndex | None) -> str:
    source = next(
        (
            c.lineage.source
            for c in scan_standing_rounds(ledger_chain(CycleDir(cycle_dir))).candidates()
            if c.candidate_id == point.candidate_id
        ),
        "",
    )
    fork = None if index is None else index.fork
    return authorship_of(source, "" if fork is None else fork.issued_by)


def _masked(point: _ChainPoint, samples: frozenset[int] | None) -> _ChainPoint:
    if samples is None:
        return point
    return point._replace(sheet=point.sheet.where(lambda cell: cell.sample_id in samples))


def _scenario(
    stores: Stores, spec: SubjectSpec, cycle: _Cycle
) -> tuple[ScenarioReading, list[_ChainPoint]] | None:
    try:
        record = load_mask_record(stores, spec.campaign_id, spec.samples, lens=spec.lens)
    except ScoringFormulaError as exc:
        raise ValueError(f"The lens on {spec.key!r} cannot grade this branch: {exc}") from exc
    recorded = next((c for c in record.cycles if c.cycle_id == spec.cycle_id), None)
    if recorded is None:
        return None
    steps = scenario_spine(recorded)
    points = [p for s in steps if (p := cycle.point(s.round, candidate_id=s.candidate_id))]
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
            invariant_rounds=len(steps) - 1 if parted else len(steps),
            # The cycle's, not the chain's: the chain stops at the parting.
            total_rounds=len(recorded.rounds),
            n_samples_scored=len(_masked(points[-1], spec.samples).sheet),
            note=_SCENARIO_NOTE,
        ),
        [_masked(p, spec.samples) for p in points],
    )


_SCENARIO_NOTE = (
    "This re-ranks the RECORD under the formula you named — it does not re-run the campaign. "
    "θ is not re-fitted and no election is replayed, so the round named here is where the two "
    "readings first part, not a verdict the campaign reached. The chain STOPS at that round: past "
    "it the run would have stood on a parent it never had, and no measurement says what that "
    "produces. `ab` replay is what re-derives an election exactly."
)


def _candidate_point(cycle: _Cycle, candidate_id: str) -> _ChainPoint | None:
    for round_num in sorted({rnd for rnd, _ in cycle.walked.arms}, reverse=True):
        point = cycle.point(round_num, candidate_id=candidate_id)
        if point is not None and point.sheet:
            return point
    return None


def _winner_chain(
    stores: Stores,
    head: _Head,
    spec: SubjectSpec,
    compiled: CompiledExpression,
    channels: dict[str, dict[str, float]],
) -> list[WinnerChainPoint]:
    """Each point reads its OWN cells: subsets move between rounds, so the head's would redraw earlier ones."""
    if head.chain is not None:
        return [_winner_chain_point(p, cell_channels(p.sheet), compiled) for p in head.chain]
    cycle = _open_cycle(stores, spec.campaign_id, head.cycle_dir)
    crowns = cycle.walked.crowns if cycle is not None else {}
    at = head.point.round
    rounds = [r for r in sorted({0, *(r for r in crowns if r < at)}) if r != at]
    points = [
        _masked(p, spec.samples)
        for r in rounds
        if cycle is not None and (p := cycle.point(r, label=crowns.get(r, ""))) is not None
    ]
    return [
        *(_winner_chain_point(p, cell_channels(p.sheet), compiled) for p in points),
        _winner_chain_point(head.point, channels, compiled),
    ]


def _config_of(point: _ChainPoint) -> dict[str, str]:
    # The report restates what the arm announced before its first cell, so either names it.
    searchpoint = point.arm.report or point.arm.announced
    prompt_fields = {} if searchpoint is None else searchpoint.prompt_fields
    # `lineage` is identity, not configuration: it differs between any two candidates.
    fields = {k: v for k, v in prompt_fields.items() if k != "lineage" and v}
    return build_candidate_flat(
        flatten_sp_summary(None if searchpoint is None else searchpoint.resolved_pipeline_params),
        {"prompt_fields": fields},
    )


def _winner_chain_point(
    point: _ChainPoint, channels: dict[str, dict[str, float]], compiled: CompiledExpression
) -> WinnerChainPoint:
    values, _ = _score_cells(compiled, channels)
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
    """Catches the parent error: a missing term and a non-finite result are both unscorable, never 0."""
    values: dict[str, float] = {}
    missed: list[str] = []
    for cell, row_channels in channels.items():
        try:
            values[cell] = compiled.evaluate(dict(row_channels), "this cell")
        except ScoringFormulaError:
            missed.append(cell)
    return (values, sorted(missed))


SIDE_CHANNELS: tuple[str, ...] = ("cost", "latency", "tokens", "target_prompt_chars")


def _cell_means(channels: dict[str, dict[str, float]]) -> dict[str, float]:
    means: dict[str, float] = {}
    for channel in SIDE_CHANNELS:
        carried = [cell[channel] for cell in channels.values() if channel in cell]
        if carried:
            means[channel] = sum(carried) / len(carried)
    return means


def _spend_to_round(by_round: Mapping[int, SpendRollup]) -> dict[str, float]:
    per_round = {rnd: rollup.total_used_usd for rnd, rollup in by_round.items()}
    if not per_round:
        return {}
    running = 0.0
    out: dict[str, float] = {}
    for rnd in range(max(per_round) + 1):
        running += per_round.get(rnd, 0.0)
        out[str(rnd)] = round(running, 6)
    return out


def _cycle_facts(cycle_dir: Path, dataset_name: str) -> _CycleFacts:
    chain = ledger_chain(CycleDir(cycle_dir))
    standing = scan_standing_rounds(chain).rounds
    origin = standing[0].close if 0 in standing else None
    spend_by_round = scan_ledger_spend_by_round(chain)
    hashes = origin.optimizer_state.prompt_hashes if origin else None
    return _CycleFacts(
        # Unstamped is `None`, never a shared hash: `replicates` would pair every unstamped campaign.
        arm_id=stable_hash(dict(hashes)) if hashes else None,
        instrument_id=cycle_instrument(dataset_name, standing),
        ability=origin.ability if origin else None,
        # No bill at all priced nothing (`None`); bills summing to 0 measured it.
        spend_usd=scan_ledger_spend(chain).spend.total_used_usd if spend_by_round else None,
        rounds_scored=max(len(standing) - 1, 0),
        spend_to_round=_spend_to_round(spend_by_round),
    )


def _reading_row(
    stores: Stores,
    spec: SubjectSpec,
    head: _Head,
    compiled: CompiledExpression,
    channels: dict[str, dict[str, float]],
    cycle: _CycleFacts,
    *,
    include_winner_chain: bool,
    include_config: bool,
) -> SubjectReading:
    values, unscorable = _score_cells(compiled, channels)
    arm = head.point.arm
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
        comparable=None,
        comparable_note="",
        mask=(
            SubjectMask(
                lens=spec.lens.spelling if spec.lens else None,
                samples=sorted(spec.samples) if spec.samples else None,
            )
            if spec.lens or spec.samples
            else None
        ),
        scenario=head.scenario,
        winner_chain=(
            _winner_chain(stores, head, spec, compiled, channels)
            if include_winner_chain and spec.kind != "campaign"
            else None
        ),
        config=_config_of(head.point) if include_config else None,
        arm_id=cycle.arm_id,
        authorship=head.authorship,
        human_intervened=head.human_intervened,
        status=arm.state,
        expected_samples=arm.walk_length if arm.report is None else arm.report.expected_samples,
        cached_samples=None if arm.report is None else arm.report.cached_samples,
        instrument_id=cycle.instrument_id,
        ability=cycle.ability,
        round=head.point.round,
        cycle_spend_usd=cycle.spend_usd,
        cycle_rounds_scored=cycle.rounds_scored,
        spend_to_round=cycle.spend_to_round,
        cell_means=_cell_means(channels),
        values=values,
        value=value,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_cells=n_cells,
        unscorable_cells=unscorable,
    )


def _lift(edit: RankedEdit) -> float | None:
    headline = edit.reading.headline
    return None if headline is None else headline.estimate.value


def _edit_spread(rows: list[RankedEdit]) -> EditSpread:
    lifts = [lift for r in rows if (lift := _lift(r)) is not None]
    return EditSpread(edit_effect_sd=sample_sd(lifts), n_edits=len(lifts))


def _ranked(edits: list[RankedEdit], family_id: str) -> list[RankedEdit]:
    def rank(edit: RankedEdit) -> tuple[bool, float, str]:
        lift = _lift(edit)
        return (lift is None, 0.0 if lift is None else -lift, edit.label)

    ordered = sorted(edits, key=rank)
    stamped = as_family([r.reading for r in ordered], family_id)
    return [
        r.model_copy(update={"reading": reading})
        for r, reading in zip(ordered, stamped, strict=True)
    ]


def _campaign_edits(
    cycle: _Cycle, origin: SubjectMember, measurand: Measurand, *, anchor_hash: str
) -> list[RankedEdit]:
    address = origin.rows.address
    hop = address.path[-1]
    accums: dict[str, _Accum] = {}
    pooled: set[str] = set()
    for (round_num, _), arm in cycle.walked.arms.items():
        cand_id = arm.candidate_id
        sp_hash = "" if arm.report is None else arm.report.sp_hash
        if not cand_id or not sp_hash or sp_hash == anchor_hash:
            continue
        acc = accums.setdefault(
            sp_hash,
            _Accum(ArmPointer(round=round_num, label=arm.label, candidate_id=cand_id)),
        )
        acc.provenance.append(
            EffectProvenance(
                campaign_id=hop.campaign_id,
                cycle_id=hop.cycle_id,
                round=round_num,
                candidate_id=cand_id,
            )
        )
        if cand_id in pooled:
            continue
        pooled.add(cand_id)
        acc.cells.update((cell.key, cell) for cell in cycle.sheet(cand_id))
    return [
        RankedEdit(
            sp_hash=sp_hash,
            campaign_id=hop.campaign_id,
            label=acc.arm.label,
            provenance=acc.provenance,
            reading=pair_subjects(
                origin,
                SubjectMember(
                    _member_rows(
                        address.path,
                        acc.arm,
                        CellSheet(cycle.scorer.id, tuple(acc.cells.values())),
                        origin.rows.instrument_id,
                    ),
                    masked=False,
                ),
                measurand,
            ),
        )
        for sp_hash, acc in accums.items()
    ]


__all__ = [
    "ConfigKeys",
    "EditSpread",
    "EffectProvenance",
    "Evidence",
    "RankedEdit",
    "campaigns_on_dataset",
    "subject_evidence",
]
