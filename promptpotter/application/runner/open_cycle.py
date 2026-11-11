"""Run init, one module in one order: the launch opens (`open_launch`: clock, control, the held
budget and its book, the scorer, the origin), then each cycle its line runs on opens
(`open_cycle`: the cycle's records, preflight and INIT, the replay and its fork) and closes INIT
(`close_init`). A rebase's fork re-arms the launch's book on its own cycle (`onto_fork`)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.resume import (
    resume_with_divergence_check,
)
from promptpotter.application.initialization.scoring_scope import cycle_partition, scorer_setup
from promptpotter.application.initialization.session import LaunchClock, Session
from promptpotter.application.intelligence.earned_blocks import CampaignSurvey
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.jobs.launcher.admission import open_spend_book
from promptpotter.application.jobs.quota import cycle_share, land_job, move_campaign_money
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.optimizer_manifest import bound_optimizer
from promptpotter.application.origin import CampaignOrigin, establish_campaign_origin
from promptpotter.application.preflight import run_preflight_checks
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
    declare_run_wiring,
)
from promptpotter.application.run_phase_control import FlightGauge, RunControl
from promptpotter.application.runner.inner.spawn_context import retarget_inner_spawn
from promptpotter.application.scoring.query_loop import NEXT_CELL
from promptpotter.application.scoring.sample_measurement import cell_bound, measured_cell_price
from promptpotter.application.views.ingress import init_enter, init_exit
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.inner_origin import inner_origin_of
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import CampaignPhase
from promptpotter.domain.run_records import StandingBudgetRecord
from promptpotter.domain.spend import CYCLE_AXES, Budget, HeldBudget
from promptpotter.infrastructure.backend import CELL
from promptpotter.infrastructure.llm.pricing import refresh_rates_in_background
from promptpotter.infrastructure.llm.send_pacing import set_abort_check
from promptpotter.infrastructure.llm.spend_book import bound_spend_book, filed
from promptpotter.infrastructure.llm.telemetry import bind_priced
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_priced_keys
from promptpotter.shared.run_scope import rescope

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
    from promptpotter.domain.run_mode import RunMode
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedRun:
    """No ceiling of its own: the held one is SET INTO ``campaign_config``, so the value that halts
    is served. ``dataset`` is the search split the line's cycles walk."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig
    dataset: list[Sample]


def start_launch(session: Session) -> None:
    """The launch's clock and its control, fresh, keeping only the cycles it measures FOR."""
    session.state.clock = LaunchClock.now()
    session.state.control = RunControl(
        commands=session.store.commands,
        enclosing=session.state.control.enclosing,
        acting=session.state.control.acting,
    )


async def open_launch(
    bank: list[Sample],
    cycle: CycleConfig,
    *,
    session: Session,
    observers: RunObservers,
    budget: HeldBudget,
) -> PreparedRun:
    """*bank* is the whole bank; the launch walks the split its cycle drew."""
    cb = observers.callbacks
    campaign_config = cycle.config
    partition = cycle_partition(session, bank, campaign_config)
    dataset = list(partition.search)
    # Before this launch takes a cell: a re-read of a cell the campaign priced prices nothing.
    campaigns = session.store.campaigns
    bind_priced(scan_ledger_priced_keys(campaigns.campaign_cycle_ledgers(session.campaign_id)))
    refresh_rates_in_background()
    # SET, never a `min`: admission already layered seed and standing.
    campaign_config = campaign_config.model_copy(
        update={
            "optimization": campaign_config.optimization.model_copy(
                update={"budget": budget.admitted}
            )
        }
    )
    session.state.held = budget
    # Declared before origin scoring; the record is the readout, the arming below the enforcement.
    declare_run_wiring(session, campaign_config, cb.ledger, selected=bound_optimizer())
    # A fresh mint was admitted before its campaign existed: its ceiling is stored here.
    move_campaign_money(
        budget.standing,
        stores=session.store,
        job_registry=JobRegistry.attach(session.store.jobs_dir),
        campaign_id=session.hop.campaign_id,
    )
    stand_held_budget(session.store, session.hop, budget)

    # After the declaration, and before the origin pass, which spends.
    arm_run_controls(session, observers)

    session.state.source = RunSource.ORIGIN
    session.state.scoring = scorer_setup(session, campaign_config, partition=partition)
    if (book := bound_spend_book()) is not None and (
        cell := await cell_bound(session, session.pipeline_params)
    ) is not None:
        # After the scorer, which the stop this raises is finalized with.
        book.refuse_unpriced(
            cell, filed(CELL).kind, NEXT_CELL, session.backend_client.connector.cell_payer
        )
    # The launch's one push of the term index, before the origin walk sends a cell.
    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)
    origin = await establish_campaign_origin(
        session, dataset, campaign_config, cycle=cycle, listener=cb
    )
    if origin.locked_scoring is not None:
        campaign_config = campaign_config.model_copy(update={"scoring": origin.locked_scoring})
    return PreparedRun(origin=origin, campaign_config=campaign_config, dataset=dataset)


def arm_run_controls(session: Session, observers: RunObservers) -> None:
    """Always armed, caps or none, before origin scoring spends; a fork arms its own cycle dir."""
    if session.state.held is None:
        raise RuntimeError("a run's book is armed only over what admission held for it")
    session.state.flight = FlightGauge(observers.callbacks.on_flight)
    book = open_spend_book(
        session.state.held,
        stores=session.store,
        job_registry=JobRegistry.attach(session.store.jobs_dir),
        hop=session.hop,
        meters=ceiling_meter(session.purpose),
        opened=session.state.launch_clock().monotonic,
        live=True,
    )
    observers.arm_spend_book(book)
    store = session.store
    session.state.control = replace(
        session.state.control, commands=store.commands, cycle=store.subject(session.hop)
    )
    # The rate-limit countdown polls it too — the one blocking seam that otherwise ignores a pause.
    set_abort_check(session.state.control.pause_requested)


def stand_held_budget(stores: Stores, hop: CycleHop, held: HeldBudget) -> None:
    """This cycle's own axes; its money is the campaign's (``move_campaign_money``), its hold the job's."""
    cycle = held.standing.only(CYCLE_AXES)
    if stores.campaigns.read_standing_budget(hop).budget != cycle:
        stores.campaigns.append(hop, StandingBudgetRecord(budget=cycle))


def _rebase_held(session: Session, parent: CycleHop) -> None:
    """The job follows the fork, and its share stops counting *parent*'s metered spend, which the campaign's holds."""
    held = session.state.held
    if held is None:
        raise RuntimeError("a run rebases only over what admission held for it")
    spent = session.store.campaigns.cycle_metered(parent, ceiling_meter(session.purpose))
    job_registry = JobRegistry.attach(session.store.jobs_dir)
    with job_registry.admission_gate():
        job = job_registry.get(held.job_id)
        if job is None:
            raise RuntimeError(f"a run rebases only on its own job, and {held.job_id} is gone")
        live = cycle_share(session.store, job)
        allotment = Budget(
            usd=None if live.usd is None else max(0.0, live.usd - spent.usd_amount),
            tokens=None if live.tokens is None else max(0, live.tokens - spent.token_amount),
        )
        land_job(session.store, job_registry, job.job_id, session.hop, allotment)
    stand_held_budget(session.store, session.hop, held)


def onto_fork(
    session: Session,
    parent: CycleHop,
    observers: RunObservers,
    *,
    campaign_config: CampaignConfig,
) -> RunObservers:
    _rebase_held(session, parent)
    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        forked_from=observers,
    )
    # The line's every emit lands on the cycle it moved onto, under the launch's one scope.
    rescope(ledger=observers.callbacks.ledger)
    arm_run_controls(session, observers)
    return observers


def declare_round_allowance(session: Session, step_rounds: int | None) -> None:
    """A launch with no allowance clears the last launch's."""
    campaigns = session.store.campaigns
    standing = campaigns.read_standing_budget(session.hop)
    pause_at_round = (
        None if step_rounds is None else max(session.state.resumed_from_round - 1, 0) + step_rounds
    )
    if standing.pause_at_round != pause_at_round:
        campaigns.append(
            session.hop,
            StandingBudgetRecord(budget=standing.budget, pause_at_round=pause_at_round),
        )


def _init_cycle(session: Session, *, resume_from_round_override: int | None) -> None:
    """A present-but-BROKEN cycle propagates: swallowing it re-spends the campaign from scratch."""
    store = session.store.campaigns
    hop = session.hop
    # Before anything that can raise: a fork's state is its parent's until this line.
    session.state.resumed_from_round = (
        _next_round(session)
        if resume_from_round_override is None
        else store.rewind_enters(hop, resume_from_round_override)
    )
    # Restated on every launch: the backend may have moved its axes since.
    session.record_resolved_run()
    store.write_optimized_surface(
        hop,
        session.pipeline_schema.value_tree(
            prompt_delivery=session.backend_client.connector.prompt_delivery(
                session.pipeline_params
            )
        ),
    )
    existing = store.load(hop, moment=None)
    if existing is not None:
        session.state.human_intervened = existing.human_intervened


def _next_round(session: Session) -> int:
    """The round NUMBER after the highest one closed, never a count: the origin is round 0."""
    last = session.store.campaigns.standing_rounds(session.hop, moment=None).last_closed
    return 1 if last is None else last + 1


def _campaigns_on_origin(session: Session, survey: CampaignSurvey) -> list[str]:
    own = next((c for c in survey.campaigns if c.campaign_id == session.campaign_id), None)
    if own is None:
        return []
    return sorted(
        other.campaign_id
        for other in survey.campaigns
        if other.campaign_id != own.campaign_id and other.root_content_hash == own.root_content_hash
    )


def _banked_instruments(session: Session, survey: CampaignSurvey) -> list[str]:
    banked: list[str] = []
    for campaign in survey.campaigns:
        if campaign.dataset_name != session.measured_dataset:
            continue
        standing = survey.standing(campaign.root_hop)
        origin = None if standing is None else standing.rounds.get(0)
        instrument = (
            None
            if origin is None
            else inner_origin_of(origin.close.origin.individual.pipeline_params)
        )
        if instrument is not None:
            banked.append(instrument)
    return banked


async def _enter_init(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    dataset: list[Sample],
    cb: RunCallbacks,
    session: Session,
    origin: CampaignOrigin,
    survey: CampaignSurvey,
) -> None:
    target_node_configs = list(session.pipeline_params.nodes.items())
    target_models = tuple(v.model for _, v in target_node_configs if v.model)

    bound = await cell_bound(session, session.pipeline_params)
    measured = measured_cell_price(session, session.pipeline_params)
    instrument = inner_origin_of(session.pipeline_params)
    preflight_warnings = run_preflight_checks(
        config,
        selected,
        dataset,
        target_models,
        framing=origin.framing,
        cell_usd=None if bound is None else bound.usd,
        measured_cell_usd=None if measured is None else measured.usd,
        origin_campaigns=_campaigns_on_origin(session, survey),
        instrument=instrument,
        banked_instruments=[] if instrument is None else _banked_instruments(session, survey),
    )
    for w in preflight_warnings:
        logger.warning("preflight[%s]: %s — %s", w.code, w.title, w.detail)
    cb.on_phase(
        CampaignPhase.INIT,
        "enter",
        view=init_enter(
            cb.view_context,
            config=config,
            dataset=dataset,
            session=session,
            warnings=preflight_warnings,
        ),
    )


async def open_cycle(
    prep: PreparedRun,
    *,
    session: Session,
    cb: RunCallbacks,
    mode: RunMode,
) -> tuple[Cycle, CycleHop | None]:
    """The cycle holds the session it runs on, and beside it the cycle a divergence forked it from.

    INIT stays open: its caller moves onto a fork first (`onto_fork`), then closes it (`close_init`).
    """
    origin, dataset, config = prep.origin, prep.dataset, prep.campaign_config
    selected = bound_optimizer()
    _init_cycle(session, resume_from_round_override=mode.from_round)
    # The one cross-campaign read of this cycle: preflight's and every round's earned blocks.
    survey = CampaignSurvey(session.store.campaigns)
    await _enter_init(config, selected, dataset, cb, session, origin, survey)
    # A round the ledger closed, the origin's included, is replayed, never closed again; a rewind
    # keeps the round it rewinds to, so it always replays and is written once the replay proceeds.
    replays = (
        session.store.campaigns.standing_rounds(session.hop, moment=None).last_closed is not None
    )
    cycle = Cycle.start(
        origin.origin,
        framing=origin.framing,
        session=session,
        config=config,
        optimizer=selected,
        survey=survey,
    )
    forked_from: CycleHop | None = None
    if replays:
        fork = await resume_with_divergence_check(
            cycle,
            dataset,
            cb,
            skip_divergence_check=mode.no_divergence_check,
            fork_on_divergence=mode.fork_on_divergence,
            rewind=mode.from_round,
        )
        if fork is not None:
            forked_from = session.hop
            cycle.session = session.bound(
                CycleHop(campaign_id=session.campaign_id, cycle_id=fork), purpose=session.purpose
            )
            # Off the fork's own chain: one cut at round 0 closes the origin again, then runs round 1.
            cycle.session.state.resumed_from_round = _next_round(cycle.session)
    return cycle, forked_from


def close_init(cycle: Cycle, cb: RunCallbacks) -> None:
    """*cb* is the cycle's own: INIT exits on the cycle the loop runs, a fork's included."""
    session = cycle.session
    cycle.sample_index = SampleIndex.ensure_for(
        session.store,
        scorer=session.state.scoring.scorer,
        bank=session.bank,
        sample_ids=session.state.scoring.partition.admitted_ids,
    )
    # A RUNNING cycle's `log.md` reads its formula off this bracket (`scorer_cell_formula`).
    cb.on_phase(
        CampaignPhase.INIT,
        "exit",
        view=init_exit(cb.view_context, cycle=cycle, session=session),
    )
    # The cycle id is FINAL only here; the spawn context was published before a fork could move it.
    retarget_inner_spawn(session)
