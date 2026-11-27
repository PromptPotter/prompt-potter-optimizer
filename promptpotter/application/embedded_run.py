"""The embedded launch entry: a host program drives one campaign in its own loop, holding a share
of its ceiling and no machine slot."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from promptpotter.application.account import local_stores
from promptpotter.application.caller import Caller
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.datasets.authored import ConfigLayers
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.admission import refuse_campaign
from promptpotter.application.jobs.launcher.head_to_head import (
    HeldArm,
    admit_head_to_head,
    dataset_arms,
    run_each,
)
from promptpotter.application.jobs.launcher.launch import admit
from promptpotter.application.jobs.launcher.mint_and_start import EmbeddedMint, EmbeddedResume
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.application.read_params import CycleDashboardParams, ReadParams
from promptpotter.application.views.readout import StatusFn
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import Door, HeadToHeadRequest
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.draft_edit import CampaignLayer
from promptpotter.domain.job import EmbeddedRun
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.dataset_access import resolve_dataset
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError

# This module IS the embedded surface: a capability it does not name is one a host cannot find.
# Every verb but the run is a command the host files through `host_dispatcher`, and every read a
# `ReadKind` row it answers through `answer_read` (a cycle's dashboard: `CycleDashboardParams`).
__all__ = [
    "CommandCall",
    "CycleDashboardParams",
    "Door",
    "HostDefinition",
    "answer_read",
    "enter",
    "host_dispatcher",
    "reindex_measurement_archive",
    "run_campaign",
    "run_head_to_head",
]


async def enter(stores: Stores | None = None) -> Stores:
    """The embedded door, first wherever a host enters it: *stores* (the local account's where none),
    landed and swept off the loop, as no server ticks for a host. A sync host awaits `asyncio.run`."""
    # Here alone: only an entry lands, and a module importing this one is no entry.
    from promptpotter.application.maintenance import landing

    setup_logging()
    await asyncio.to_thread(landing.land_at_entry, every_treatment=False)
    stores = local_stores(tenant=None) if stores is None else stores
    await asyncio.to_thread(sweep, stores.projects_root)
    return stores


async def host_dispatcher(stores: Stores | None = None) -> CommandDispatcher:
    """What a host files every command through, past `enter`."""
    return CommandDispatcher(await enter(stores))


async def answer_read(params: ReadParams, stores: Stores | None = None) -> object:
    """*params*' read answered as every door answers it (the body its route serves), past `enter`."""
    return await params.answered(Caller(await enter(stores)))


@dataclass(frozen=True)
class HostDefinition:
    """An account definition a host runs, as `Stores.write_own_definition` takes it: `run_campaign`
    writes it past its one `enter`, since a landing drops a definition written before it."""

    slug: str
    source: dict[str, Any]
    pipeline: dict[str, Any]
    campaign: dict[str, Any]
    description: str
    framing: TaskDecomposition
    origin: dict[str, dict[str, Any]]


async def run_campaign(
    target: str | CycleHop | HostDefinition,
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
    """*target* is a dataset (a host's, written first), whose campaign the run mints under *campaign_config* (the dataset's
    own where none), or a cycle, which it resumes under its campaign's declaration. Answers the
    session bound to the cycle the run ended on, whose backend client it closes on every exit.
    Under ``origin_gate: strict`` an unhealthy origin halts it, and a resume proceeds; under an
    ``arm_gate`` it blocks until ``submit-arms`` answers."""
    stores = await enter(stores)
    if isinstance(target, HostDefinition):
        stores.write_own_definition(**vars(target))
        target = target.slug
    request: EmbeddedMint | EmbeddedResume
    if isinstance(target, CycleHop):
        if campaign_config is not None or rows is not None:
            raise PayloadInvalidError(
                f"{target.cycle_id} resumes under its campaign's declaration and bank: pass "
                "no config and no rows to resume it",
                code="resume_declared",
            )
        refuse_campaign(stores, (at := stores.campaigns.campaign_cycle(target)).campaign)
        request = EmbeddedResume(at, budget, step_rounds)
    else:
        dataset = resolve_dataset(stores, target)
        # The config the mint declares runs as laid (`authored.py::effective_config`).
        layers = ConfigLayers.of(dataset, campaign_config)
        request = EmbeddedMint(dataset, budget, layers, None, program, rows, step_rounds)
    # Admitted and probed BEFORE the session, the mint's paid framing and a resume's bind: a
    # refusal opens and writes nothing.
    admitted = await asyncio.to_thread(
        admit,
        request,
        mode,
        stores=stores,
        job_registry=JobRegistry.attach(stores.jobs_dir),
        inline=None,
    )
    held = await admitted.held()
    return await held.run(readout_sink=readout_sink)


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
    stores = await enter(stores)

    async def _run(arm: HeldArm) -> tuple[str, Session, CycleResult]:
        ended, result = await replace(arm.held, mode=mode).run(readout_sink=readout_sink)
        return arm.arm_key, ended, result

    arms = await dataset_arms(stores, resolve_dataset(stores, dataset_name))
    async with arms.closing:
        admitted = await admit_head_to_head(
            replace(arms, layers=ConfigLayers.stating(campaign_config)),
            request,
            job_registry=JobRegistry.attach(stores.jobs_dir),
            holder=EmbeddedRun(arms.session.measured_dataset),
        )
        return await run_each(await admitted.minted(), _run)
