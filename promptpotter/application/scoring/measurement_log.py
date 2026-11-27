"""The measurement log and one cell opened; a cell is read from ONE place, the archive, and graded per read."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from promptpotter.application.bench.difficulty import judging_ruler, restamped
from promptpotter.application.datasets.authored import (
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import held_bank
from promptpotter.application.initialization.session import drawn_partition
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples,
    rank_hard_samples,
)
from promptpotter.application.intelligence.rasch import Observation, build_observations
from promptpotter.application.pipeline_resolve import (
    Resolution,
    cycle_config,
    graded_scorer,
    resolved,
)
from promptpotter.application.scoring.closed_rounds import (
    closed_rounds,
    cycle_bank,
    walked_rows,
)
from promptpotter.application.scoring.sample_measurement import interpolate_prompt
from promptpotter.domain.arms import Measuring, Proposed, arm_name
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cells import (
    Cell,
    CellCandidate,
    CellRow,
    CellSpan,
    CellsResponse,
    DatasetItem,
    HardSampleOrder,
    HardSamples,
    HeatmapScope,
    hit_spread,
)
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.paired_reading import MeasurandUnit
from promptpotter.domain.pass_kinds import individual_cells
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import RulerReach, RulerScope
from promptpotter.domain.sample import ArchiveEntry, Bank, FiledAnswer, Sample
from promptpotter.domain.scoring import (
    NO_CELLS,
    CellSheet,
    GradedCell,
    PipelineData,
    SampleStatus,
    Scorer,
    ground_truth_text,
    is_verifier_graded,
)
from promptpotter.domain.search_point import split_rendered_prompt
from promptpotter.infrastructure.run_scope import scoped
from promptpotter.infrastructure.store.archive_queries import (
    OpenAt,
    list_populations,
    load_population,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import STANDING_ROUNDS, line_walks
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    definition_version,
    resolve_dataset,
)
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import Moment, derived, read_line
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, NotFoundError, PayloadInvalidError
from promptpotter.shared.measurement_context import RoleScope

_SPAN_KEYS = frozenset({"step_tokens", "step_timings", "terminal_node", "total_time"})

_PREDICTED_CHARS = 120

ScopeCells = tuple[list[CellCandidate], list[CellRow], str | None]


def _trim(text: object) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= _PREDICTED_CHARS else t[: _PREDICTED_CHARS - 1] + "…"


def _row_cell(cell: GradedCell, *, key: str, position: int) -> CellRow:
    facts = cell.facts
    if facts.answer is None:
        raise KeyError(f"the cell of sample {facts.sample_key} names no archive answer")
    return CellRow(
        position=position,
        answer=facts.answer,
        candidate=key,
        status=sample_status(facts, cell.grade),
        fitness=cell.grade.fitness if cell.scored else None,
        cached=facts.cached,
        predicted=_trim(facts.predicted),
    )


def _span(
    node: str,
    cfg: dict[str, Any],
    pd: PipelineData,
    recorded: Mapping[str, object],
    variables: dict[str, Any],
    output_keys: list[str],
) -> CellSpan:
    usage = pd.step_tokens.get(node)
    prompt, config = split_rendered_prompt(cfg)
    return CellSpan(
        node=node,
        model=(usage.model if usage else None) or cfg.get("model"),
        provider=(usage.provider if usage else None) or cfg.get("provider"),
        input=interpolate_prompt(prompt, variables) if isinstance(prompt, str) else None,
        config=config,
        outputs={k: recorded[k] for k in output_keys if k in recorded},
        seconds=pd.step_timings.get(node),
        input_tokens=usage.input if usage else None,
        output_tokens=usage.output if usage else None,
        cache_read_tokens=usage.cache_read if usage else None,
        cost_usd=usage.cost_usd if usage else None,
        rate_priced_usd=usage.rate_priced_usd if usage else None,
        estimated=usage.estimated if usage else False,
    )


def _assemble(
    described: ArchiveEntry,
    filed: FiledAnswer,
    cell: GradedCell,
    sid: int,
) -> Cell:
    """Spans are READ-TIME assembly: outputs attribute through the outputs filed with the answer."""
    facts = cell.facts
    pd = facts.pipeline
    recorded = pd.wire()
    variables = {
        "id": sid,
        "query": facts.query,
        "ground_truth": facts.ground_truth or None,
        "question": pd.question,
    }
    spans = [
        _span(node, cfg, pd, recorded, variables, list(filed.output_keys.get(node, ())))
        for node, cfg in described.node_configs
    ]
    attributed = {k for s in spans for k in s.outputs} | _SPAN_KEYS
    return Cell(
        answer=filed.answer,
        position=sid,
        dataset_hash=filed.dataset_hash,
        role=filed.role,
        created_at=filed.created_at,
        prompt_fields_id=described.prompt_fields_id,
        query=facts.query,
        ground_truth=None if is_verifier_graded(facts.ground_truth) else facts.ground_truth,
        ground_truth_text=ground_truth_text(facts.ground_truth),
        predicted=facts.predicted,
        status=sample_status(facts, cell.grade),
        fitness=cell.grade.fitness,
        error=facts.error,
        terminal_node=pd.terminal_node,
        seconds=facts.cost_s,
        cached=facts.cached,
        spans=spans,
        other_outputs={k: v for k, v in recorded.items() if k not in attributed},
    )


def open_cell(stores: Stores, answer: str) -> Cell:
    """Read off the answer alone: its position is its own bank's, its grade the grading it was filed
    under, so no dataset label (which a re-cut may have moved) is resolved."""
    filed = stores.archive.answer(answer)
    if filed is None:
        raise NotFoundError(f"Answer '{answer}' not found")
    described = stores.archive.entry(filed.config_key, filed.dataset_hash)
    if described is None:
        raise NotFoundError(f"Answer '{answer}' is filed under a configuration no index describes")
    bank = stores.tenant_datasets.bank(filed.dataset_hash)
    if bank is None:
        raise NotFoundError(f"Answer '{answer}' is filed under a bank no longer held")
    position = bank.position_of[filed.cell.sample_key]
    filed = stores.archive.whole(filed)
    scorer = graded_scorer(filed.grading, verifier_graded=bank.verifier_graded)
    return _assemble(described, filed, scorer.grade(filed.cell), position)


def campaign_scope_cycle(stores: Stores, campaign_id: str) -> CycleHop:
    """One cycle, the line's holder: a ruler is a cycle's, so a sibling's cell lies on no scale this read holds."""
    return stores.campaigns.live_cycle(stores.campaigns.campaign(campaign_id).root_hop)


def _scope_cycle(
    stores: Stores, scope: HeatmapScope, campaign_id: str | None, cycle_id: str | None
) -> CycleHop | None:
    if scope == "dataset":
        return None
    if scope == "campaign":
        if not campaign_id:
            raise BadRequestError("scope=campaign requires campaign_id")
        return campaign_scope_cycle(stores, campaign_id)
    if not campaign_id or not cycle_id:
        raise BadRequestError("scope=cycle requires campaign_id and cycle_id")
    return CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)


def _dataset_bank(stores: Stores, dataset: ResolvedDataset) -> Bank:
    """What the label holds now, never fetched by a read: a bank-less dataset holds no rows."""
    try:
        held = held_bank(stores, dataset)
    except (ValueError, OSError, ImportError) as exc:
        raise PayloadInvalidError(
            f"Dataset {dataset.name!r} declares rows that could not be read: {exc}",
            code="dataset_panel_invalid",
            details={"dataset_name": dataset.name},
        ) from exc
    return Bank.of(()) if held is None else held


def _artifact_scope_store(
    stores: Stores,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
) -> tuple[Stores, str | None, str | None]:
    if not descend:
        return stores, campaign_id, cycle_id
    if not campaign_id or not cycle_id:
        raise BadRequestError("descend requires campaign_id and cycle_id (the root hop)")
    leaf_store, leaf = resolve_cycle_path(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *descend)
    )
    return leaf_store, leaf.campaign_id, leaf.cycle_id


@dataclass(frozen=True)
class CellsScope:
    """One log's scope, resolved: ``art_store`` is the leaf's, a sandbox's at depth."""

    stores: Stores
    art_store: Stores
    name: str
    scope: HeatmapScope
    hop: CycleHop | None
    at: int | None

    def version(self) -> tuple[object, ...]:
        """What the log reads, at its writers' stamps: the stores it reads (a fork's chain is the
        leaf's store), and the authored definition, which no writer stamps."""
        return (
            self.name,
            self.scope,
            self.hop,
            self.at,
            definition_version(self.stores, self.name),
            *self.stores.stamps(),
            *self.art_store.stamps(),
        )


def cells_scope(
    stores: Stores,
    name: str,
    *,
    scope: HeatmapScope,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
    at: int | None,
) -> CellsScope:
    art_store, art_campaign, art_cycle = _artifact_scope_store(
        stores, campaign_id, cycle_id, descend
    )
    if at is not None and scope != "cycle":
        raise BadRequestError("at replays one cycle's ledger: it needs scope=cycle and its ids")
    hop = _scope_cycle(art_store, scope, art_campaign, art_cycle)
    return CellsScope(stores, art_store, name, scope, hop, at)


def _drawn_rows(cells: CellsScope, drawn: PartitionRecord, slots: list[int]) -> dict[int, Sample]:
    """A cycle reads the bank it DREW, by hash, and only the rows a page shows."""
    rows = cells.stores.tenant_datasets.rows(drawn.dataset_hash, slots)
    if rows is None:
        raise PayloadInvalidError(
            f"This campaign drew its samples from a bank no longer held ({drawn.dataset_hash}).",
            code="campaign_roster_unreadable",
            details={"dataset_name": cells.name, "dataset_hash": drawn.dataset_hash},
        )
    return rows


def cycle_hard_samples(
    stores: Stores, hop: CycleHop, rounds: list[RoundResult], config: CampaignConfig
) -> HardSamples:
    """A cycle's scale, folded off its ledger: its closed rounds read on the scale its last close's
    level series was read on, re-fit. It resolves nothing: the caller hands the closed rounds and
    config it already holds."""
    if not cycle_dir_for(stores.base_dir, hop).exists():
        raise NotFoundError(f"Cycle '{hop.campaign_id}/{hop.cycle_id}' not found")
    knobs = config.optimization
    bank = cycle_bank(stores, hop) if rounds else Bank.of(())
    series = stores.campaigns.standing_rounds(hop, moment=None).level_series
    stamp = None if series is None else series.stamp
    judging = (
        None
        if stamp is None or stamp.cold
        else restamped(
            stores.ruler,
            stamp,
            n_min=knobs.elimination_n_min,
            enable_2pl=knobs.enable_2pl_graduation,
        )
    )
    cells = NO_CELLS
    for rr in rounds:
        cells = cells.merged(rr.results)
    frontier = None if judging is None else judging.frontier(cells)
    return build_hard_samples(
        build_observations(rounds),
        None if judging is None else judging.ruler,
        frontier=None if frontier is None else (frontier.theta, frontier.se),
        slots=bank.position_of,
        cycle_id=hop.cycle_id,
    )


def _dataset_view(stores: Stores, name: str, bank: Bank) -> HardSamples:
    scorer = Resolution(_dataset_campaign_config(stores, name), bank.verifier_graded).scorer
    knobs = _dataset_campaign_config(stores, name).optimization
    n_min, enable_2pl = knobs.elimination_n_min, knobs.enable_2pl_graduation

    scope = RulerScope(scorer=scorer.id, dataset=name)
    reach = RulerReach(version=stores.ruler.version(), own=None)

    def build() -> HardSamples:
        observations = [
            Observation(address, key, y)
            for address, key, y in stores.ruler.rows(scope, reach)
            if key in bank.position_of
        ]
        # No election reads this scale, so it judges no one.
        judging = judging_ruler(
            stores.ruler,
            scope,
            reach,
            (),
            objective_id=scorer.id,
            n_min=n_min,
            enable_2pl=enable_2pl,
        )
        return build_hard_samples(
            observations,
            judging.ruler,
            frontier=None,
            slots=bank.position_of,
            cycle_id=None,
        )

    key = ("dataset_hard_samples", stores.ruler.path, scope, bank.dataset_hash, n_min, enable_2pl)
    return derived(key, sig=reach.version, compute=build)


def _trim_unmeasured(
    order: list[int],
    measured: set[int],
    cap: int | None,
) -> list[int]:
    if cap is None:
        return list(order)
    kept_unmeasured = 0
    out: list[int] = []
    for sid in order:
        if sid in measured:
            out.append(sid)
        elif kept_unmeasured < cap:
            out.append(sid)
            kept_unmeasured += 1
    return out


@dataclass(frozen=True)
class _LeaderboardPage:
    shown: dict[int, Sample]
    view: HardSamples
    candidates: list[CellCandidate]
    cells: list[CellRow]
    headline: str | None
    order: HardSampleOrder
    ranked: list[int]
    """Page positions in served order; index + 1 IS each row's ``hard_sample_rank``."""
    fitness_unit: MeasurandUnit


def _selected(
    view: HardSamples, members: Collection[int], max_unmeasured: int | None, limit: int
) -> list[int]:
    in_view = [view.positions[k] for k in view.ranked_keys if view.positions[k] in members]
    selection = in_view + sorted(set(members).difference(in_view))
    return _trim_unmeasured(selection, set(in_view), max_unmeasured)[:limit]


def _leaderboard_page(
    cells: CellsScope, *, limit: int, max_unmeasured: int | None, order: HardSampleOrder | None
) -> _LeaderboardPage:
    """*Measured* is a GRADED cell in this scope, never a δ entry, which forks inherit."""
    art_store, hop = cells.art_store, cells.hop
    if hop is None:
        bank = _dataset_bank(cells.stores, resolve_dataset(cells.stores, cells.name))
        view = _dataset_view(art_store, cells.name, bank)
        selected = _selected(view, range(len(bank.samples)), max_unmeasured, limit)
        shown = {sid: bank.samples[sid] for sid in selected}
        wanted = {shown[sid].key: sid for sid in selected}
        graded_under = Resolution(
            _dataset_campaign_config(art_store, cells.name), bank.verifier_graded
        ).scorer
        candidates, rows, headline = _dataset_cells(art_store, graded_under, bank, wanted)
    else:
        drawn = drawn_partition(art_store.campaigns, hop)
        at = art_store.campaigns.campaign_cycle(hop)
        graded_under = resolved(art_store, at.campaign.root_cycle).scorer
        rounds = closed_rounds(art_store, hop, graded_under, moment=None)
        view = cycle_hard_samples(art_store, hop, rounds, cycle_config(art_store, at).config)
        selected = _selected(view, drawn.bank_ids, max_unmeasured, limit)
        shown = _drawn_rows(cells, drawn, selected)
        wanted = {shown[sid].key: sid for sid in selected}
        candidates, rows, headline = _cycle_cells(art_store, hop, graded_under, wanted, cells.at)

    sorted_by, ranked = rank_hard_samples(
        view,
        wanted,
        measured={shown[c.position].key for c in rows if c.fitness is not None},
        order=order or _dataset_campaign_config(cells.stores, cells.name).hard_sample_order,
    )
    return _LeaderboardPage(
        shown=shown,
        view=view,
        candidates=candidates,
        cells=rows,
        headline=headline,
        order=sorted_by,
        ranked=[wanted[key] for key in ranked],
        fitness_unit=graded_under.fitness_unit,
    )


def _cycle_cells(
    stores: Stores,
    hop: CycleHop,
    scorer: Scorer,
    wanted: Mapping[str, int],
    at: int | None,
) -> ScopeCells:
    """A column is an INDIVIDUAL, named by the arm it first was, so one a later round carried is
    served once. The headline is the latest election's `ended_on`, a dataset scope's every column."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    moment = None if at is None else Moment(CycleLayout(cycle_dir).ledger, at)
    chain = stores.campaigns.chain(hop, moment=moment)
    walks = line_walks(chain)
    standing = read_line(chain, STANDING_ROUNDS)
    elected = max(standing.elections, default=None)
    ended_on = None if elected is None else standing.elections[elected].ended_on
    candidates: dict[str, CellCandidate] = {}
    cells: list[CellRow] = []
    headline = None
    for (round_no, arm_key), arm in standing.arms.items():
        state = arm.state
        if state.invalid:
            continue
        label, individual_id = arm_name(arm_key, state, round_no).label, state.individual.id
        key = f"{hop.cycle_id}/{individual_id}"
        if arm_key == ended_on:
            headline = key
        live = isinstance(state, Proposed | Measuring)
        if (column := candidates.get(individual_id)) is not None:
            candidates[individual_id] = column.model_copy(update={"live": column.live or live})
            continue
        candidates[individual_id] = CellCandidate(
            key=key,
            label=str(label),
            candidate_id=individual_id,
            round=round_no,
            cycle_id=hop.cycle_id,
            live=live,
        )
        measured = individual_cells(walks, individual_id, RoleScope.REPORT)
        sheet = walked_rows(stores, measured, scorer).standing()
        cells.extend(
            _row_cell(cell, key=key, position=wanted[cell.key])
            for cell in sheet
            if cell.key in wanted
        )
    return list(candidates.values()), cells, headline


def _dataset_cells(
    stores: Stores, scorer: Scorer, bank: Bank, wanted: Mapping[str, int]
) -> ScopeCells:
    """A column is a CONFIGURATION's population: the archive names no individual or walk, so no ``RoleScope``."""
    archive = stores.archive
    sigs = archive.cell_signatures()
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    with scoped(memory=OpenAt()):
        for entry in list_populations(stores, bank):
            key = entry.config_key

            def grade(entry: ArchiveEntry = entry) -> CellSheet:
                return scorer.sheet(answer.cell for answer in load_population(stores, entry, bank))

            sheet = derived(
                ("graded_population", archive.base_dir, key, bank.dataset_hash, scorer.id),
                sig=archive.signature(entry, sigs),
                compute=grade,
            )
            if not sheet:
                continue
            candidates.append(
                CellCandidate(
                    key=key,
                    label=f"{entry.name} {key[:8]}",
                    created_at=entry.created_at,
                )
            )
            cells.extend(
                _row_cell(cell, key=key, position=wanted[cell.key])
                for cell in sheet
                if cell.key in wanted
            )
    return candidates, cells, None


def _dataset_campaign_config(stores: Stores, name: str) -> CampaignConfig:
    return load_dataset_campaign_config(resolve_dataset(stores, name))


def _graded(cells: list[CellRow]) -> list[float]:
    return [c.fitness for c in cells if c.fitness is not None]


def measurement_log(
    cells: CellsScope,
    *,
    limit: int,
    max_unmeasured: int | None,
    order: HardSampleOrder | None,
    candidate_id: str | None,
    round: int | None,
    status: SampleStatus | None,
) -> CellsResponse:
    """A cycle scope's ``at`` replays its ledger to that line, under the ranking as it stands NOW."""
    page = _leaderboard_page(cells, limit=limit, max_unmeasured=max_unmeasured, order=order)
    shown = page.shown

    round_of = {c.key: c.round for c in page.candidates}
    individual_of = {c.key: c.candidate_id for c in page.candidates}
    kept = [
        c
        for c in page.cells
        if (candidate_id is None or individual_of.get(c.candidate) == candidate_id)
        and (round is None or round_of.get(c.candidate) == round)
        and (status is None or c.status == status)
    ]
    rank_of = {sid: i for i, sid in enumerate(page.ranked)}
    cand_pos = {c.key: i for i, c in enumerate(page.candidates)}
    # Stable, so the walk order inside a candidate survives.
    kept.sort(key=lambda c: cand_pos[c.candidate])
    by_sample: dict[int, list[CellRow]] = {}
    for c in kept:
        by_sample.setdefault(c.position, []).append(c)

    filtered = candidate_id is not None or round is not None or status is not None
    ranked = [sid for sid in page.ranked if sid in by_sample] if filtered else page.ranked
    held = {c.candidate for c in kept}
    candidates = [c for c in page.candidates if c.key in held] if filtered else page.candidates
    # A filter names the cells its headline reads; unfiltered, the cycle's is its selection's.
    spread_of = None if filtered else page.headline

    def _item(sid: int) -> DatasetItem:
        rows = by_sample.get(sid, [])
        graded = _graded(rows)
        headline = _graded([c for c in rows if spread_of in (None, c.candidate)])
        sample = shown[sid]
        on_ruler = page.view.difficulty(sample.key)
        return DatasetItem(
            position=sid,
            sample_key=sample.key,
            query=sample.query,
            ground_truth=sample.ground_truth,
            # The position in the UNFILTERED ranking.
            hard_sample_rank=rank_of[sid] + 1,
            delta_label=on_ruler.label,
            delta=on_ruler.delta,
            delta_se=on_ruler.delta_se,
            pick_score=on_ruler.pick_score,
            n_measured=len(graded),
            mean_fitness=sum(graded) / len(graded) if graded else None,
            hit_spread=hit_spread(headline),
        )

    graded_all = _graded(kept)
    samples = [_item(sid) for sid in ranked]
    spreads = Counter(item.hit_spread for item in samples)
    return CellsResponse(
        name=cells.name,
        scope=cells.scope,
        order=page.order,
        ruler=page.view.ruler,
        fitness_unit=page.fitness_unit,
        samples=samples,
        never_hit=spreads["never"],
        partly_hit=spreads["partly"],
        always_hit=spreads["always"],
        measured_samples=len(samples) - spreads["unmeasured"],
        unmeasured_samples=spreads["unmeasured"],
        candidates=candidates,
        cells=kept,
        total_measurements=len(graded_all),
        mean_fitness=sum(graded_all) / len(graded_all) if graded_all else None,
    )
