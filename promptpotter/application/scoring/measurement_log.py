"""The measurement log and one cell opened; a cell is read from ONE place, the archive, and graded per read."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import calibrate_delta_ruler
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    dataset_scorer,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.loaders import samples_from_dicts
from promptpotter.application.intelligence.hard_sample_archive import build_archive_observations
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples,
    rank_hard_samples,
    read_hard_samples,
)
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    experiment_outside_run,
)
from promptpotter.application.scoring.closed_rounds import campaign_scorer, walked_rows
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
from promptpotter.domain.results import HardSampleOrder, individual_cells
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
from promptpotter.infrastructure.store.dataset_access import (
    dataset_panel_rows,
    readable_dataset_dir,
    readable_dataset_rows,
)
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import Moment, derived
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, NotFoundError, PayloadInvalidError
from promptpotter.shared.measurement_context import RoleScope

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import ArchiveEntry, FiledAnswer
    from promptpotter.domain.scoring import GradedCell, PipelineData, Scorer

__all__ = [
    "campaign_scope_cycle",
    "measurement_log",
    "open_cell",
]

_SPAN_KEYS = frozenset({"step_tokens", "step_timings", "terminal_node", "total_time"})

_PREDICTED_CHARS = 120

ScopeCells = tuple[list[CellCandidate], list[CellRow]]


def _trim(text: object) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= _PREDICTED_CHARS else t[: _PREDICTED_CHARS - 1] + "…"


def _row_cell(cell: GradedCell, *, key: str) -> CellRow:
    facts = cell.facts
    if facts.answer is None:
        raise KeyError(f"the cell at slot {facts.sample_id} names no archive answer")
    account = TokenAccount.from_step_tokens(facts.pipeline.step_tokens)
    return CellRow(
        sample_id=facts.sample_id,
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
) -> Cell:
    """Spans are READ-TIME assembly: outputs attribute through the dataset's CURRENT schema."""
    facts = cell.facts
    pd = facts.pipeline
    recorded = pd.wire()
    sid = facts.sample_id
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
        sample_id=sid,
        dataset_name=filed.dataset_name,
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
    """The answer need not have been measured UNDER *name*: a walk replays a cell by content."""
    dataset_dir = readable_dataset_dir(stores, name)
    filed = stores.archive.answer(answer)
    if filed is None:
        raise NotFoundError(f"Answer '{answer}' not found")
    described = stores.archive.entry(filed.config_key, filed.dataset_name)
    if described is None:
        raise NotFoundError(f"Answer '{answer}' is filed under a configuration no index describes")
    filed = stores.archive.whole(filed)
    declared = dataset_pipeline_declaration(
        stores, dataset_dir, experiment_outside_run(dataset_dir)
    )
    schema = parse_pipeline_response(declared) if declared is not None else None
    return _assemble(described, filed, dataset_scorer(dataset_dir).grade(filed.cell), schema)


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


def _recorded_roster(
    stores: Stores, hop: CycleHop | None
) -> tuple[frozenset[int], dict[str, Any] | None]:
    if hop is None:
        return frozenset(), None
    drawn = stores.campaigns.read_bank_partition(hop)
    return (
        frozenset() if drawn is None else drawn.bank_ids,
        stores.campaigns.read_resolved_experiment(hop),
    )


def _load_dataset_rows(
    stores: Stores,
    name: str,
    recorded_ids: frozenset[int],
    experiment: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    """Missing rows are a bank-less dataset, never an unknown one; only a scope with no roster answers empty."""
    raw: dict[str, Any] | None
    try:
        panel = dataset_panel_rows(stores, name, experiment=experiment)
    except (ValueError, OSError, ImportError) as exc:
        raise PayloadInvalidError(
            f"Dataset {name!r} declares a connector-owned panel that could not be read: {exc}",
            code="dataset_panel_invalid",
            details={"dataset_name": name},
        ) from exc
    if panel is not None:
        raw = {"name": name, "items": [s.model_dump() for s in samples_from_dicts(panel)]}
    else:
        raw = readable_dataset_rows(stores, name)
    if raw is None:
        raw = {"name": name, "items": []}
    sample_lookup: dict[int, dict[str, Any]] = {}
    for item in raw["items"]:
        sid = int(item["sample_id"] if "sample_id" in item else item["id"])
        sample_lookup[sid] = item
    if not recorded_ids:
        return raw, sample_lookup
    if missing := recorded_ids - sample_lookup.keys():
        raise PayloadInvalidError(
            f"This campaign recorded {len(recorded_ids)} samples on {name!r} and "
            f"{len(missing)} of them could not be resolved from the dataset.",
            code="campaign_roster_unreadable",
            details={"dataset_name": name, "missing": len(missing)},
        )
    return raw, {sid: sample_lookup[sid] for sid in sorted(recorded_ids)}


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


def _cycle_view(stores: Stores, hop: CycleHop, name: str) -> HardSamples:
    """A missing ``hard_samples.json`` is never a ruler state: the ledger's ruler over no cells stands in."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    if not cycle_dir.exists():
        raise NotFoundError(f"Cycle '{hop.campaign_id}/{hop.cycle_id}' not found")
    written = read_hard_samples(CycleLayout(cycle_dir).hard_samples)
    if written is not None:
        return written
    return build_hard_samples(
        [],
        stores.campaigns.read_ruler(hop, dataset_name=name),
        frontier=None,
        arm_theta={},
        parent_grades={},
        cycle_id=hop.cycle_id,
    )


def _resolve_scope_artifact(stores: Stores, hop: CycleHop | None, name: str) -> HardSamples:
    if hop is not None:
        return _cycle_view(stores, hop, name)
    scorer = dataset_scorer(readable_dataset_dir(stores, name))
    knobs = _dataset_campaign_config(stores, name).optimization
    n_min, enable_2pl = knobs.elimination_n_min, knobs.enable_2pl_graduation

    def build() -> HardSamples:
        observations = build_archive_observations(
            stores, dataset_name=name, scorer=scorer, sample_ids=None
        )
        ruler, _ = calibrate_delta_ruler(
            None, n_min, enable_2pl=enable_2pl, archive_obs=observations
        )
        return build_hard_samples(
            observations, ruler, frontier=None, arm_theta={}, parent_grades={}, cycle_id=None
        )

    key = ("dataset_hard_samples", stores.archive.base_dir, name, scorer.id, n_min, enable_2pl)
    held = derived(key, sig=stores.archive.signature(), compute=build)
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
    raw: dict[str, Any]
    sample_lookup: dict[int, dict[str, Any]]
    view: HardSamples
    candidates: list[CellCandidate]
    cells: list[CellRow]
    order: HardSampleOrder
    ranked: list[int]
    """Page sample_ids in served order; index + 1 IS each row's ``hard_sample_rank``."""


def _resolve_leaderboard_page(
    stores: Stores,
    *,
    name: str,
    scope: HeatmapScope,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
    limit: int,
    max_unmeasured: int | None,
    order: HardSampleOrder | None,
    at: int | None,
) -> _LeaderboardPage:
    """*Measured* is a GRADED cell in this scope, never a δ entry, which forks inherit."""
    art_store, art_campaign, art_cycle = _artifact_scope_store(
        stores, campaign_id, cycle_id, descend
    )
    if at is not None and scope != "cycle":
        raise BadRequestError("at replays one cycle's ledger: it needs scope=cycle and its ids")
    hop = _scope_cycle(art_store, scope, art_campaign, art_cycle)
    raw, sample_lookup = _load_dataset_rows(stores, name, *_recorded_roster(art_store, hop))
    view = _resolve_scope_artifact(art_store, hop, name)
    in_view = [sid for sid in view.sample_order if sid in sample_lookup]
    selection = in_view + sorted(sample_lookup.keys() - set(in_view))
    selected = _trim_unmeasured(selection, set(in_view), max_unmeasured)[:limit]

    candidates, cells = _page_cells(art_store, hop, name, set(selected), at)

    resolved, ranked = rank_hard_samples(
        view,
        selected,
        measured={c.sample_id for c in cells if c.fitness is not None},
        order=order or _dataset_campaign_config(stores, name).hard_sample_order,
    )
    return _LeaderboardPage(
        raw=raw,
        sample_lookup=sample_lookup,
        view=view,
        candidates=candidates,
        cells=cells,
        order=resolved,
        ranked=ranked,
    )


def _page_cells(
    art_store: Stores, hop: CycleHop | None, name: str, wanted: set[int], at: int | None
) -> ScopeCells:
    if hop is None:
        return _dataset_cells(art_store, name, wanted)
    graded_under = campaign_scorer(art_store, hop.campaign_id)
    if graded_under is None:
        raise NotFoundError(f"Campaign '{hop.campaign_id}' not found")
    return _cycle_cells(art_store, hop, graded_under, wanted, at)


def _cycle_cells(
    stores: Stores,
    hop: CycleHop,
    scorer: Scorer,
    wanted: set[int],
    at: int | None,
) -> ScopeCells:
    """A column is an INDIVIDUAL, named by the arm it first was, so one a later round carried is served once."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    moment = None if at is None else Moment(CycleLayout(cycle_dir).ledger, at)
    chain = ledger_chain(CycleDir(cycle_dir), moment)
    walks = scan_ledger_walks(chain)
    candidates: dict[str, CellCandidate] = {}
    cells: list[CellRow] = []
    for (round_no, _), arm in scan_standing_rounds(chain).arms.items():
        label, individual_id = arm.label, arm.candidate_id
        if not label or not individual_id:
            continue
        live = arm.state == "minted"
        if (column := candidates.get(individual_id)) is not None:
            candidates[individual_id] = column.model_copy(update={"live": column.live or live})
            continue
        key = f"{hop.cycle_id}/{label}"
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
        cells.extend(_row_cell(cell, key=key) for cell in sheet if cell.sample_id in wanted)
    return list(candidates.values()), cells


def _dataset_cells(stores: Stores, name: str, wanted: set[int]) -> ScopeCells:
    """A column is a CONFIGURATION's population: the archive names no individual or walk, so no ``RoleScope``."""
    scorer = dataset_scorer(readable_dataset_dir(stores, name))
    archive = stores.archive
    sigs = archive.cell_signatures()
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    for entry in list_populations(stores, dataset_name=name):
        key = entry.config_key

        def grade(entry: ArchiveEntry = entry) -> CellSheet:
            return scorer.sheet(answer.cell for answer in load_population(stores, entry))

        sheet = derived(
            ("graded_population", archive.base_dir, key, name, scorer.id),
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
        cells.extend(_row_cell(cell, key=key) for cell in sheet if cell.sample_id in wanted)
    return candidates, cells


def _dataset_campaign_config(stores: Stores, name: str) -> CampaignConfig:
    return load_dataset_campaign_config(dataset_campaign_path(readable_dataset_dir(stores, name)))


def _graded(cells: list[CellRow]) -> list[float]:
    return [c.fitness for c in cells if c.fitness is not None]


def measurement_log(
    stores: Stores,
    name: str,
    *,
    scope: HeatmapScope,
    campaign_id: str | None,
    cycle_id: str | None,
    descend: CyclePath,
    limit: int,
    max_unmeasured: int | None,
    order: HardSampleOrder | None,
    candidate_id: str | None,
    round: int | None,
    status: SampleStatus | None,
    at: int | None = None,
) -> CellsResponse:
    """*at* replays the cycle scope to that ledger line, under the ranking as it stands NOW."""
    page = _resolve_leaderboard_page(
        stores,
        name=name,
        scope=scope,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        descend=descend,
        limit=limit,
        max_unmeasured=max_unmeasured,
        order=order,
        at=at,
    )
    sample_lookup = page.sample_lookup

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
        by_sample.setdefault(c.sample_id, []).append(c)

    filtered = candidate_id is not None or round is not None or status is not None
    ranked = [sid for sid in page.ranked if sid in by_sample] if filtered else page.ranked
    held = {c.candidate for c in kept}
    candidates = [c for c in page.candidates if c.key in held] if filtered else page.candidates

    def _item(sid: int) -> DatasetItem:
        graded = _graded(by_sample.get(sid, []))
        on_ruler = page.view.difficulty(sid)
        return DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid].get("ground_truth"),
            task=sample_lookup[sid].get("task"),
            # The position in the UNFILTERED ranking.
            hard_sample_rank=rank_of[sid] + 1,
            delta_label=on_ruler.label,
            delta=on_ruler.delta,
            delta_se=on_ruler.delta_se,
            p_hat=on_ruler.p_hat,
            pick_score=on_ruler.pick_score,
            n_measured=len(graded),
            mean_fitness=sum(graded) / len(graded) if graded else None,
            hit_spread=hit_spread(graded),
        )

    graded_all = _graded(kept)
    samples = [_item(sid) for sid in ranked]
    spreads = Counter(item.hit_spread for item in samples)
    return CellsResponse(
        name=page.raw["name"],
        scope=scope,
        order=page.order,
        ruler=page.view.ruler,
        samples=samples,
        never_hit=spreads["never"],
        partly_hit=spreads["partly"],
        always_hit=spreads["always"],
        candidates=candidates,
        cells=kept,
        total_measurements=len(graded_all),
        total_hits=sum(1 for f in graded_all if is_hit(f)),
        mean_fitness=sum(graded_all) / len(graded_all) if graded_all else None,
    )
