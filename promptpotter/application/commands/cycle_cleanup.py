"""Both kinds go through ``cleanup_stub_fork_if_empty``, whose refusals and spend banking bind."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier, RejectedError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.layout import root_cycle_id
from promptpotter.infrastructure.store.session_pointer import (
    cleanup_stub_fork_if_empty,
    read_active_pointer,
)

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import (
        CleanupEmptyCyclesPayload,
        DeleteCyclePayload,
    )
    from promptpotter.domain.campaign import Campaign

__all__ = ["cleanup_empty_cycles", "delete_cycle"]


def delete_cycle(
    dispatcher: CommandDispatcher, payload: DeleteCyclePayload, campaign: Campaign, hop: CycleHop
) -> Applier[object]:
    campaigns = dispatcher.stores.campaigns
    index = campaigns.load(hop)
    parent_cycle_id = (None if index is None else index.parent_cycle_id) or campaign.root_cycle_id

    def _apply() -> None:
        deleted, reason = cleanup_stub_fork_if_empty(
            campaign_store=campaigns, hop=hop, parent_cycle_id=parent_cycle_id
        )
        if not deleted:
            raise RejectedError(reason)

    return Applier.silent(_apply)


def cleanup_empty_cycles(
    dispatcher: CommandDispatcher,
    payload: CleanupEmptyCyclesPayload,
    campaign: Campaign,
    hop: CycleHop,
) -> Applier[object]:
    stores = dispatcher.stores

    def _apply() -> None:
        root_id = root_cycle_id(hop.cycle_id)
        active_cmp, active_cid = read_active_pointer(stores.base_dir)
        deleted_ids: list[str] = []
        for _pass in range(2):
            progress = False
            family_ids = [
                e.cycle_id
                for e in stores.campaigns.enumerate_cycles()
                if e.campaign_id == hop.campaign_id
                and e.cycle_id != root_id
                and e.parent_cycle_id == root_id
            ]
            for cid in family_ids:
                if cid in deleted_ids:
                    continue
                if hop.campaign_id == active_cmp and cid == active_cid:
                    continue
                deleted, _reason = cleanup_stub_fork_if_empty(
                    campaign_store=stores.campaigns,
                    hop=CycleHop(campaign_id=hop.campaign_id, cycle_id=cid),
                    parent_cycle_id=root_id,
                )
                if deleted:
                    deleted_ids.append(cid)
                    progress = True
            if not progress:
                break

    return Applier.silent(_apply)
