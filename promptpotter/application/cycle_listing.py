from __future__ import annotations

from typing import NamedTuple

from pydantic import Field

from promptpotter.domain.cycle_listing import CycleListEntry
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores, descend_store

__all__ = ["ActivePointer", "CyclesResponse", "active_pointer", "list_cycles"]


class ActivePointer(NamedTuple):
    """Every field ``None`` where nothing was launched or the workspace was cleared — a steady state."""

    campaign_id: str | None
    cycle_id: str | None


def active_pointer(stores: Stores) -> ActivePointer:
    """Liveness is the entry's ``run_phase``; a pointer outliving its cycle dir names nothing."""
    campaign_id, cycle_id = read_active_pointer(stores.base_dir)
    if not campaign_id or not cycle_id:
        return ActivePointer(None, None)
    if not stores.campaigns.cycle_dir(
        CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
    ).is_dir():
        return ActivePointer(None, None)
    return ActivePointer(campaign_id, cycle_id)


class CyclesResponse(StrictModel):
    tenant_id: str
    active_cycle_id: str | None = Field(
        description="Active cycle per active_session.json; null when no session is active."
    )
    cycles: list[CycleListEntry]


def list_cycles(
    stores: Stores, *, inside: CyclePath, campaign_id: str | None, attached_only: bool
) -> CyclesResponse:
    leaf = descend_store(stores, inside)
    pointer = active_pointer(leaf)
    return CyclesResponse(
        tenant_id=leaf.tenant_id,
        active_cycle_id=pointer.cycle_id,
        cycles=sorted(
            (
                cycle
                for cycle in leaf.campaigns.enumerate_cycles()
                if campaign_id in (None, cycle.campaign_id)
                and (cycle.producer_attached or not attached_only)
            ),
            key=lambda cycle: cycle.updated_at,
            reverse=True,
        ),
    )
