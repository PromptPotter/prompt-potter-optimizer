from __future__ import annotations

from dataclasses import replace

from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.admission import open_spend_book
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.run_observers import RunObservers
from promptpotter.application.run_phase_control import FlightGauge
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.spend import CYCLE_AXES, Budget, HeldBudget
from promptpotter.infrastructure.llm.send_pacing import set_abort_check
from promptpotter.infrastructure.store.stores import Stores


def arm_run_controls(session: Session, observers: RunObservers) -> None:
    """Always armed, caps or none, before origin scoring spends; a fork arms its own cycle dir."""
    if session.held is None:
        raise RuntimeError("a run's book is armed only over what admission held for it")
    session.flight = FlightGauge(observers.callbacks.on_flight)
    book = open_spend_book(
        session.held,
        stores=session.store,
        job_registry=JobRegistry.attach(),
        hop=session.hop if session.state.cycle_id else None,
        meters=ceiling_meter(session.purpose),
        opened=session.launched,
        live=True,
    )
    observers.arm_spend_book(book)
    session.control = replace(session.control, cycle_dir=book.cycle_dir, book=book)
    # The rate-limit countdown polls it too — the one blocking seam that otherwise ignores a pause.
    set_abort_check(session.control.pause_requested)


def stand_held_budget(stores: Stores, hop: CycleHop, held: HeldBudget) -> None:
    """This cycle's own axes; its money is the campaign's (``move_campaign_money``), its hold the job's."""
    cycle = held.standing.only(CYCLE_AXES)
    if stores.campaigns.read_standing_budget(hop).budget != cycle:
        stores.campaigns.write_standing_budget(hop, cycle, pause_at_round=None)


def rebase_held(session: Session, parent: CycleHop) -> None:
    """The job follows the fork, and its share stops counting *parent*'s metered spend, which the campaign's holds."""
    book, held = session.control.book, session.held
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
    if not session.state.cycle_id:
        return
    campaigns = session.store.campaigns
    standing = campaigns.read_standing_budget(session.hop)
    pause_at_round = (
        None if step_rounds is None else max(session.state.resumed_from_round - 1, 0) + step_rounds
    )
    if standing.pause_at_round != pause_at_round:
        campaigns.write_standing_budget(session.hop, standing.budget, pause_at_round=pause_at_round)


__all__ = ["arm_run_controls", "declare_round_allowance", "rebase_held", "stand_held_budget"]
