"""Instrument mode rides `run_scope.py::RunScope.instrument` alone, laid by `tasks.py::enter_inner_cell`.

The recursion's connector; the connector table loads it by name, so no backend package imports the application."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, NamedTuple

from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import campaign_refusal
from promptpotter.application.jobs.launcher.launch import admit
from promptpotter.application.jobs.launcher.mint_and_start import InnerSeat
from promptpotter.application.jobs.launcher.run_job import HeldRun
from promptpotter.application.jobs.mint import (
    ConfigDriftError,
    CyclePlan,
    resolve_cycle_plan,
    resolve_resume,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.optimizer_manifest import (
    resolved_overrides,
)
from promptpotter.application.pipeline_resolve import laid_session
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.bench import line_spans
from promptpotter.application.runner.inner.tasks import (
    InnerTasks,
    ResolvedInnerPanel,
    enter_inner_cell,
    inner_instrument_config,
    inner_spawn_context,
    panel_cell,
)
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.campaign import Campaign, CampaignCycle, Optimize
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cells import Seat
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.l4.cells import InnerCells, cell_key
from promptpotter.domain.l4.inner_origin import INNER_ORIGIN_KEY
from promptpotter.domain.l4.proxies import (
    BENCH_ROWS_KEY,
    INNER_CELL_TERMS,
    INNER_REPORTED_KEYS,
    INNER_RESULT_KEY,
    compute_outer_proxies,
    inner_cell_facts,
    inner_cell_report,
)
from promptpotter.domain.layer_fold import Layer
from promptpotter.domain.phases import ERROR_CATEGORY_INFO, StopReason
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.records.run_records import (
    RunPhaseRecord,
    SpawnedBy,
    SpawnedRecord,
)
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import split_rendered_prompt
from promptpotter.infrastructure.llm.telemetry import bound_round
from promptpotter.infrastructure.run_scope import (
    InnerSpawnContext,
    run_scope,
    scope_context,
    scoped,
)
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.layout import (
    SandboxOwner,
    dataset_id_prefix,
    sandbox_owner_path,
)
from promptpotter.infrastructure.store.stores import Stores, sandbox_store
from promptpotter.shared.errors import (
    CellSendRefusedError,
    CellUnscoreableError,
    ErrorCategory,
    PayloadInvalidError,
)
from promptpotter.shared.hashing import stable_hash

# An inner cycle's budget is ROUNDS, never a spend or token cap: those trip on token counts, which jitter.

_REFUSALS: dict[StopReason | None, ErrorCategory] = {
    info.stops: category for category, info in ERROR_CATEGORY_INFO.items() if info.stops is not None
}

# Per round, for ONE inner cell; never instrument-depth-dependent, or an outcome moves with what is in flight.
OUTER_SAMPLE_WALL_S_PER_ROUND = 600.0

Overrides = dict[str, dict[str, Any]]


def _spawn_provenance(ctx: InnerSpawnContext, round_num: int, query: str) -> dict[str, Any]:
    cand = run_scope().measured
    return {
        "round": round_num,
        "individual_id": cand.individual_id if cand else None,
        "role": cand.role if cand else None,
        "cycle_id": ctx.enclosing[-1].cycle_id if cand else None,
        "task": query,
    }


def inner_campaign_id(dataset: str, key: str, overrides: Overrides) -> str:
    """Content, never asker: the cell's key (`cell_key`) and the arm's edits, at a root cycle id's
    width."""
    edits = stable_hash([key, resolved_overrides(overrides)], length=12)
    return f"{dataset_id_prefix(dataset)}__{edits}"


class _InnerCell(NamedTuple):
    """Resolved in the OUTER task, which alone carries the role."""

    ctx: InnerSpawnContext
    cell: Seat
    dataset: str
    overrides: Overrides
    campaign_id: str


def _resolve_inner_cell(sample: Sample, payload: dict[str, Any]) -> _InnerCell:
    ctx = inner_spawn_context()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: this run has no cycle to spawn an inner campaign under"
        )
    cells = _cells(ctx)
    pinned = panel_cell(sample, cells.cells)
    arm = cells.own(pinned.cell, payload.get("optimizer_prompt_overrides") or {})
    dataset = cells.dataset(pinned.cell)
    campaign_id = inner_campaign_id(dataset, pinned.instrument, arm)
    return _InnerCell(ctx, pinned.cell, dataset, arm, campaign_id)


def claim_sandbox(ctx: InnerSpawnContext) -> Stores:
    """Inside a cell, never at `tasks.py::inner_spawn_of`, which fires for EVERY cycle."""
    owned_by = ctx.inside[-1]
    owner = SandboxOwner(
        tenant_id=str(ctx.stores.tenant_id),
        campaign_id=owned_by.campaign_id,
        cycle_id=owned_by.cycle_id,
    )
    sandbox = sandbox_store(ctx.stores, ctx.inside[-1])
    write_text(sandbox_owner_path(sandbox.projects_root), owner.model_dump_json())
    return sandbox


def _banked_inner_rounds(store: CampaignStore, root: CycleHop) -> int:
    index = store.load(store.live_cycle(root), moment=None)
    return len(index.rounds) if index else 0


def inner_cell_envelope_s(sample: Sample, payload: dict[str, Any]) -> float:
    """``max(1, …)`` grants a fully-banked cycle the round it needs to replay and finalize."""
    cell = _resolve_inner_cell(sample, payload)
    store = sandbox_store(cell.ctx.stores, cell.ctx.inside[-1]).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    banked = _banked_inner_rounds(store, campaign.root_hop) if campaign else 0
    rounds = _cells(cell.ctx).held(cell.cell).campaign.optimization.budget.rounds
    assert rounds is not None, "a panel lays its seats' rounds (`InnerTasks.resolve_cells`)"
    return OUTER_SAMPLE_WALL_S_PER_ROUND * max(1, (rounds + 1) - banked)


def inner_cell_hop(sample: Sample, payload: dict[str, Any]) -> CycleHop | None:
    """The hop holding the cell's campaign line, as the outer cell's heartbeat names it."""
    cell = _resolve_inner_cell(sample, payload)
    store = sandbox_store(cell.ctx.stores, cell.ctx.inside[-1]).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    return None if campaign is None else store.live_cycle(campaign.root_hop)


def _continue_inner_campaign(
    session: Session, campaign: Campaign, train_data: Sequence[Sample]
) -> CyclePlan:
    """``stop_reason_outcome`` governs scoring, never resumption."""
    store = session.store.campaigns
    # A rebase retires the root under `superseded_by`; the banked trajectory is its successor's.
    hop = store.live_cycle(campaign.root_hop)
    run = session.store.run_state(hop)
    if store.load(hop, moment=None) is None:
        raise CellUnscoreableError(
            f"its campaign {campaign.campaign_id} names successor {hop.cycle_id}, which has no "
            "index",
            spent={},
        )
    if not run.resumable:
        raise CellUnscoreableError(
            f"its campaign {campaign.campaign_id}/{hop.cycle_id} reads {run.run_phase} — another "
            "producer owns it, and two runs writing one cycle is not a measurement",
            spent={},
        )
    bound = session.bound(CampaignCycle(campaign, hop.cycle_id))
    try:
        return resolve_resume(bound, campaign, train_data)
    except ConfigDriftError as refused:
        raise CellUnscoreableError(str(refused), spent={}) from refused


def _cells(ctx: InnerSpawnContext) -> InnerCells:
    assert ctx.cells is not None, "run init refuses an outer dataset that holds no panel"
    return ctx.cells


def arm_layer(overrides: Overrides) -> Layer[str]:
    """An arm's edit, each node's on that node alone, laid last over the cell's own overlay: the
    selection refuses what no render can apply."""
    nodes = {node: {"config": edit} for node, edit in overrides.items()}
    return Layer("arm", {"optimization": {"nodes": nodes}} if nodes else {})


def _seat_rows(session: Session, dataset: str) -> list[Sample]:
    if not (rows := list(session.bank.samples)):
        raise ValueError(f"inner dataset {dataset!r} loaded zero samples")
    return rows


def _laid_seat(
    session: Session, cells: InnerCells, cell: Seat, overrides: Overrides
) -> tuple[Session, CampaignConfig]:
    """*cell*'s instrument with the arm's edits laid last: what its seat runs, and its key reads."""
    instrument = inner_instrument_config(
        cell,
        cells.held(cell).campaign,
        solver=session.pipeline_read.declared_schema().prompt_nodes(),
    )
    armed = instrument.overlaid(arm_layer(overrides))
    # The instrument stands in for the cell dataset's block: the outer campaign set every knob.
    return laid_session(
        session, ConfigLayers({}, armed.model_dump(mode="json", exclude_unset=True))
    )


async def panel_rows(stores: Stores, panel: ResolvedInnerPanel) -> list[dict[str, Any]]:
    """THE build of an outer's bank: each seat keyed (`cell_key`) by the origin it plans under
    `Optimize`, before any purpose binds, in one session per seat dataset. A refusal any seat
    owes refuses here, before the outer mints. ``ground_truth`` is ``None``: an L4 cell is graded
    by its outer proxies, never a label."""
    cells = panel.cells(stores)
    keys: dict[str, str] = {}
    for name in sorted(cells.by_dataset):
        async with init_services(
            dataset_name=name, identity=stores.identity, stores=stores
        ) as session:
            rows = _seat_rows(session, name)
            for seat in (s for s in cells.cells if cells.dataset(s) == name):
                plan = resolve_cycle_plan(*_laid_seat(session, cells, seat, {}), Optimize(), rows)
                rounds = plan.cycle.config.optimization.budget.rounds
                assert rounds is not None, "a panel lays its seats' rounds (`resolve_cells`)"
                keys[seat.id] = cell_key(
                    plan.cycle_id, rounds, plan.treatment.digest, cells.estimator
                )
    return panel.samples(keys)


@asynccontextmanager
async def _open_seat(cell: _InnerCell, store: Stores) -> AsyncIterator[HeldRun]:
    ctx = cell.ctx
    # The campaign that exists decides, never an id its origin content derives: a drifted origin
    # reaches the drift refusal rather than a fresh root.
    campaign = store.campaigns.load_campaign(cell.campaign_id)
    if campaign is not None and (refused := campaign_refusal(store, campaign)) is not None:
        raise CellUnscoreableError(str(refused), spent={}) from refused
    async with init_services(
        dataset_name=cell.dataset, identity=store.identity, stores=store
    ) as session:
        session.state.control = RunControl(
            commands=store.commands, enclosing=ctx.enclosing, acting=ctx.acting
        )
        train_data = _seat_rows(session, cell.dataset)
        try:
            session, config = _laid_seat(session, _cells(ctx), cell.cell, cell.overrides)
            plan = (
                resolve_cycle_plan(session, config, ctx.purpose, train_data)
                if campaign is None
                else _continue_inner_campaign(session, campaign, train_data)
            )
        except PayloadInvalidError as refused:
            if refused.code != "optimizer_overlay":
                raise
            # The arm's fault, banked on its cell in the selection's own words, before anything is sent.
            raise CellUnscoreableError(str(refused), spent={}) from refused
        # Admitted BEFORE the mint, as every slotless door is: a refusal mints no seat.
        seat = InnerSeat(
            plan, cell.campaign_id, cell.dataset, None if campaign is None else plan.session.at
        )
        admitted = await asyncio.to_thread(
            admit,
            seat,
            RunMode(),
            stores=store,
            job_registry=JobRegistry.attach(store.jobs_dir),
            inline=None,
        )
        yield await admitted.held()


async def _run_inner_campaign(
    cell: _InnerCell,
    minted: list[CycleHop],
    spawned_by: dict[str, Any],
) -> tuple[CycleResult, list[RoundResult], str]:
    """Runs in a FRESH task, so every ContextVar bound below is this cell's alone. The rounds are
    the ones its LINE spent (`bench.py::LineSpan.own`), a rebase's discarded ones included, and the
    words its stop was declared in, off its ledger."""
    store = claim_sandbox(cell.ctx)
    # Over the seat too: its init reads what this cell remembers.
    with scoped(memory=enter_inner_cell(cell.ctx, store.campaigns, cell.campaign_id)):
        async with _open_seat(cell, store) as held, AsyncExitStack() as unrun:
            # The seat's run ends inside: its opening's exit is its END.
            unrun.push_async_exit(held.closing)
            session = held.session
            session.store.campaigns.append(
                session.hop, SpawnedRecord(spawned_by=SpawnedBy.model_validate(spawned_by))
            )
            minted.append(session.hop)
            unrun.pop_all()
            ended, result = await held.run(readout_sink=None)
            spans = line_spans(ended.store, ended.state.scoring.scorer, ended.hop)
            declared = ended.store.campaigns.last(ended.hop, RunPhaseRecord)
    assert declared is not None and declared.stop_reading is not None, "an inner run ends declared"
    return result, [rr for span in spans for rr in span.own], declared.stop_reading.said


def _reclaim_forks_partial(cell: _InnerCell, hop: CycleHop) -> None:
    """A fork's cell abandoning the inner campaign it minted reclaims it at its own exit, where no
    holder has it: it lies in the parent's sandbox (`claim_sandbox`), which no sweep reclaims while
    the parent stands, and a CLI-only run runs no sweep."""
    if cell.ctx.enclosing[-1].cycle_id == cell.ctx.inside[-1].cycle_id:
        return
    store = sandbox_store(cell.ctx.stores, cell.ctx.inside[-1]).campaigns
    if store.holder(hop) is None:
        store.delete_campaign(cell.campaign_id, keep_results=False)


async def run_inner_cycle(sample: Sample, payload: dict[str, Any]) -> dict[str, Any]:
    """One cell: an inner hop in the asker's sandbox, read by one law."""
    query = sample.query
    cell = _resolve_inner_cell(sample, payload)
    minted: list[CycleHop] = []
    fresh = (
        sandbox_store(cell.ctx.stores, cell.ctx.inside[-1]).campaigns.load_campaign(
            cell.campaign_id
        )
        is None
    )
    # Captured in the OUTER task: the inner task's context copy rebinds the round.
    spawned_by = _spawn_provenance(cell.ctx, bound_round(), query)

    # The backend's hole is not the optimizer prompt's: asked once more, the cell resumes and
    # re-measures it, and only a second hole grades it.
    for _ in range(2):
        # Awaited DIRECTLY, never under `asyncio.shield`/`wait`, which orphan it to keep billing.
        # The context binds no OUTER candidate, or inner optimizer calls bill under the outer cell's role.
        inner_task = asyncio.create_task(
            _run_inner_campaign(cell, minted, spawned_by),
            context=scope_context(measured=None),
        )
        try:
            result, rounds, stopped = await inner_task
        except asyncio.CancelledError:
            if not inner_task.done():
                inner_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await inner_task
            if fresh and minted:
                _reclaim_forks_partial(cell, minted[0])
            raise
        if result.stop_reason is not StopReason.BACKEND_HOLE:
            break
    said = f"its inner campaign {cell.campaign_id} stopped: {stopped}"
    if (refused := _REFUSALS.get(result.stop_reason)) is not None:
        # The inner run spends the outer's key and ceiling, so the refusal halts the walk for every later cell.
        raise CellSendRefusedError(said, category=refused, spent={})
    try:
        scored = compute_outer_proxies(result, rounds)
    except CellUnscoreableError as unscored:
        # Every other no-evidence shape, which `measure_sample` resolves.
        raise CellUnscoreableError(f"{unscored}; {said}", spent=unscored.spent) from unscored
    lift, se = scored.held_out_lift, scored.held_out_lift_se
    held = "floored" if lift is None else f"B{lift:+.3f}" + ("" if se is None else f"±{se:.3f}")

    data: dict[str, Any] = {
        # A summary line, not an answer to be matched: this cell carries no label, so add no hit/miss reader.
        INNER_RESULT_KEY: [f"inner:{query} {held}"],
        **inner_cell_report(inner_cell_facts(result), scored),
        **({} if result.curve is None else {BENCH_ROWS_KEY: result.curve.rows}),
        # An inner campaign consumes its optimizer's ENTIRE outer config, so the stamp is its terminal.
        "terminal_node": _cells(cell.ctx).terminal(cell.cell),
        # No `step_tokens`: every call was billed as it settled, and returning it bills the cell twice.
    }
    return {"data": data}


# A RESOURCE ceiling (peak RSS, one shared provider key), never a scientific one.
MAX_CELLS_IN_FLIGHT = 4


def _inner_cells(stores: Stores, experiment: ResolvedInnerPanel | None) -> InnerCells:
    if experiment is None:
        raise PayloadInvalidError(
            "an outer dataset's graph and identity derive from its panel, "
            "which this box does not hold.",
            code="pipeline_config_invalid",
        )
    return experiment.cells(stores)


def _pipeline_declaration(stores: Stores, experiment: ResolvedInnerPanel | None) -> dict[str, Any]:
    return _inner_cells(stores, experiment).pipeline()


def _identity_config(
    stores: Stores, experiment: ResolvedInnerPanel | None, _base_url: str
) -> dict[str, dict[str, Any]]:
    """What reads every cell's number alike; what measures ONE cell is in its pin (`cell_key`)."""
    cells = _inner_cells(stores, experiment)
    return {cells.chain[0]: {INNER_ORIGIN_KEY: cells.estimator}}


def promptpotter_wire_adapter(
    sample: Sample,
    pipeline_params: PipelineParams,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": sample.query}
    # Each inner node's prompt arrives as its field map (`NodePromptInfo.field_wise`), laid flat.
    optimizer_prompt_overrides = {}
    for node, cfg in pipeline_params.laid_nodes.items():
        prompt, config = split_rendered_prompt(cfg)
        assert prompt is None or isinstance(prompt, dict), "the recursion serves field-wise nodes"
        if laid := {**config, **(prompt or {})}:
            optimizer_prompt_overrides[node] = laid
    if optimizer_prompt_overrides:
        payload["optimizer_prompt_overrides"] = optimizer_prompt_overrides

    return payload


async def _in_process_run(
    _workload: InProcessWorkload, sample: Sample, payload: dict[str, Any], _pairs: object
) -> dict[str, Any]:
    return await run_inner_cycle(sample, payload)


def _cell_envelope_s(sample: Sample, pipeline_params: PipelineParams) -> float:
    """Through this connector's OWN adapter, so the envelope and the run that spends it read one payload."""
    return inner_cell_envelope_s(sample, promptpotter_wire_adapter(sample, pipeline_params))


def _cell_hop(sample: Sample, pipeline_params: PipelineParams) -> CycleHop | None:
    return inner_cell_hop(sample, promptpotter_wire_adapter(sample, pipeline_params))


CONNECTOR = Connector(
    name="promptpotter",
    execution="in_process",
    wire_adapter=promptpotter_wire_adapter,
    extract_experiment=panel_rows,
    experiment_model=ResolvedInnerPanel,
    declared_panel_model=InnerTasks,
    in_process_run=_in_process_run,
    max_cells_in_flight=MAX_CELLS_IN_FLIGHT,
    holds_own_sends=True,
    cancel_stops_billing=True,
    # Without it a throttle storm stretches one cell, a whole campaign, across the round measuring it.
    cell_envelope_s=_cell_envelope_s,
    cell_payer="host",
    # Every inner send is admitted and billed under the enclosing run's book.
    sent_spend_bound=None,
    cells_priced=True,
    cell_hop=_cell_hop,
    measured_unit="cell",
    required_observation_keys=(INNER_RESULT_KEY,),
    cell_terms=INNER_CELL_TERMS,
    reported_keys=INNER_REPORTED_KEYS,
    resolve_experiment=ResolvedInnerPanel.read,
    # The outer's graph IS the inner optimizer's, so it is served here, never mirrored in a file.
    pipeline_declaration=_pipeline_declaration,
    identity_config=_identity_config,
    self_optimization=True,
)
