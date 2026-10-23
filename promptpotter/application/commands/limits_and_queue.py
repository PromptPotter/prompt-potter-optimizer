from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application.account import account_of
from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.jobs import quota
from promptpotter.application.jobs.launcher.admission import withdraw_queued
from promptpotter.domain.spend import MONEY_AXES, Budget
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.command_kinds import (
        CancelQueuedRunPayload,
        ChangeRunLimitsPayload,
        SetConcurrentCyclesPayload,
    )
    from promptpotter.domain.cycle_paths import CycleHop


def change_run_limits(
    dispatcher: CommandDispatcher,
    payload: ChangeRunLimitsPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    change = payload.budget
    return Applier.silent(lambda: _apply_change_run_limits(dispatcher, hop, change))


def _clamp_to_account_ceilings(
    dispatcher: CommandDispatcher, hop: CycleHop, change: Budget
) -> tuple[Budget, Budget]:
    stores = dispatcher.stores
    return quota.clamp_budget_change(
        requested=change,
        user=account_of(stores),
        stores=stores,
        job_registry=dispatcher.job_registry,
        hop=hop,
    )


async def _apply_change_run_limits(
    dispatcher: CommandDispatcher, hop: CycleHop, change: Budget
) -> None:
    """Clamped to the account FIRST: unclamped, a raise here is the way around the host-wallet gate."""
    reserve = Budget()
    # Rounds and a clock are no money: a change naming neither money axis skips the wallet read.
    if change.named & MONEY_AXES:
        change, reserve = await asyncio.to_thread(
            _clamp_to_account_ceilings, dispatcher, hop, change
        )
    await asyncio.to_thread(
        quota.hold_budget_change,
        job_registry=dispatcher.job_registry,
        stores=dispatcher.stores,
        hop=hop,
        change=change,
        reserve=reserve,
    )


def set_concurrent_cycles(
    dispatcher: CommandDispatcher, payload: SetConcurrentCyclesPayload
) -> Applier[object]:
    limit = payload.max_concurrent_cycles
    return Applier.silent(
        lambda: quota.set_concurrent_cycles(stores=dispatcher.stores, limit=limit)
    )


def cancel_queued_run(
    dispatcher: CommandDispatcher, payload: CancelQueuedRunPayload
) -> Applier[object]:
    """A queued FORK's stub stays for ``cleanup-empty-cycles``: deleting a cycle is that verb's."""

    def _apply() -> None:
        stores = dispatcher.stores
        principal = acting_principal(stores.identity).id
        if not withdraw_queued(
            stores, dispatcher.job_registry, payload.job_id, principal_id=principal
        ):
            raise NotFoundError("Not found", code="not_found")

    return Applier.silent(_apply)
