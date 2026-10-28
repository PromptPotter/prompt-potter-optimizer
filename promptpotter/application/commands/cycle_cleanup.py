"""Both kinds go through ``cleanup_stub_fork_if_empty``, whose refusals and spend banking bind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier, RejectedError
from promptpotter.infrastructure.store.session_pointer import cleanup_stub_fork_if_empty

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToCycle
    from promptpotter.domain.command_kinds import CleanupEmptyCyclesPayload, DeleteCyclePayload


def delete_cycle(dispatched: DispatchedToCycle[DeleteCyclePayload]) -> Applier[object]:
    campaigns, hop = dispatched.dispatcher.stores.campaigns, dispatched.hop

    def _apply() -> None:
        deleted, reason = cleanup_stub_fork_if_empty(campaign_store=campaigns, hop=hop)
        if not deleted:
            raise RejectedError(reason)

    return Applier.silent(_apply)


def cleanup_empty_cycles(
    dispatched: DispatchedToCycle[CleanupEmptyCyclesPayload],
) -> Applier[object]:
    campaigns, hop = dispatched.dispatcher.stores.campaigns, dispatched.hop

    def _apply() -> None:
        for _pass in range(2):
            deleted = [
                cleanup_stub_fork_if_empty(campaign_store=campaigns, hop=stub)[0]
                for stub in campaigns.deletable_stubs(hop)
            ]
            if not any(deleted):
                break

    return Applier.silent(_apply)
