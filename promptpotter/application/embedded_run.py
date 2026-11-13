"""The embedded launch entry: a host program drives one campaign in its own loop, holding a share
of its ceiling and no machine slot."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import ConfigLayers, effective_config
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import (
    admit_slotless,
    campaign_at,
    probe_backend,
    refuse_campaign,
)
from promptpotter.application.jobs.launcher.head_to_head import (
    DatasetArms,
    HeldArm,
    admit_head_to_head,
    run_each,
)
from promptpotter.application.jobs.launcher.mint_and_start import ExistingCycle
from promptpotter.application.jobs.launcher.run_job import run_held_job
from promptpotter.application.jobs.mint import (
    declare,
    mint_framed_cycle,
)
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.application.maintenance.landing import land_at_entry
from promptpotter.application.pipeline_resolve import cycle_config, laid_session
from promptpotter.application.views.readout import StatusFn
from promptpotter.config.logging import setup_logging
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.job import EmbeddedRun
from promptpotter.domain.results import CycleResult
from promptpotter.domain.sample import Bank
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.dataset_access import resolve_dataset, runnable_definition
from promptpotter.shared.errors import PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.campaign import RunDeclaration
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.domain.draft_edit import CampaignLayer
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
    """The embedded door, first wherever a host enters it: the data root landed, then *stores*
    (the local account's where none). A launch's admission reclaims what a dead producer left."""
    setup_logging()
    land_at_entry(every_treatment=False)
    return local_stores(tenant=None) if stores is None else stores


def host_dispatcher(stores: Stores | None = None) -> CommandDispatcher:
    """What a host files every command through, past `enter`."""
    return CommandDispatcher(enter(stores))


@asynccontextmanager
async def _dataset_session(
    stores: Stores,
    dataset_name: str,
    *,
    program: object | None,
    rows: Sequence[Sample] | None,
) -> AsyncIterator[Session]:
    """Raises what ``probe_backend`` refuses a launch on; *rows* replace the definition's bank,
    which the mint labels."""
    down = await probe_backend(
        runnable_definition(resolve_dataset(stores, dataset_name)).backend_type
    )
    if down is not None:
        raise down
    async with init_services(
        dataset_name=dataset_name, identity=stores.identity, stores=stores, program=program
    ) as session:
        if rows is None:
            yield session
            return
        bank = Bank.of(rows)
        stores.tenant_datasets.put_bank(bank)
        yield replace(session, bank=bank)


async def run_campaign(
    target: str | CycleHop,
    campaign_config: CampaignLayer | None = None,
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
    session bound to the cycle the run ended on, whose backend client it closes on every exit.
    Under ``origin_gate: strict`` an unhealthy origin halts it, and a resume proceeds; under an
    ``arm_gate`` it blocks until ``submit-arms`` answers."""
    stores = enter(stores)
    resuming: ExistingCycle | None = None
    declaration: RunDeclaration | None = None
    async with AsyncExitStack() as clients:
        if isinstance(target, CycleHop):
            if campaign_config is not None or rows is not None:
                raise PayloadInvalidError(
                    f"{target.cycle_id} resumes under its campaign's declaration and bank: pass "
                    "no config and no rows to resume it",
                    code="resume_declared",
                )
            refuse_campaign(stores, campaign := campaign_at(stores, target))
            resuming = ExistingCycle(target, campaign, budget)
            config = cycle_config(stores, campaign, target).config
            connector, measured = campaign.connector, campaign.dataset_name
        else:
            session = await clients.enter_async_context(
                _dataset_session(stores, target, program=program, rows=rows)
            )
            assert session.dataset is not None, "a dataset session resolved its dataset"
            session, laid = laid_session(session, ConfigLayers.of(session.dataset, campaign_config))
            declaration = declare(session, laid, purpose=Optimize())
            config = effective_config(declaration)
            connector, measured = declaration.environment.connector, session.measured_dataset

        # Admitted BEFORE the mint, whose framing call is paid, and before a resume binds its
        # cycle: a refusal leaves nothing written. Offloaded: the sweep scans every cycle ledger.
        await asyncio.to_thread(sweep, stores.projects_root)
        start, held = await admit_slotless(
            config,
            stores=stores,
            holder=EmbeddedRun(measured),
            hop=None if resuming is None else resuming.hop,
            requested=budget,
            step_rounds=step_rounds,
            sends=sends_on(stores, config, connector),
        )
        # Then held as a head-to-head's arm is: its cycle opened, a mint's landed on.
        stamps: CycleHop | None = None
        try:
            if resuming is not None:
                running = await clients.enter_async_context(resuming.open(stores))
            else:
                assert declaration is not None, "a run on a dataset declares its campaign"
                running = await mint_framed_cycle(
                    session, declaration, session.bank.samples, task_text=None
                )
                stamps = running.hop
                start.land(running.hop, held)
        except BaseException as exc:
            start.release(exc, stamps=stamps)
            raise
        return await run_held_job(
            start,
            running.session,
            running.cycle,
            mode=mode,
            budget=held,
            optimizer=running.optimizer,
            readout_sink=readout_sink,
        )


async def run_head_to_head(
    dataset_name: str,
    campaign_config: CampaignLayer,
    request: HeadToHeadRequest,
    *,
    stores: Stores | None = None,
    mode: RunMode,
    readout_sink: StatusFn | None = None,
) -> list[tuple[str, Session, CycleResult]]:
    """Arms of one head-to-head on *dataset_name*, each answered by its key: every arm planned and
    admitted before any is minted, then run one after another in the host's loop, on the one
    backend client it closes on every exit.

    Raises what ``head-to-head`` refuses the browser's and the terminal's act on.
    """
    stores = enter(stores)

    async def _run(arm: HeldArm) -> tuple[str, Session, CycleResult]:
        held = arm.held
        ended, result = await run_held_job(
            held.start,
            held.session,
            held.cycle,
            mode=mode,
            budget=held.budget,
            optimizer=held.optimizer,
            readout_sink=readout_sink,
        )
        return arm.arm_key, ended, result

    async with (
        _dataset_session(stores, dataset_name, program=None, rows=None) as session,
        AsyncExitStack() as closing,
    ):
        admitted = await admit_head_to_head(
            DatasetArms(session, ConfigLayers.stating(campaign_config), closing),
            request,
            job_registry=JobRegistry.attach(stores.jobs_dir),
            holder=EmbeddedRun(session.measured_dataset),
        )
        return await run_each(await admitted.minted(), _run)
