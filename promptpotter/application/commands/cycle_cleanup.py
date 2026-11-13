"""Both kinds go through ``cleanup_stub_fork_if_empty``, whose refusals and spend banking bind, under
the destroyers' one guard (`admission.py::destroying`)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier, RejectedError
from promptpotter.application.jobs.launcher.admission import deletable_stubs, destroying
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty
from promptpotter.shared.errors import CycleBusyError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToCycle
    from promptpotter.domain.command_kinds import CleanupEmptyCyclesPayload, DeleteCyclePayload


async def delete_cycle(dispatched: DispatchedToCycle[DeleteCyclePayload]) -> Applier[object]:
    dispatcher, hop = dispatched.dispatcher, dispatched.hop
    stores = dispatcher.stores

    def _apply() -> None:
        with destroying(stores, dispatcher.job_registry, hop.campaign_id, cycle_id=hop.cycle_id):
            deleted, reason = cleanup_stub_fork_if_empty(campaign_store=stores.campaigns, hop=hop)
        if not deleted:
            raise RejectedError(reason)

    return Applier.blocking(_apply)


async def cleanup_empty_cycles(
    dispatched: DispatchedToCycle[CleanupEmptyCyclesPayload],
) -> Applier[object]:
    dispatcher, hop = dispatched.dispatcher, dispatched.hop
    stores = dispatcher.stores

    def _apply() -> None:
        for _pass in range(2):
            deleted = False
            for stub in deletable_stubs(stores, hop):
                # Asked again at the act: one a run or a pass took since the list stays.
                with (
                    contextlib.suppress(CycleBusyError),
                    destroying(
                        stores, dispatcher.job_registry, stub.campaign_id, cycle_id=stub.cycle_id
                    ),
                ):
                    deleted |= cleanup_stub_fork_if_empty(
                        campaign_store=stores.campaigns, hop=stub
                    )[0]
            if not deleted:
                break

    return Applier.blocking(_apply)
