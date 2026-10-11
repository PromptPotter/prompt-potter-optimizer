from __future__ import annotations

from typing import NamedTuple

from pydantic import Field

from promptpotter.domain.cycle_listing import CycleListEntry
from promptpotter.domain.cycle_paths import CyclePath
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import Stores, descend_store

__all__ = ["ActivePointer", "CyclesResponse", "active_pointer", "list_cycles"]


class ActivePointer(NamedTuple):
    """Every field ``None`` where nothing was launched or the workspace was cleared — a steady state."""

    campaign_id: str | None
    cycle_id: str | None


def active_pointer(stores: Stores) -> ActivePointer:
    """Reported RAW: whether the pointed cycle is live is its entry's ``run_phase``."""
    campaign_id, cycle_id = read_active_pointer(stores.base_dir)
    return ActivePointer(campaign_id or None, cycle_id or None)


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
