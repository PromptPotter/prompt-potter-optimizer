"""Instrument mode is declared in ``shared/measurement_context.py`` alone; read it before changing what it binds.

The recursion's connector; the connector table loads it by name, so no backend package imports the application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any, NamedTuple

from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import campaign_refusal, slotless_share
from promptpotter.application.jobs.mint import (
    ConfigDriftError,
    declare,
    mint,
    resolve_cycle_plan,
    resolve_resume,
)
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    resolved_overrides,
)
from promptpotter.application.pipeline_resolve import laid_session
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.runner.inner.narrative import inner_narrative, narrative_head
from promptpotter.application.runner.inner.tasks import (
    ResolvedPanel,
    enter_inner_cell,
    inner_instrument_config,
    inner_spawn_context,
    panel_cell,
    parse_panel,
    parse_resolved,
    seat_draw,
)
from promptpotter.connectors.protocol import Connector, InProcessWorkload
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
from promptpotter.domain.cells import Seat
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.job import InnerRun
from promptpotter.domain.l4.cells import InnerCells, InnerTaskSpec
from promptpotter.domain.l4.inner_origin import INNER_ORIGIN_KEY
from promptpotter.domain.l4.proxies import (
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
from promptpotter.domain.records.run_records import SpawnedBy, SpawnedRecord
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.sample import Sample
from promptpotter.infrastructure.llm.telemetry import bound_round
from promptpotter.infrastructure.run_scope import InnerSpawnContext, scoped
from promptpotter.infrastructure.store.campaign_store.store import CampaignStore
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.layout import (
    SandboxOwner,
    dataset_id_prefix,
    sandbox_owner_path,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import (
    CellSendRefusedError,
    CellUnscoreableError,
    ErrorCategory,
    PayloadInvalidError,
)
from promptpotter.shared.measurement_context import (
    MAX_INSTRUMENT_DEPTH,
    instrument_depth,
    measured_pass,
    measured_pass_context,
)

logger = logging.getLogger(__name__)


# An inner cycle's budget is ROUNDS, never a spend or token cap: those trip on token counts, which jitter.

_REFUSALS: dict[StopReason | None, ErrorCategory] = {
    info.stops: category for category, info in ERROR_CATEGORY_INFO.items() if info.stops is not None
}

# Per round, for ONE inner cell; never instrument-depth-dependent, or an outcome moves with what is in flight.
OUTER_SAMPLE_WALL_S_PER_ROUND = 600.0

Overrides = dict[str, dict[str, Any]]


def _spawn_provenance(ctx: InnerSpawnContext, round_num: int, query: str) -> dict[str, Any]:
    cand = measured_pass()
    return {
        "round": round_num,
        "individual_id": cand.individual_id if cand else None,
        "role": cand.role if cand else None,
        "cycle_id": ctx.enclosing[-1].cycle_id if cand else None,
        "task": query,
    }


def inner_campaign_id(spec: InnerTaskSpec, overrides: Overrides) -> str:
    """Content, never asker: the cell's continuation and the arm's edits, never why a pass ran."""
    return f"{dataset_id_prefix(spec.inner_dataset)}__{spec.continuation(resolved_overrides(overrides))}"


class _InnerCell(NamedTuple):
    """Resolved in the OUTER task, which alone carries the role."""

    ctx: InnerSpawnContext
    cell: Seat
    spec: InnerTaskSpec
    overrides: Overrides
    campaign_id: str


def _resolve_inner_cell(sample: Sample, payload: dict[str, Any]) -> _InnerCell:
    ctx = inner_spawn_context()
    if ctx is None:
        raise RuntimeError(
            "promptpotter connector: this run has no cycle to spawn an inner campaign under"
        )
    cells = _cells(ctx)
    cell = panel_cell(sample)
    arm = cells.own(cell, payload.get("optimizer_prompt_overrides") or {})
    spec = cells.spec(cell)
    campaign_id = inner_campaign_id(spec, arm)
    return _InnerCell(ctx, cell, spec, arm, campaign_id)


def sandbox_stores(ctx: InnerSpawnContext) -> Stores:
    """Only campaign STATE is sandboxed; the caches stay on the real tenant tree via ``shared_root``."""
    built = build_stores(
        ctx.identity,
        projects_root=ctx.inner_sandbox_root,
        install_datasets_root=ctx.install_datasets_root,
        research_datasets_root=ctx.research_datasets_root,
        shared_root=ctx.shared_root,
    )
    return replace(built, inside=ctx.inside)


def claim_sandbox(ctx: InnerSpawnContext) -> Stores:
    """Inside a cell, never at `tasks.py::inner_spawn_of`, which fires for EVERY cycle."""
    owned_by = ctx.inside[-1]
    owner = SandboxOwner(
        tenant_id=str(ctx.identity.tenant_id),
        campaign_id=owned_by.campaign_id,
        cycle_id=owned_by.cycle_id,
    )
    write_text(sandbox_owner_path(ctx.inner_sandbox_root), owner.model_dump_json())
    return sandbox_stores(ctx)


def _banked_inner_rounds(store: CampaignStore, root: CycleHop) -> int:
    index = store.load(store.line_holder(root), moment=None)
    return len(index.rounds) if index else 0


def inner_cell_envelope_s(sample: Sample, payload: dict[str, Any]) -> float:
    """``max(1, …)`` grants a fully-banked cycle the round it needs to replay and finalize."""
    cell = _resolve_inner_cell(sample, payload)
    store = sandbox_stores(cell.ctx).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    banked = _banked_inner_rounds(store, campaign.root_hop) if campaign else 0
    return OUTER_SAMPLE_WALL_S_PER_ROUND * max(1, (cell.spec.n_rounds + 1) - banked)


def inner_cell_hop(sample: Sample, payload: dict[str, Any]) -> CycleHop | None:
    """The hop holding the cell's campaign line, as the outer cell's heartbeat names it."""
    cell = _resolve_inner_cell(sample, payload)
    store = sandbox_stores(cell.ctx).campaigns
    campaign = store.load_campaign(cell.campaign_id)
    return None if campaign is None else store.line_holder(campaign.root_hop)


def _continue_inner_campaign(
    session: Session, campaign: Campaign, train_data: Sequence[Sample]
) -> tuple[Session, CycleConfig, SelectedOptimizer]:
    """``stop_reason_outcome`` governs scoring, never resumption."""
    store = session.store.campaigns
    # A rebase retires the root under `superseded_by`; the banked trajectory is its successor's.
    hop = store.line_holder(campaign.root_hop)
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
    bound = session.bound(hop, purpose=campaign.declaration.purpose)
    try:
        plan = resolve_resume(bound, campaign, train_data)
    except ConfigDriftError as refused:
        raise CellUnscoreableError(str(refused), spent={}) from refused

    logger.info(
        "inner campaign %s/%s CONTINUES from %d banked round record(s) (was %s)",
        campaign.campaign_id,
        hop.cycle_id,
        _banked_inner_rounds(store, campaign.root_hop),
        run.run_phase,
    )
    return plan.session, plan.cycle, plan.optimizer


def _cells(ctx: InnerSpawnContext) -> InnerCells:
    assert ctx.cells is not None, "run init refuses an outer dataset that holds no panel"
    return ctx.cells


class _Opened(NamedTuple):
    session: Session
    cycle: CycleConfig
    train_data: list[Sample]
    optimizer: SelectedOptimizer


def arm_layer(overrides: Overrides) -> Layer[str]:
    """An arm's edit, each node's on that node alone, laid last over the cell's own overlay: the
    selection refuses what no render can apply."""
    nodes = {node: {"config": edit} for node, edit in overrides.items()}
    return Layer("arm", {"optimization": {"nodes": nodes}} if nodes else {})


@asynccontextmanager
async def _open_seat(cell: _InnerCell, store: Stores) -> AsyncIterator[_Opened]:
    ctx, spec = cell.ctx, cell.spec
    # The campaign that exists decides, never an id its origin content derives: a drifted origin
    # reaches the drift refusal rather than a fresh root.
    campaign = store.campaigns.load_campaign(cell.campaign_id)
    if campaign is not None and (refused := campaign_refusal(store, campaign)) is not None:
        raise CellUnscoreableError(str(refused), spent={}) from refused
    async with init_services(
        dataset_name=spec.inner_dataset, identity=ctx.identity, stores=store
    ) as session:
        session.state.control = RunControl(
            commands=store.commands, enclosing=ctx.enclosing, acting=ctx.acting
        )
        all_samples = session.bank.samples
        if not all_samples:
            raise ValueError(f"inner dataset {spec.inner_dataset!r} loaded zero samples")
        search, bench = seat_draw(all_samples, spec)
        if len(bench) < spec.n_samples_bench:
            # Before anything is minted or sent: a short bench is a panel geometry error, not a measurement.
            raise CellUnscoreableError(
                f"inner dataset {spec.inner_dataset!r} holds {len(all_samples)} rows; the bank takes "
                f"{len(search)} and leaves {len(bench)} for a bench of {spec.n_samples_bench}. Lower "
                "`n_samples_origin` / `n_samples_bench` or give the cell a larger dataset.",
                spent={},
            )
        train_data = [*search, *bench]
        instrument = inner_instrument_config(
            spec,
            CampaignConfig.model_validate(
                dict(_cells(ctx).by_dataset[spec.inner_dataset].campaign_config)
            ),
            solver=session.pipeline_schema.prompt_nodes(),
            n_search=len(search),
        )
        try:
            armed = instrument.overlaid(arm_layer(cell.overrides))
            # The instrument stands in for the cell dataset's block: the outer campaign set every knob.
            session, config = laid_session(
                session, ConfigLayers({}, armed.model_dump(mode="json", exclude_unset=True))
            )
            declaration = declare(session, config, purpose=ctx.purpose)
            if campaign is None:
                minted = mint(
                    resolve_cycle_plan(session, declaration, train_data),
                    campaign_id=cell.campaign_id,
                    adopting=None,
                )
                session, cycle, optimizer = minted.session, minted.cycle, minted.optimizer
            else:
                session, cycle, optimizer = _continue_inner_campaign(session, campaign, train_data)
        except PayloadInvalidError as refused:
            if refused.code != "optimizer_overlay":
                raise
            # The arm's fault, banked on its cell in the selection's own words, before anything is sent.
            raise CellUnscoreableError(str(refused), spent={}) from refused
        yield _Opened(session, cycle, train_data, optimizer)


async def _run_inner_campaign(
    cell: _InnerCell,
    minted: list[CycleHop],
    spawned_by: dict[str, Any],
) -> CycleResult:
    """Runs in a FRESH task, so every ContextVar bound below is this cell's alone."""
    store = claim_sandbox(cell.ctx)
    # Over the seat too: its init reads what this cell remembers.
    with scoped(memory=enter_inner_cell(cell.ctx, store.campaigns, cell.campaign_id)):
        async with _open_seat(cell, store) as opened:
            session, config = opened.session, opened.cycle.config
            session.store.campaigns.append(
                session.hop, SpawnedRecord(spawned_by=SpawnedBy.model_validate(spawned_by))
            )
            minted.append(session.hop)
            observers = await asyncio.to_thread(
                build_run_observers, session=session, campaign_config=config
            )
            # Each send is admitted by its own book AND the ROOT's; its share bounds every checkpoint.
            _, result = await slotless_share(
                config,
                stores=session.store,
                holder=InnerRun(cell.spec.inner_dataset),
                hop=session.hop,
                sends=(),
                run=lambda held: run_optimization(
                    opened.train_data,
                    opened.cycle,
                    session=session,
                    observers=observers,
                    budget=held,
                    mode=RunMode(),
                    optimizer=opened.optimizer,
                ),
            )
    return result


def _reclaim_forks_partial(cell: _InnerCell, hop: CycleHop) -> None:
    """A fork's cell abandoning the inner campaign it minted reclaims it at its own exit, where no
    holder has it: it lies in the parent's sandbox (`claim_sandbox`), which no sweep reclaims while
    the parent stands, and a CLI-only run runs no sweep."""
    if cell.ctx.enclosing[-1].cycle_id == cell.ctx.inside[-1].cycle_id:
        return
    store = sandbox_stores(cell.ctx).campaigns
    if store.holder(hop) is None:
        store.delete_campaign(cell.campaign_id, keep_results=False, changed_at=utcnow_iso())


async def run_inner_cycle(sample: Sample, payload: dict[str, Any]) -> dict[str, Any]:
    """One cell: an inner hop in the asker's sandbox, read by one law."""
    query = sample.query
    depth = instrument_depth()
    if depth >= MAX_INSTRUMENT_DEPTH:
        raise CellUnscoreableError(
            f"promptpotter connector: an inner run {depth} level(s) deep spawns no inner campaign "
            f"(MAX_INSTRUMENT_DEPTH={MAX_INSTRUMENT_DEPTH}): the inner dataset's own backend_type "
            "is 'promptpotter' — name an inner_benchmark on another backend in the panel.",
            spent={},
        )
    cell = _resolve_inner_cell(sample, payload)
    minted: list[CycleHop] = []
    fresh = sandbox_stores(cell.ctx).campaigns.load_campaign(cell.campaign_id) is None
    # Captured in the OUTER task: the inner task's context copy rebinds the round.
    spawned_by = _spawn_provenance(cell.ctx, bound_round(), query)

    # Awaited DIRECTLY, never under `asyncio.shield`/`wait`, which orphan it to keep billing.
    # The context binds no OUTER candidate, or inner optimizer calls bill under the outer cell's role.
    inner_task = asyncio.create_task(
        _run_inner_campaign(cell, minted, spawned_by),
        context=measured_pass_context(None),
    )
    try:
        result = await inner_task
    except asyncio.CancelledError:
        logger.warning(
            "inner cell %s abandoned; its partial campaign is at %s",
            query,
            sandbox_stores(cell.ctx).campaigns.cycle_dir(minted[0])
            if minted
            else "<not yet minted>",
        )
        if not inner_task.done():
            inner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await inner_task
        if fresh and minted:
            _reclaim_forks_partial(cell, minted[0])
        raise
    if (refused := _REFUSALS.get(result.stop_reason)) is not None:
        # The inner run spends the outer's key and ceiling, so the refusal halts the walk for every later cell.
        raise CellSendRefusedError(
            f"its inner campaign {cell.campaign_id} stopped on {result.stop_reason}",
            category=refused,
            spent={},
        )
    # Raises `CellUnscoreableError` on every other no-evidence shape, which `measure_sample` resolves.
    scored = compute_outer_proxies(result)
    facts = inner_cell_facts(result)
    delta = "—" if facts.mean_round_delta is None else f"{facts.mean_round_delta:+.3f}"
    held = "floored" if scored.held_out_lift is None else f"B{scored.held_out_lift:+.3f}"

    data: dict[str, Any] = {
        # A summary line, not an answer to be matched: this cell carries no label, so add no hit/miss reader.
        INNER_RESULT_KEY: [f"inner:{query} {held} D{delta}"],
        "reasoning_trace": inner_narrative(result, facts, scored, narrative_head(cell.spec)),
        **inner_cell_report(facts, scored),
        # An inner campaign consumes its optimizer's ENTIRE outer config, so the stamp is its terminal.
        "terminal_node": _cells(cell.ctx).terminal(cell.cell),
        # No `step_tokens`: every call was billed as it settled, and returning it bills the cell twice.
    }
    return {"data": data}


# A RESOURCE ceiling (peak RSS, one shared provider key), never a scientific one.
MAX_CELLS_IN_FLIGHT = 4


def _inner_cells(stores: Stores, experiment: Mapping[str, Any] | None) -> InnerCells:
    if experiment is None:
        raise PayloadInvalidError(
            "an outer dataset's graph and identity derive from its panel, "
            "which this box does not hold.",
            code="pipeline_config_invalid",
        )
    return parse_resolved(experiment).panel.resolve_cells(stores)


def _pipeline_declaration(stores: Stores, experiment: Mapping[str, Any] | None) -> dict[str, Any]:
    return _inner_cells(stores, experiment).pipeline()


def _identity_config(
    stores: Stores, experiment: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """What reads every cell's number alike; what measures ONE cell is in its pin (`InnerTaskSpec.key`)."""
    cells = _inner_cells(stores, experiment)
    return {cells.chain[0]: {INNER_ORIGIN_KEY: cells.estimator}}


def promptpotter_wire_adapter(
    sample: Sample,
    pipeline_params: PipelineParams,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"query": sample.query}

    optimizer_prompt_overrides = {k: v for k, v in pipeline_params.laid_nodes.items() if v}
    if optimizer_prompt_overrides:
        payload["optimizer_prompt_overrides"] = optimizer_prompt_overrides

    return payload


def _resolve_panel(stores: Stores, panel: Mapping[str, Any]) -> dict[str, Any]:
    """Each cell's instrument beside the panel; only what was declared survives the dump, so a
    field that gains a default re-keys no banked cell."""
    parsed = parse_panel(panel)
    cells = parsed.resolve_cells(stores)
    resolved = ResolvedPanel(
        panel=parsed,
        instruments={t.id: cells.spec(t).key() for t in parsed.tasks},
        datasets={t.id: parsed.dataset_for(t) for t in parsed.tasks},
    )
    return resolved.model_dump(mode="json", exclude_unset=True)


def _extract_experiment(experiment_data: dict[str, Any]) -> list[dict[str, Any]]:
    """``ground_truth`` is ``None``: an L4 cell is graded by its outer proxies, never a label."""
    return parse_resolved(experiment_data).samples()


async def _in_process_run(
    _workload: InProcessWorkload, sample: Sample, payload: dict[str, Any]
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
    extract_experiment=_extract_experiment,
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
    resolve_experiment=_resolve_panel,
    # The outer's graph IS the inner optimizer's, so it is served here, never mirrored in a file.
    pipeline_declaration=_pipeline_declaration,
    prompt_fields_as_node_params=True,
    identity_config=_identity_config,
    self_optimization=True,
)
