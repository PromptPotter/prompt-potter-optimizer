from __future__ import annotations

from dataclasses import dataclass, replace

from promptpotter.application.campaign_config import CampaignConfig, apply_cycle_seed
from promptpotter.application.initialization.loop_start import populate_session_scoring
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.admission import open_spend_book
from promptpotter.application.jobs.quota import move_campaign_money
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.origin import (
    CampaignOrigin,
    establish_campaign_origin,
)
from promptpotter.application.run_observers import RunObservers, declare_run_wiring
from promptpotter.application.run_phase_control import FlightGauge
from promptpotter.application.scoring.query_loop import NEXT_CELL
from promptpotter.application.scoring.sample_measurement import cell_bound
from promptpotter.domain.bench import BankPartition
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.pipeline_overlay import (
    overlay_sets_model_outside_allowed,
    permitted_models_from_narrowing,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.spend import CYCLE_AXES, Budget, HeldBudget
from promptpotter.infrastructure.llm.send_pacing import set_abort_check
from promptpotter.infrastructure.llm.spend_book import bound_spend_book
from promptpotter.infrastructure.store.stores import Stores


@dataclass(frozen=True)
class PreparedRun:
    """No ceiling of its own: the held one is SET INTO ``campaign_config``, so the value that halts is served."""

    origin: CampaignOrigin
    campaign_config: CampaignConfig


async def prepare_run(
    dataset: list[Sample],
    campaign_config: CampaignConfig,
    *,
    session: Session,
    partition: BankPartition,
    observers: RunObservers,
    budget: HeldBudget,
    langfuse_session_id: str | None,
) -> PreparedRun:
    cb = observers.callbacks

    seed = session.store.campaigns.read_cycle_seed(session.hop)
    if seed is not None:
        campaign_config = apply_cycle_seed(campaign_config, seed)
        if overlay_sets_model_outside_allowed(
            seed.pipeline_overlay,
            permitted_models_from_narrowing(campaign_config.optimizer_narrowing),
        ):
            # Stamped here: the mint seam cannot, the index being created at init.
            session.record_intervention("disallowed_model_override")

    # LAST, after the seed's other knobs; SET, never a `min`: admission already layered seed and standing.
    campaign_config = campaign_config.model_copy(
        update={
            "optimization": campaign_config.optimization.model_copy(
                update={"budget": budget.admitted}
            )
        }
    )
    session.state.held = budget
    # Declared before origin scoring; the record is the readout, the arming below the enforcement.
    observers.tracing.start(
        config_snapshot=campaign_config.model_dump(mode="json"),
        dataset=dataset,
        langfuse_session_id=langfuse_session_id,
    )
    declare_run_wiring(session, campaign_config, cb.ledger, tracing=observers.tracing)
    # A fresh mint was admitted before its campaign existed: its ceiling is stored here.
    move_campaign_money(
        budget.standing,
        stores=session.store,
        job_registry=JobRegistry.attach(),
        campaign_id=session.hop.campaign_id,
    )
    stand_held_budget(session.store, session.hop, budget)

    # After the declaration, and before the origin pass, which spends.
    arm_run_controls(session, observers)

    populate_session_scoring(session, campaign_config, source=RunSource.ORIGIN, partition=partition)
    if (book := bound_spend_book()) is not None and (
        cell := await cell_bound(session, session.pipeline_params)
    ) is not None:
        # After the scorer, which the stop this raises is finalized with.
        book.refuse_unpriced(cell, NEXT_CELL)
    origin = await establish_campaign_origin(
        session,
        dataset,
        campaign_config,
        seed=seed,
        listener=cb,
    )
    if origin.locked_scoring is not None:
        campaign_config = campaign_config.model_copy(update={"scoring": origin.locked_scoring})

    return PreparedRun(origin=origin, campaign_config=campaign_config)


def arm_run_controls(session: Session, observers: RunObservers) -> None:
    """Always armed, caps or none, before origin scoring spends; a fork arms its own cycle dir."""
    if session.state.held is None:
        raise RuntimeError("a run's book is armed only over what admission held for it")
    session.state.flight = FlightGauge(observers.callbacks.on_flight)
    book = open_spend_book(
        session.state.held,
        stores=session.store,
        job_registry=JobRegistry.attach(),
        hop=session.hop,
        meters=ceiling_meter(session.purpose),
        opened=session.state.launch_clock().monotonic,
        live=True,
    )
    observers.arm_spend_book(book)
    session.state.control = replace(session.state.control, cycle_dir=book.cycle_dir, book=book)
    # The rate-limit countdown polls it too — the one blocking seam that otherwise ignores a pause.
    set_abort_check(session.state.control.pause_requested)


def stand_held_budget(stores: Stores, hop: CycleHop, held: HeldBudget) -> None:
    """This cycle's own axes; its money is the campaign's (``move_campaign_money``), its hold the job's."""
    cycle = held.standing.only(CYCLE_AXES)
    if stores.campaigns.read_standing_budget(hop).budget != cycle:
        stores.campaigns.write_standing_budget(hop, cycle, pause_at_round=None)


def rebase_held(session: Session, parent: CycleHop) -> None:
    """The job follows the fork, and its share stops counting *parent*'s metered spend, which the campaign's holds."""
    book, held = session.state.control.book, session.state.held
    if book is None or held is None:
        raise RuntimeError("a run rebases only once its book is armed")
    spent = session.store.campaigns.cycle_metered(parent, book.meters)
    job_registry = JobRegistry.attach()
    with job_registry.admission_gate():
        job = job_registry.get(held.job_id)
        if job is None:
            raise RuntimeError(f"a run rebases only on its own job, and {held.job_id} is gone")
        live = job.allotment
        allotment = Budget(
            usd=None if live.usd is None else max(0.0, live.usd - spent.usd_amount),
            tokens=None if live.tokens is None else max(0, live.tokens - spent.token_amount),
        )
        job_registry.update_target(job.job_id, hop=session.hop)
        job_registry.set_held(job.job_id, job.reserve, allotment)
    stand_held_budget(session.store, session.hop, held)


def declare_round_allowance(session: Session, step_rounds: int | None) -> None:
    """A launch with no allowance clears the last launch's."""
    campaigns = session.store.campaigns
    standing = campaigns.read_standing_budget(session.hop)
    pause_at_round = (
        None if step_rounds is None else max(session.state.resumed_from_round - 1, 0) + step_rounds
    )
    if standing.pause_at_round != pause_at_round:
        campaigns.write_standing_budget(session.hop, standing.budget, pause_at_round=pause_at_round)
