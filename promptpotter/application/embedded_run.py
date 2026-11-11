"""The embedded launch entry: a host program drives one campaign in its own loop, holding a share
of its ceiling and no machine slot."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import load_dataset_campaign_config
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    campaign_at,
    probe_backend,
    refuse_campaign,
    slotless_share,
)
from promptpotter.application.jobs.launcher.head_to_head import DatasetArms, admit_head_to_head
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle
from promptpotter.application.jobs.launcher.run_job import run_held_job
from promptpotter.application.jobs.mint import (
    OpenedCycle,
    declare,
    fresh_campaign_id,
    mint_framed_cycle,
)
from promptpotter.application.jobs.quota import land_job, sends_on
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.application.maintenance.landing import land_at_entry
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.views.readout import StatusFn
from promptpotter.config.logging import setup_logging
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.campaign_config import effective_config
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import EmbeddedRun
from promptpotter.domain.results import CycleResult
from promptpotter.domain.sample import Bank
from promptpotter.domain.spend import Budget, HeldBudget
from promptpotter.infrastructure.store.dataset_access import resolve_dataset, runnable_definition
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.campaign import RunDeclaration
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.domain.run_mode import RunMode
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores

# This module IS the embedded surface: a capability it does not name is one a host cannot find.
# Every verb but the run is a command the host files through `host_dispatcher`, as every door does.
__all__ = [
    "CommandCall",
    "enter",
    "host_dispatcher",
    "reindex_measurement_archive",
    "run_campaign",
    "run_head_to_head",
]


def enter(stores: Stores | None = None) -> Stores:
    """The embedded door, first wherever a host enters it: the data root landed, what a dead
    producer left reclaimed, then *stores* (the local account's where none)."""
    setup_logging()
    land_at_entry(every_treatment=False)
    if stores is None:
        stores = local_stores(tenant=None)
    sweep(stores.projects_root)
    return stores


def host_dispatcher(stores: Stores | None = None) -> CommandDispatcher:
    """What a host files every command through, past `enter`."""
    return CommandDispatcher(enter(stores))


async def _dataset_session(
    stores: Stores,
    dataset_name: str,
    *,
    program: object | None,
    rows: Sequence[Sample] | None,
) -> Session:
    """Raises what ``probe_backend`` refuses a launch on; *rows* replace the definition's bank,
    which the mint labels."""
    down = await probe_backend(
        runnable_definition(resolve_dataset(stores, dataset_name)).backend_type
    )
    if down is not None:
        raise down
    session = await init_services(
        dataset_name=dataset_name, identity=stores.identity, stores=stores, program=program
    )
    if rows is None:
        return session
    bank = Bank.of(rows)
    stores.tenant_datasets.put_bank(bank)
    return replace(session, bank=bank)


async def run_campaign(
    target: str | CycleHop,
    campaign_config: CampaignConfig | None = None,
    *,
    stores: Stores | None = None,
    program: object | None = None,
    rows: Sequence[Sample] | None = None,
    readout_sink: StatusFn | None = None,
    budget: Budget,
    step_rounds: int | None = None,
    mode: RunMode,
) -> tuple[Session, CycleResult]:
    """*target* is a dataset, whose campaign the run mints under *campaign_config* (the dataset's
    own where none), or a cycle, which it resumes under its campaign's declaration. Answers the
    session bound to the cycle the run ended on. Under ``origin_gate: strict`` an unhealthy origin
    halts it, and a resume proceeds; under an ``arm_gate`` it blocks until ``submit-arms`` answers."""
    stores = enter(stores)
    opened: OpenedCycle | None = None
    declaration: RunDeclaration | None = None
    if isinstance(target, CycleHop):
        if campaign_config is not None or rows is not None:
            raise PayloadInvalidError(
                f"{target.cycle_id} resumes under its campaign's declaration and bank: pass no "
                "config and no rows to resume it",
                code="resume_declared",
            )
        refuse_campaign(stores, campaign := campaign_at(stores, target))
        opened = await ExistingCycle(target, campaign, budget).open(stores)
        session = opened.session
        campaign_config = opened.cycle.config
        connector = campaign.connector
    else:
        session = await _dataset_session(stores, target, program=program, rows=rows)
        assert session.dataset is not None, "a dataset session resolved its dataset"
        declaration = declare(
            session,
            campaign_config or load_dataset_campaign_config(session.dataset),
            purpose=Optimize(),
        )
        campaign_config = effective_config(declaration)
        connector = declaration.environment.connector

    async def _run(held: HeldBudget) -> tuple[Session, CycleResult]:
        running = opened
        if running is None:
            assert declaration is not None, "a run on a dataset declares its campaign"
            running = await mint_framed_cycle(
                session,
                declaration,
                session.bank.samples,
                campaign_id=fresh_campaign_id(session),
                task_text=None,
            )
            land_job(
                stores, JobRegistry.attach(stores.jobs_dir), held.job_id, running.hop, held.share
            )
        return await run_optimization(
            list(running.session.bank.samples),
            running.cycle,
            session=running.session,
            observers=build_run_observers(
                session=running.session,
                campaign_config=running.cycle.config,
                readout_sink=readout_sink,
            ),
            budget=held,
            mode=mode,
            optimizer=running.optimizer,
        )

    # Admitted BEFORE the mint, whose framing call is paid: a refusal leaves nothing written.
    return await slotless_share(
        campaign_config,
        stores=stores,
        holder=EmbeddedRun(session.measured_dataset),
        hop=None if opened is None else opened.hop,
        requested=budget,
        step_rounds=step_rounds,
        sends=sends_on(stores, campaign_config, connector),
        run=_run,
    )


async def run_head_to_head(
    dataset_name: str,
    campaign_config: CampaignConfig,
    request: HeadToHeadRequest,
    *,
    stores: Stores | None = None,
    mode: RunMode,
    readout_sink: StatusFn | None = None,
) -> list[tuple[str, Session, CycleResult]]:
    """Arms of one head-to-head on *dataset_name*, each answered by its key: every arm planned and
    admitted before any is minted, then run one after another in the host's loop.

    Raises what ``head-to-head`` refuses the browser's and the terminal's act on.
    """
    stores = enter(stores)
    session = await _dataset_session(stores, dataset_name, program=None, rows=None)
    job_registry = JobRegistry.attach(stores.jobs_dir)
    admitted = await admit_head_to_head(
        DatasetArms(session, campaign_config),
        request,
        job_registry=job_registry,
        holder=EmbeddedRun(session.measured_dataset),
    )
    arms = await admitted.minted()
    ran: list[tuple[str, Session, CycleResult]] = []
    for i, arm in enumerate(arms):
        held = arm.held
        try:
            result, _observers = await run_held_job(
                held.start,
                held.session,
                held.cycle,
                mode=mode,
                budget=held.budget,
                optimizer=held.optimizer,
                readout_sink=readout_sink,
            )
        except BaseException as exc:
            # The arms not yet run hold their shares, and no other process would hand them back.
            for rest in arms[i + 1 :]:
                rest.held.start.release(exc, stamps=rest.held.session.hop)
            raise
        ran.append((arm.arm_key, held.session, result))
    return ran
