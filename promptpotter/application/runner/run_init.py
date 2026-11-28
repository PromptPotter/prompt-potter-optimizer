"""Run init, one module in one order: a held run crosses its boundary (`enter_cycle`), the launch
opens (`open_launch`: clock, control, the held budget and its book, the scorer, the origin), then
each cycle its line runs on opens (`open_cycle`: the cycle's records, preflight and INIT, the replay
and its fork) and closes INIT (`close_init`). A rebase's fork re-arms the launch's book on its own
cycle (`onto_fork`)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.resume import resume_with_divergence_check
from promptpotter.application.initialization.scoring_scope import cycle_partition, scorer_setup
from promptpotter.application.initialization.session import LaunchClock, Session
from promptpotter.application.intelligence.earned_blocks import CampaignSurvey
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.jobs.launcher.opening import open_spend_book
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.jobs.shares import move_campaign_money, stand_held_budget
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.origin import CampaignOrigin, seat_campaign_origin
from promptpotter.application.preflight import run_preflight_checks
from promptpotter.application.run_observers import (
    RunObservers,
    build_run_observers,
    declare_run_wiring,
)
from promptpotter.application.run_phase_control import FlightGauge, RunControl, close_dead_bench
from promptpotter.application.runner.inner.tasks import retarget_inner_spawn
from promptpotter.application.scoring.query_loop import NEXT_CELL
from promptpotter.application.scoring.sample_measurement import cell_bound, measured_cell_price
from promptpotter.application.views.readout import StatusFn
from promptpotter.domain.campaign import CampaignCycle, ceiling_meter
from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.inner_origin import inner_origin_of
from promptpotter.domain.phase_acts import CellFormula, InitClosed, InitOpened
from promptpotter.domain.phases import CampaignPhase, StopReason
from promptpotter.domain.records.run_phase import ArmsGate, PhaseRecord, Running, RunPhaseRecord
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.backend import CELL
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.pricing import refresh_rates
from promptpotter.infrastructure.llm.spend_book import filed
from promptpotter.infrastructure.llm.telemetry import append_record
from promptpotter.infrastructure.run_scope import rescope, run_scope
from promptpotter.infrastructure.store.campaign_store.ledger_scan import CycleFactsFold
from promptpotter.infrastructure.store.read_model import LedgerIndex
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.infrastructure.store.stores import Stores

if TYPE_CHECKING:
    from promptpotter.application.bench.resume_and_fork.fork_siblings import ForkCut


@dataclass(frozen=True)
class PreparedRun:
    """No ceiling of its own: the held one is SET INTO ``campaign_config``, so the value that halts
    is served. ``dataset`` is the search split the line's cycles walk; ``resumed_ending`` the stop
    the launch resumed, read before run init can move the session onto a fork."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig
    dataset: list[Sample]
    resumed_ending: StopReason | None
    # The launch's one selection, which every cycle of its line runs.
    optimizer: SelectedOptimizer


def start_launch(session: Session) -> None:
    """The launch's clock and control, fresh, keeping only the cycles it measures FOR."""
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
    optimizer: SelectedOptimizer,
) -> PreparedRun:
    """*bank* is the whole bank; the launch walks the split its cycle drew."""
    campaign_config = cycle.config
    partition = cycle_partition(session, bank, campaign_config)
    dataset = list(partition.search)
    # Before this launch takes a cell: a re-read of a cell the campaign priced prices nothing.
    campaigns = session.store.campaigns
    rescope(priced=campaigns.campaign_root_dir(session.campaign_id))
    await asyncio.to_thread(refresh_rates)
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
    declare_run_wiring(session, campaign_config, selected=optimizer)
    # A fresh mint was admitted before its campaign existed: its ceiling is stored here, off the
    # loop, under the gate.
    await asyncio.to_thread(
        move_campaign_money,
        budget.standing,
        asked=budget.standing,
        stores=session.store,
        job_registry=JobRegistry.attach(session.store.jobs_dir),
        campaign=session.bound_campaign,
    )
    stand_held_budget(session.store, session.hop, budget)

    # After the declaration, and before the origin pass, which spends.
    arm_run_controls(session, observers)

    session.state.scoring = scorer_setup(session, campaign_config, partition=partition)
    if (book := run_scope().book) is not None and (
        cell := await cell_bound(session, session.pipeline_params)
    ) is not None:
        # After the scorer, which the stop this raises is finalized with.
        book.refuse_unpriced(
            cell,
            filed(CELL).kind,
            NEXT_CELL,
            session.backend_client.cells_paid_by(session.pipeline_params),
        )
    # The launch's one session open, before round 0's door sends a cell.
    await session.backend_client.init_session(session.bank.samples, session.answer_terms)
    return PreparedRun(
        origin=seat_campaign_origin(session, dataset, campaign_config, cycle=cycle),
        campaign_config=campaign_config,
        dataset=dataset,
        resumed_ending=LedgerIndex.of(campaigns.ledger(session.hop))
        .view(CycleFactsFold)
        .resumed_ending,
        optimizer=optimizer,
    )


def arm_run_controls(session: Session, observers: RunObservers) -> None:
    """Always armed, caps or none, before origin scoring spends; a fork arms its own cycle dir."""
    if session.state.held is None:
        raise RuntimeError("a run's book is armed only over what admission held for it")
    session.state.flight = FlightGauge()
    book = open_spend_book(
        session.state.held,
        stores=session.store,
        hop=session.hop,
        meters=ceiling_meter(session.purpose),
        opened=session.state.launch_clock().monotonic,
        live=True,
    )
    observers.arm_spend_book(book)
    store = session.store
    session.state.control = replace(
        session.state.control,
        commands=store.commands,
        cycle=store.subject(session.hop),
        hold=observers.hold.control.hold,
    )
    # The rate-limit countdown polls it too — the one blocking seam that otherwise ignores a pause.
    rescope(abort_check=session.state.control.pause_requested)


def enter_cycle(
    session: Session,
    *,
    readout_sink: StatusFn | None = None,
    forked_from: RunObservers | None = None,
) -> RunObservers:
    """THE run boundary on *session*'s cycle, a launch's and a fork's: its observers bound, the
    cycle declared running, its inbox voided and its pointer the latest."""
    observers = build_run_observers(
        session=session,
        readout_sink=readout_sink,
        forked_from=forked_from,
    )
    store = session.store
    try:
        prior = store.campaigns.last(session.hop, RunPhaseRecord)
        # Any declaration after a `terminal` one retires that ending (`ledger_scan.py::_CycleFacts`).
        store.campaigns.append(session.hop, RunPhaseRecord(state=Running()))
        # A launch is a run boundary, sparing what was asked of its own hold, a QUEUED claim's
        # included; the run a gate held resumes, what it was asked standing.
        if prior is None or not isinstance(prior.state, ArmsGate):
            store.void_inbox(session.hop, sparing=observers.hold.control.hold)
        # The latest launch is the latest run held: a mint, a fork or a resume, from every door.
        save_active_pointer(store.base_dir, session.hop)
    except BaseException:
        observers.release()
        raise
    session.state.observers = observers
    close_dead_bench(store, session.hop)
    return observers


def _rebased_observers(session: Session, cut: ForkCut, observers: RunObservers) -> RunObservers:
    # The cut's last step: everything the fork stands on, its share included
    # (`fork_siblings.py::cut_run_fork`), landed before it supersedes its parent.
    cut.take(session.store)
    return enter_cycle(session, forked_from=observers)


async def onto_fork(
    session: Session, cut: ForkCut, parent: RunObservers, *, prep: PreparedRun
) -> tuple[Session, RunObservers]:
    """THE in-process move onto a fork, a rebase's and a divergence's: the take, the bind, the
    held share, the wiring and *parent*'s release. Taken and rebased off the loop: both wait
    on a gate."""
    session = session.bound(CampaignCycle(session.bound_campaign, cut.child.cycle_id))
    observers = await asyncio.to_thread(_rebased_observers, session, cut, parent)
    # A fork's config is already admitted, so it is wired as its observers open.
    declare_run_wiring(session, prep.campaign_config, selected=prep.optimizer)
    # The line's every emit lands on the cycle it moved onto, under the launch's one scope.
    rescope(ledger=CycleEventLog.open(observers.cycle_dir))
    arm_run_controls(session, observers)
    # Held through the fork's mint, which moves the campaign's holds off it.
    parent.release()
    return session, observers


def _init_cycle(session: Session, *, resume_from_round_override: int | None) -> None:
    """A present-but-BROKEN cycle propagates: swallowing it re-spends the campaign from scratch."""
    store = session.store.campaigns
    hop = session.hop
    # Before anything that can raise: a fork's state is its parent's until this line.
    session.state.resumed_from_round = (
        _next_round(session.store, hop)
        if resume_from_round_override is None
        else store.rewind_enters(hop, resume_from_round_override)
    )
    # Restated on every launch: the backend may have moved its axes since.
    session.record_resolved_run()
    store.write_optimized_surface(
        hop,
        session.bound_resolution.chain.value_tree(
            prompt_delivery=session.backend_client.connector.prompt_delivery(
                session.pipeline_params
            )
        ),
    )
    existing = store.load(hop, moment=None)
    if existing is not None:
        session.state.human_intervened = existing.human_intervened


def _next_round(stores: Stores, hop: CycleHop) -> int:
    """The round NUMBER after the highest one closed, never a count: the origin is round 0."""
    last = stores.campaigns.standing_rounds(hop, moment=None).last_closed
    return 1 if last is None else last + 1


def _campaigns_on_origin(session: Session, survey: CampaignSurvey) -> list[str]:
    own = next((c for c in survey.campaigns if c.campaign_id == session.campaign_id), None)
    if own is None:
        return []
    # One origin graded under another identity is another measurement, never the identical one.
    return sorted(
        other.campaign_id
        for other in survey.campaigns
        if other.campaign_id != own.campaign_id
        and other.root_content_hash == own.root_content_hash
        and (drawn := survey.drawn(other.root_hop)) is not None
        and drawn.cell_identity == session.cell_identity
    )


def _banked_instruments(session: Session, survey: CampaignSurvey) -> list[str]:
    banked: list[str] = []
    for campaign in survey.campaigns:
        if campaign.dataset_name != session.measured_dataset:
            continue
        if (drawn := survey.drawn(campaign.root_hop)) is not None:
            banked.append(drawn.instrument_id)
    return banked


async def _enter_init(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    dataset: list[Sample],
    session: Session,
    origin: CampaignOrigin,
    survey: CampaignSurvey,
) -> None:
    bound = await cell_bound(session, session.pipeline_params)
    measured = measured_cell_price(
        session.store, session.bank.dataset_hash, session.pipeline_params
    )
    # An L4 cell's instrument names its inner origin; another's is its bank alone.
    inner = inner_origin_of(session.pipeline_params.keyed_by(session.cell_identity))
    instrument = None if inner is None else session.instrument_id
    warnings = run_preflight_checks(
        config,
        selected,
        session,
        dataset,
        framing=origin.framing,
        cell_usd=None if bound is None else bound.usd,
        measured_cell_usd=None if measured is None else measured.usd,
        origin_campaigns=_campaigns_on_origin(session, survey),
        instrument=instrument,
        banked_instruments=[] if instrument is None else _banked_instruments(session, survey),
        unread=survey.faulted,
    )
    append_record(
        PhaseRecord(
            phase=CampaignPhase.INIT,
            step="enter",
            act=InitOpened(warnings=warnings, formula=CellFormula.of(session.state.scoring.scorer)),
        )
    )


async def open_cycle(
    prep: PreparedRun,
    *,
    session: Session,
    mode: RunMode,
) -> tuple[Cycle, ForkCut | None]:
    """The cycle holds the session it runs on, and beside it the fork a divergence cut, untaken.

    INIT stays open: its caller moves onto a fork first (`onto_fork`), then closes it (`close_init`).
    """
    origin, dataset, config = prep.origin, prep.dataset, prep.campaign_config
    selected = prep.optimizer
    _init_cycle(session, resume_from_round_override=mode.from_round)
    # The one cross-campaign read of this cycle: preflight's and every round's earned blocks.
    survey = CampaignSurvey(session.store.campaigns)
    await _enter_init(config, selected, dataset, session, origin, survey)
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
    # Off the loop: a divergence cuts under the admission gate.
    cut = await asyncio.to_thread(resume_with_divergence_check, cycle, mode) if replays else None
    if cut is not None:
        # Off the fork's own chain: one cut at round 0 closes the origin again, then runs round 1.
        session.state.resumed_from_round = _next_round(session.store, cut.child)
    return cycle, cut


def close_init(cycle: Cycle) -> None:
    """On the scope's ledger, which a fork rescoped: INIT exits on the cycle the loop runs."""
    session = cycle.session
    cycle.sample_index = SampleIndex.ensure_for(
        session.store,
        scorer=session.state.scoring.scorer,
        bank=session.bank,
        sample_ids=session.state.scoring.partition.admitted_ids,
    )
    partition = session.state.scoring.partition
    # A RUNNING cycle's readout reads its formula off this bracket (`scorer_cell_formula`).
    append_record(
        PhaseRecord(
            phase=CampaignPhase.INIT,
            step="exit",
            act=InitClosed(
                origin_fitness=None if (fold := cycle.origin_round.fold) is None else fold.fitness,
                cycle_id=session.cycle_id,
                samples=len(partition.search),
                bench_samples=len(partition.bench),
                resumed_from_round=session.state.resumed_from_round,
                # Round 0 is built fresh by `Cycle.start`, so only later rounds count as cached.
                cached_rounds=sum(1 for rr in cycle.rounds if rr.round > 0),
                framing_keys=len(cycle.framing),
                formula=CellFormula.of(session.state.scoring.scorer),
            ),
        )
    )
    # The cycle id is FINAL only here; the spawn context was published before a fork could move it.
    retarget_inner_spawn(session)
