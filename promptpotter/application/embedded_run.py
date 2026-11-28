"""The embedded launch entry: a host program drives one campaign in its own loop, holding a share
of its ceiling and no machine slot."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from promptpotter.application.account import local_stores
from promptpotter.application.caller import Caller
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.launcher.head_to_head import (
    AdmittedHeadToHead,
    HeldArm,
    run_each,
)
from promptpotter.application.jobs.launcher.launch import Inline, Launched
from promptpotter.application.jobs.launcher.mint_and_start import Hosted
from promptpotter.application.jobs.reaper import sweep
from promptpotter.application.maintenance import landing
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.application.read_params import CycleDashboardParams, ReadParams
from promptpotter.application.runner.entry import run_held
from promptpotter.application.views.readout import StatusFn
from promptpotter.config.logging import setup_logging
from promptpotter.domain.command_kinds import (
    Door,
    HeadToHeadRequest,
    MintCampaignPayload,
    MintHeadToHeadPayload,
    StartRunPayload,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.cycle_result import CycleResult
from promptpotter.domain.draft_edit import CampaignLayer
from promptpotter.domain.run_mode import RunMode
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import Budget
from promptpotter.infrastructure.store.dataset_access import write_own_definition
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
    """An account definition a host runs, as `dataset_access.py::write_own_definition` takes it: `run_campaign`
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
    own where none), or a cycle, which it resumes under its campaign's declaration and *mode*.
    Filed as `mint-campaign` or `start-run` at the host's door, held here (`Hosted`). Answers the
    session bound to the cycle the run ended on, whose backend client it closes on every exit.
    Under ``origin_gate: strict`` an unhealthy origin halts it, and a resume proceeds; under an
    ``arm_gate`` it blocks until ``submit-arms`` answers."""
    stores = await enter(stores)
    if isinstance(target, HostDefinition):
        write_own_definition(stores, **vars(target))
        target = target.slug
    payload: StartRunPayload | MintCampaignPayload
    if isinstance(target, CycleHop):
        if campaign_config is not None or rows is not None:
            raise PayloadInvalidError(
                f"{target.cycle_id} resumes under its campaign's declaration and bank: pass "
                "no config and no rows to resume it",
                code="resume_declared",
            )
        payload = StartRunPayload(
            campaign_id=target.campaign_id,
            cycle_id=target.cycle_id,
            budget=budget,
            step_rounds=step_rounds,
            mode=mode,
        )
    else:  # a mint has no record for *mode* to rewind or diverge from
        payload = MintCampaignPayload(
            dataset_name=target,
            budget=budget,
            step_rounds=step_rounds,
            campaign_config=campaign_config,
        )
    dispatcher = CommandDispatcher(stores, inline=Inline(hosted=Hosted(program, rows)))
    launched = (await dispatcher.dispatch(CommandCall(payload, door=Door.HOST))).result
    assert isinstance(launched, Launched) and launched.held is not None, "a hosted launch is held"
    held = await launched.held
    return await run_held(held, readout_sink=readout_sink)


async def run_head_to_head(
    dataset_name: str,
    campaign_config: CampaignLayer,
    request: HeadToHeadRequest,
    *,
    stores: Stores | None = None,
    readout_sink: StatusFn | None = None,
) -> list[tuple[str, Session, CycleResult]]:
    """Arms of one head-to-head on *dataset_name*, each answered by its key: filed as
    `head-to-head` at the host's door, every arm planned and admitted before any is minted, then
    run one after another in the host's loop, on the one backend client it closes on every exit.
    Each arm is a mint, so no mode rewinds or diverges it.

    Raises what ``head-to-head`` refuses the browser's and the terminal's act on.
    """
    stores = await enter(stores)

    async def _run(arm: HeldArm) -> tuple[str, Session, CycleResult]:
        ended, result = await run_held(arm.held, readout_sink=readout_sink)
        return arm.arm_key, ended, result

    payload = MintHeadToHeadPayload(
        dataset_name=dataset_name, head_to_head=request, campaign_config=campaign_config
    )
    dispatcher = CommandDispatcher(stores, inline=Inline(hosted=Hosted()))
    admitted = (await dispatcher.dispatch(CommandCall(payload, door=Door.HOST))).result
    assert isinstance(admitted, AdmittedHeadToHead), "a hosted head-to-head is held"
    async with admitted.source.closing:
        return await run_each(await admitted.minted(), _run)
