from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.jobs import quota
from promptpotter.application.jobs.launcher.admission import withdraw_queued
from promptpotter.domain.spend import SpendCeilings
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.identity import acting_principal_id

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import (
        CancelQueuedRunPayload,
        ChangeRunLimitsPayload,
        SetConcurrentCyclesPayload,
    )
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.launch_limits import RoundsCap

__all__ = ["cancel_queued_run", "change_run_limits", "set_concurrent_cycles"]


def change_run_limits(
    dispatcher: CommandDispatcher,
    payload: ChangeRunLimitsPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    change, rounds = payload.ceiling, payload.rounds_cap
    return Applier.silent(lambda: _apply_change_run_limits(dispatcher, hop, change, rounds))


def _clamp_to_account_ceilings(
    dispatcher: CommandDispatcher, hop: CycleHop, change: SpendCeilings
) -> tuple[SpendCeilings, SpendCeilings]:
    """Only a SUPPLIED arm is clamped: composing an absent one writes a ceiling nobody moved."""
    stores = dispatcher.stores
    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id), tenant_id=str(stores.identity.tenant_id)
    )
    return quota.clamp_budget_change(
        requested=change,
        user=user,
        stores=stores,
        job_registry=dispatcher.job_registry,
        hop=hop,
    )


async def _apply_change_run_limits(
    dispatcher: CommandDispatcher, hop: CycleHop, change: SpendCeilings, rounds: RoundsCap | None
) -> None:
    """Clamped to the account FIRST: unclamped, a raise here is the way around the host-wallet gate."""
    reserve = SpendCeilings()
    # A round cap is no money: a rounds-only change skips the wallet read, whose contention refuses.
    if change != SpendCeilings():
        change, reserve = await asyncio.to_thread(
            _clamp_to_account_ceilings, dispatcher, hop, change
        )
    quota.hold_run_limits(
        job_registry=dispatcher.job_registry,
        stores=dispatcher.stores,
        hop=hop,
        change=change,
        reserve=reserve,
        rounds=rounds,
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
        principal = acting_principal_id(stores.identity)
        if not withdraw_queued(
            stores, dispatcher.job_registry, payload.job_id, principal_id=principal
        ):
            raise NotFoundError("Not found", code="not_found")

    return Applier.silent(_apply)
