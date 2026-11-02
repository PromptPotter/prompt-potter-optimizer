from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.cycle_addressing import (
    active_pointer,
    resolve_campaign_id_hint,
    resolve_cycle_id,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    import argparse

    from promptpotter.infrastructure.store.stores import Stores


def open_stores(args: argparse.Namespace) -> Stores:
    return local_stores(tenant=args.tenant)


def resolve_target(args: argparse.Namespace, store: Stores) -> CycleHop:
    """A named campaign never takes the pointer's cycle; a cycle named alone is the active campaign's."""
    campaign_id: str = args.campaign
    cycle_id: str = args.cycle
    if campaign_id:
        campaign_id = resolve_campaign_id_hint(store, campaign_id)
        cycle_id = cycle_id or resolve_cycle_id(store, campaign_id, None)
    else:
        pointer = active_pointer(store)
        campaign_id, cycle_id = pointer.campaign_id or "", cycle_id or pointer.cycle_id or ""
    if not (campaign_id and cycle_id):
        raise NotFoundError(
            "No active cycle — name one with --campaign/--cycle, or start one with `new`."
        )
    return CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
