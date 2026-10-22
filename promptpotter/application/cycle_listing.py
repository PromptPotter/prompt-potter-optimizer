from __future__ import annotations

from typing import NamedTuple

from pydantic import Field

from promptpotter.application.account import registered_or_default_identity
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.domain.cycle_listing import CycleListEntry
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_exists,
    read_active_pointer,
)
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.shared.errors import BadRequestError, NotFoundError

__all__ = [
    "ActivePointer",
    "CyclesResponse",
    "active_pointer",
    "holds_active_pointer",
    "list_cycles",
    "resolve_campaign_id",
    "resolve_campaign_id_hint",
    "resolve_cycle_id",
]


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


def holds_active_pointer(*, tenant: str | None) -> bool:
    identity = registered_or_default_identity(tenant)
    return active_pointer_exists(tenant_workspace(DEFAULT_PROJECTS_ROOT, identity.tenant_id))


def _listed(ids: list[str]) -> str:
    return f"{', '.join(ids[:5])}{'…' if len(ids) > 5 else ''}"


def _campaign_matches(stores: Stores, needle: str, inside: CyclePath) -> list[str]:
    """Refuses on ambiguity: picking one of several is worse than resolving none."""
    candidates = descend_store(stores, inside).campaigns.match_campaign_ids(needle)
    if len(candidates) > 1:
        raise BadRequestError(
            f"{needle!r} matches {len(candidates)} campaigns: {_listed(candidates)}. "
            "Pass the full id.",
            code="ambiguous_id",
        )
    return candidates


def resolve_campaign_id(stores: Stores, needle: str, *, inside: CyclePath = ()) -> str:
    matches = _campaign_matches(stores, needle, inside)
    if not matches:
        raise NotFoundError(f"no campaign matches {needle!r}.")
    return matches[0]


def resolve_campaign_id_hint(stores: Stores, needle: str, *, inside: CyclePath = ()) -> str:
    """Returned UNCHANGED where nothing matches: the dispatcher already answers ``not_found``."""
    return next(iter(_campaign_matches(stores, needle, inside)), needle)


def resolve_cycle_id(stores: Stores, campaign_id: str, hint: str | None) -> str:
    """``hint=None`` names every cycle, so it resolves only where the campaign has ONE."""
    matches = stores.campaigns.match_cycle_ids(campaign_id, hint or "")
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFoundError(
            f"no cycle in {campaign_id!r} matches {hint!r}."
            if hint
            else f"campaign {campaign_id!r} has no cycles on disk."
        )
    raise BadRequestError(
        f"{hint!r} matches {len(matches)} cycles in {campaign_id!r}: {_listed(matches)}."
        if hint
        else f"campaign {campaign_id!r} has {len(matches)} cycles; pass --cycle <prefix>. "
        f"Available: {_listed(matches)}.",
        code="ambiguous_id",
    )


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
