"""The measurement log and one cell opened; a cell is read from ONE place, the archive, and graded per read."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import judging_ruler
from promptpotter.application.datasets.authored import (
    dataset_scorer,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import held_bank
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples,
    rank_hard_samples,
    read_hard_samples,
)
from promptpotter.application.intelligence.rasch import Observation
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    experiment_outside_run,
)
from promptpotter.application.scoring.closed_rounds import (
    campaign_ruler_scope,
    campaign_scorer,
    walked_rows,
)
from promptpotter.application.scoring.sample_measurement import interpolate_prompt
from promptpotter.domain.cells import (
    Cell,
    CellCandidate,
    CellRow,
    CellSpan,
    CellsResponse,
    DatasetItem,
    HardSamples,
    HeatmapScope,
    hit_spread,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, CyclePath
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.results import (
    Cut,
    HardSampleOrder,
    Measuring,
    Proposed,
    arm_name,
    individual_cells,
)
from promptpotter.domain.ruler import RulerReach, RulerScope
from promptpotter.domain.sample import Bank
from promptpotter.domain.scoring import (
    CellSheet,
    SampleStatus,
    ground_truth_text,
    is_hit,
    is_verifier_graded,
)
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.store.archive_queries import list_populations, load_population
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_walks,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.dataset_access import definition_version, resolve_dataset
from promptpotter.infrastructure.store.layout import CampaignLayout, CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import Moment, derived, file_sig
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, NotFoundError, PayloadInvalidError
from promptpotter.shared.measurement_context import RoleScope

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.bench import PartitionRecord
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import ArchiveEntry, FiledAnswer, Sample
    from promptpotter.domain.scoring import GradedCell, PipelineData, Scorer
    from promptpotter.infrastructure.store.dataset_access import ResolvedDataset


_SPAN_KEYS = frozenset({"step_tokens", "step_timings", "terminal_node", "total_time"})

_PREDICTED_CHARS = 120

ScopeCells = tuple[list[CellCandidate], list[CellRow]]


def _trim(text: object) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= _PREDICTED_CHARS else t[: _PREDICTED_CHARS - 1] + "…"


def _row_cell(cell: GradedCell, *, key: str, slot: int) -> CellRow:
    facts = cell.facts
    if facts.answer is None:
        raise KeyError(f"the cell of sample {facts.sample_key} names no archive answer")
    account = TokenAccount.from_step_tokens(facts.pipeline.step_tokens)
    return CellRow(
        position=slot,
        answer=facts.answer,
        candidate=key,
        status=sample_status(facts, cell.grade),
        fitness=cell.grade.fitness if cell.scored else None,
        cached=facts.cached,
        predicted=_trim(facts.predicted),
        seconds=facts.cost_s,
        input_tokens=account.input if account else None,
        output_tokens=account.output if account else None,
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
    prompt = cfg.get("prompt")
    return CellSpan(
        node=node,
        model=(usage.model if usage else None) or cfg.get("model"),
        provider=(usage.provider if usage else None) or cfg.get("provider"),
        input=interpolate_prompt(prompt, variables) if isinstance(prompt, str) else None,
        config={k: v for k, v in cfg.items() if k != "prompt"},
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
    schema: PipelineSchema | None,
    sid: int,
) -> Cell:
    """Spans are READ-TIME assembly: outputs attribute through the dataset's CURRENT schema."""
    facts = cell.facts
    pd = facts.pipeline
    recorded = pd.wire()
    variables = {
        "id": sid,
        "query": facts.query,
        "ground_truth": facts.ground_truth or None,
        "question": pd.question,
    }
    outputs_of = {n.name: n.output_keys for n in schema.nodes} if schema is not None else {}
    spans = [
        _span(node, cfg, pd, recorded, variables, outputs_of.get(node, []))
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
        spans=spans,
        other_outputs={k: v for k, v in recorded.items() if k not in attributed},
    )


def open_cell(stores: Stores, name: str, answer: str) -> Cell:
    """The slot is the answer's own bank's: *name* may label a re-cut that moved or dropped its row."""
    dataset = resolve_dataset(stores, name)
    filed = stores.archive.answer(answer)
    if filed is None:
        raise NotFoundError(f"Answer '{answer}' not found")
    described = stores.archive.entry(filed.config_key, filed.dataset_hash)
    if described is None:
        raise NotFoundError(f"Answer '{answer}' is filed under a configuration no index describes")
    bank = stores.tenant_datasets.bank(filed.dataset_hash)
    if bank is None:
        raise NotFoundError(f"Answer '{answer}' is filed under a bank no longer held")
    slot = bank.slot_of[filed.cell.sample_key]
    filed = stores.archive.whole(filed)
    declared = dataset_pipeline_declaration(
        stores, dataset, experiment_outside_run(stores, dataset)
    )
    schema = parse_pipeline_response(declared) if declared is not None else None
    return _assemble(
        described, filed, dataset_scorer(dataset.directory).grade(filed.cell), schema, slot
    )


def campaign_scope_cycle(stores: Stores, campaign_id: str) -> CycleHop:
    """One cycle, the line's holder: a ruler is a cycle's, so a sibling's cell lies on no scale this read holds."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise NotFoundError(f"Campaign '{campaign_id}' not found")
    return stores.campaigns.line_holder(campaign.root_hop)


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
        """Stats only: what the log reads, so a client holding it is answered unread."""
        art = self.art_store
        read = (
            self.name,
            self.scope,
            self.hop,
            self.at,
            definition_version(self.stores, self.name),
            art.archive.version(),
        )
        if self.hop is None:
            return (
                *read,
                self.stores.tenant_datasets.held_version(self.name),
                file_sig(art.ruler.path),
            )
        cycle_dir = cycle_dir_for(art.base_dir, self.hop)
        campaign_id = self.hop.campaign_id
        # Its campaign's ledgers name the line; a fork's chain may reach past them.
        chain = ledger_chain(CycleDir(cycle_dir), moment=None) if cycle_dir.is_dir() else []
        ledgers = {*art.campaigns.campaign_cycle_ledgers(campaign_id), *(p for p, _ in chain)}
        return (
            *read,
            file_sig(CampaignLayout(art.campaigns.campaign_root_dir(campaign_id)).manifest),
            file_sig(CycleLayout(cycle_dir).hard_samples),
            *((str(path), file_sig(path)) for path in sorted(ledgers)),
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


def _drawn_rows(
    cells: CellsScope, drawn: PartitionRecord | None, slots: list[int]
) -> dict[int, Sample]:
    """A cycle reads the bank it DREW, by hash, and only the rows a page shows."""
    if drawn is None:
        return {}
    rows = cells.stores.tenant_datasets.rows(drawn.dataset_hash, slots)
    if rows is None:
        raise PayloadInvalidError(
            f"This campaign drew its samples from a bank no longer held ({drawn.dataset_hash}).",
            code="campaign_roster_unreadable",
            details={"dataset_name": cells.name, "dataset_hash": drawn.dataset_hash},
        )
    return rows


def _cycle_view(stores: Stores, hop: CycleHop) -> HardSamples:
    """Every round's close writes ``hard_samples.json``, so a cycle without one read no scale."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    if not cycle_dir.exists():
        raise NotFoundError(f"Cycle '{hop.campaign_id}/{hop.cycle_id}' not found")
    written = read_hard_samples(CycleLayout(cycle_dir).hard_samples)
    if written is not None:
        return written
    if campaign_ruler_scope(stores, hop.campaign_id) is None:
        raise NotFoundError(f"Campaign '{hop.campaign_id}' not found")
    return build_hard_samples(
        [],
        None,
        frontier=None,
        arm_theta={},
        parent_grades={},
        slots={},
        # Nothing measured: the round order is empty whatever leads it.
        n_min=0,
        cycle_id=hop.cycle_id,
    )


def _dataset_view(stores: Stores, name: str, bank: Bank) -> HardSamples:
    scorer = dataset_scorer(resolve_dataset(stores, name).directory)
    knobs = _dataset_campaign_config(stores, name).optimization
    n_min, enable_2pl = knobs.elimination_n_min, knobs.enable_2pl_graduation

    scope = RulerScope(scorer=scorer.id, dataset=name)
    reach = RulerReach(version=stores.ruler.version(), own=None)

    def build() -> HardSamples:
        observations = [
            Observation(address, key, y)
            for address, key, y in stores.ruler.rows(scope, reach)
            if key in bank.slot_of
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
            arm_theta={},
            parent_grades={},
            slots=bank.slot_of,
            n_min=n_min,
            cycle_id=None,
        )

    key = ("dataset_hard_samples", stores.ruler.path, scope, bank.dataset_hash, n_min, enable_2pl)
    held = derived(key, sig=reach.version, compute=build)
    return build() if held is None else held


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
    order: HardSampleOrder
    ranked: list[int]
    """Page positions in served order; index + 1 IS each row's ``hard_sample_rank``."""


def _selected(
    view: HardSamples, members: Collection[int], max_unmeasured: int | None, limit: int
) -> list[int]:
    in_view = [view.positions[k] for k in view.sample_order if view.positions[k] in members]
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
        candidates, rows = _dataset_cells(art_store, cells.name, bank, wanted)
    else:
        drawn = art_store.campaigns.read_bank_partition(hop)
        view = _cycle_view(art_store, hop)
        members = frozenset() if drawn is None else drawn.bank_ids
        selected = _selected(view, members, max_unmeasured, limit)
        shown = _drawn_rows(cells, drawn, selected)
        wanted = {shown[sid].key: sid for sid in selected}
        graded_under = campaign_scorer(art_store, hop.campaign_id)
        if graded_under is None:
            raise NotFoundError(f"Campaign '{hop.campaign_id}' not found")
        candidates, rows = _cycle_cells(art_store, hop, graded_under, wanted, cells.at)

    resolved, ranked = rank_hard_samples(
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
        order=resolved,
        ranked=[wanted[key] for key in ranked],
    )


def _cycle_cells(
    stores: Stores,
    hop: CycleHop,
    scorer: Scorer,
    wanted: Mapping[str, int],
    at: int | None,
) -> ScopeCells:
    """A column is an INDIVIDUAL, named by the arm it first was, so one a later round carried is served once."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    moment = None if at is None else Moment(CycleLayout(cycle_dir).ledger, at)
    chain = ledger_chain(CycleDir(cycle_dir), moment=moment)
    walks = scan_ledger_walks(chain)
    candidates: dict[str, CellCandidate] = {}
    cells: list[CellRow] = []
    for (round_no, arm_key), arm in scan_standing_rounds(chain).arms.items():
        state = arm.state
        if state is None or (isinstance(state, Cut) and state.kind == "invalid"):
            continue
        label, individual_id = arm_name(arm_key, state, round_no).label, state.individual.id
        live = isinstance(state, Proposed | Measuring)
        if (column := candidates.get(individual_id)) is not None:
            candidates[individual_id] = column.model_copy(update={"live": column.live or live})
            continue
        key = f"{hop.cycle_id}/{individual_id}"
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
            _row_cell(cell, key=key, slot=wanted[cell.key]) for cell in sheet if cell.key in wanted
        )
    return list(candidates.values()), cells


def _dataset_cells(stores: Stores, name: str, bank: Bank, wanted: Mapping[str, int]) -> ScopeCells:
    """A column is a CONFIGURATION's population: the archive names no individual or walk, so no ``RoleScope``."""
    scorer = dataset_scorer(resolve_dataset(stores, name).directory)
    archive = stores.archive
    sigs = archive.cell_signatures()
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
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
            _row_cell(cell, key=key, slot=wanted[cell.key]) for cell in sheet if cell.key in wanted
        )
    return candidates, cells


def _dataset_campaign_config(stores: Stores, name: str) -> CampaignConfig:
    return load_dataset_campaign_config(resolve_dataset(stores, name).directory)


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

    def _item(sid: int) -> DatasetItem:
        graded = _graded(by_sample.get(sid, []))
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
            hit_spread=hit_spread(graded),
        )

    graded_all = _graded(kept)
    samples = [_item(sid) for sid in ranked]
    spreads = Counter(item.hit_spread for item in samples)
    return CellsResponse(
        name=cells.name,
        scope=cells.scope,
        order=page.order,
        ruler=page.view.ruler,
        samples=samples,
        never_hit=spreads["never"],
        partly_hit=spreads["partly"],
        always_hit=spreads["always"],
        measured_samples=len(samples) - spreads["unmeasured"],
        unmeasured_samples=spreads["unmeasured"],
        candidates=candidates,
        cells=kept,
        total_measurements=len(graded_all),
        total_hits=sum(1 for f in graded_all if is_hit(f)),
        mean_fitness=sum(graded_all) / len(graded_all) if graded_all else None,
    )
