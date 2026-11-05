from __future__ import annotations

from pydantic import Field

from promptpotter.application.account import registered_or_default_identity
from promptpotter.config.paths import default_projects_root
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, Subject
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_exists,
    read_active_pointer,
)
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.shared.errors import BadRequestError, NotFoundError


def active_pointer(stores: Stores, *, inside: CyclePath = ()) -> CycleHop | None:
    """``None`` where nothing was launched or the workspace was cleared — a steady state. Liveness is
    the entry's ``run_phase``; a pointer outliving its cycle dir names nothing."""
    stores = descend_store(stores, inside)
    hop = read_active_pointer(stores.base_dir)
    if hop is None or not stores.campaigns.cycle_dir(hop).is_dir():
        return None
    return hop


class ActiveSessionResponse(StrictModel):
    """The tenant's latest launch — not the set of live runs, which is `state.attached` on `/hierarchy`."""

    tenant_id: str = Field(
        description="Tenant the pointer belongs to — the caller's own, always known"
    )
    campaign_id: str | None = Field(
        description="Campaign of the latest launch; null when no session is active."
    )
    cycle_id: str | None = Field(
        description="Cycle of the latest launch; null when no session is active."
    )


def active_session(stores: Stores) -> ActiveSessionResponse:
    hop = active_pointer(stores)
    return ActiveSessionResponse(
        tenant_id=stores.tenant_id,
        campaign_id=None if hop is None else hop.campaign_id,
        cycle_id=None if hop is None else hop.cycle_id,
    )


def holds_active_pointer(*, tenant: str | None) -> bool:
    identity = registered_or_default_identity(tenant)
    return active_pointer_exists(tenant_workspace(default_projects_root(), identity.tenant_id))


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


def resolve_subject(stores: Stores, subject: Subject) -> Subject:
    """Every door's one resolution of an addressed campaign id prefix, in the chain it names; left
    UNCHANGED where nothing matches, so the read or command it rides answers ``not_found``."""
    matches = _campaign_matches(stores, subject.campaign_id, subject.inside)
    return subject._replace(campaign_id=next(iter(matches), subject.campaign_id))


def cycle_target(stores: Stores, campaign: str, cycle: str) -> CycleHop:
    """A terminal's named cycle: a named campaign (an id prefix) never takes the pointer's cycle,
    and a cycle named alone is the active campaign's; each a prefix of its id."""
    if campaign:
        campaign_id = resolve_campaign_id(stores, campaign)
        return CycleHop(
            campaign_id=campaign_id, cycle_id=resolve_cycle_id(stores, campaign_id, cycle or None)
        )
    hop = active_pointer(stores)
    if hop is None or not (cycle_id := cycle or hop.cycle_id):
        raise NotFoundError(
            "No active cycle — name one with --campaign/--cycle, or start one with `new`."
        )
    return CycleHop(campaign_id=hop.campaign_id, cycle_id=cycle_id)


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
