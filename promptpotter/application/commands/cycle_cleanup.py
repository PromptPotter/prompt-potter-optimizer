"""Both kinds go through ``cleanup_stub_fork_if_empty``, whose refusals and spend banking bind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier, RejectedError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import (
        CleanupEmptyCyclesPayload,
        DeleteCyclePayload,
    )
    from promptpotter.domain.campaign import Campaign


def delete_cycle(
    dispatcher: CommandDispatcher, payload: DeleteCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    campaigns = dispatcher.stores.campaigns

    def _apply() -> None:
        deleted, reason = cleanup_stub_fork_if_empty(campaign_store=campaigns, hop=hop)
        if not deleted:
            raise RejectedError(reason)

    return Applier.silent(_apply)


def cleanup_empty_cycles(
    dispatcher: CommandDispatcher,
    payload: CleanupEmptyCyclesPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    campaigns = dispatcher.stores.campaigns

    def _apply() -> None:
        for _pass in range(2):
            deleted = [
                cleanup_stub_fork_if_empty(campaign_store=campaigns, hop=stub)[0]
                for stub in campaigns.deletable_stubs(hop)
            ]
            if not any(deleted):
                break

    return Applier.silent(_apply)
