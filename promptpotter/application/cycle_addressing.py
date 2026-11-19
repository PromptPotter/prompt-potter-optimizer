from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field

from promptpotter.application.account import registered_or_default_identity
from promptpotter.application.exhibit import exhibit_stores
from promptpotter.application.origin_listing import no_dataset_hint
from promptpotter.config.paths import default_projects_root
from promptpotter.config.settings import settings
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, Subject
from promptpotter.domain.said import Said
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import tenant_workspace
from promptpotter.infrastructure.store.session_pointer import (
    active_pointer_exists,
    read_active_pointer,
)
from promptpotter.infrastructure.store.stores import Stores, descend_store
from promptpotter.shared.errors import MINT_FRESH, BadRequestError, NextAct, NotFoundError


def latest_launch(stores: Stores, *, inside: CyclePath = ()) -> CycleHop | None:
    """The cycle the latest run held (a mint's, a fork's or a resume's: the pointer moves where
    `run_observers.py::build_run_observers` holds it), and so what a verb naming no cycle acts on.
    ``None`` where nothing ran or the workspace was cleared — a steady state; a pointer outliving
    its cycle dir names nothing."""
    stores = descend_store(stores, inside)
    hop = read_active_pointer(stores.base_dir)
    if hop is None or not stores.campaigns.cycle_dir(hop).is_dir():
        return None
    return hop


def launched(stores: Stores) -> CycleHop:
    """`latest_launch` for a terminal verb naming no campaign, refused where nothing ran."""
    if (hop := latest_launch(stores)) is None:
        refusal = NotFoundError(
            "No active cycle — name its campaign and cycle, or start one.",
            next_acts=(MINT_FRESH,),
        )
        refusal.add_note(no_dataset_hint(stores))
        raise refusal
    return hop


def _own_followed(stores: Stores) -> CycleHop | None:
    launched = latest_launch(stores)
    running = stores.campaigns.run_held_cycles()
    if not running or launched in running:
        return launched
    # Of runs the pointer names none of, the newest campaign's.
    return max(running, key=lambda hop: _created_at(stores, hop))


def _created_at(stores: Stores, hop: CycleHop) -> str:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    return "" if campaign is None else campaign.created_at


def _exhibit_head(stores: Stores) -> CycleHop | None:
    """The head of the exhibit's line, where the caller follows it (`exhibit.py::exhibit_stores`)."""
    exhibit = exhibit_stores(stores)
    if (
        exhibit is None
        or (campaign := exhibit.campaigns.load_campaign(settings.EXHIBIT_CAMPAIGN)) is None
    ):
        return None
    return exhibit.campaigns.line(campaign.root_hop)[-1]


class ActiveSessionResponse(StrictModel):
    """The run to follow (`followed_cycle`), and the line saying whose it is where it is not the
    caller's own (`key` ``exhibit``), or why there is none."""

    hop: CycleHop | None = Field(description="The cycle of the run to follow; null where none is.")
    said: Said | None = Field(description="Null on the caller's own run.")


_EXHIBIT = Said(
    key="exhibit",
    tone="quiet",
    label="The host's exhibit, read-only, while you have nothing running",
    detail='"Try & learn" in your preferences turns it off.',
)


def followed_cycle(stores: Stores) -> ActiveSessionResponse:
    """What a follower shows (the stage, an unpinned webapp): a cycle a run holds, the latest
    launch's first, else the latest launch, else the head of the host's exhibit, else nothing and
    why. A verb naming no cycle acts on `latest_launch`, never on this."""
    if (own := _own_followed(stores)) is not None:
        return ActiveSessionResponse(hop=own, said=None)
    if (shown := _exhibit_head(stores)) is not None:
        return ActiveSessionResponse(hop=shown, said=_EXHIBIT)
    exhibit = (
        ', else the host\'s exhibit while "Try & learn" is on' if settings.EXHIBIT_CAMPAIGN else ""
    )
    return ActiveSessionResponse(
        hop=None,
        said=Said(
            key="nothing_followed",
            tone="quiet",
            label="No run to follow yet",
            detail=f"This screen follows your running campaign, else your latest launch{exhibit}.",
        ),
    )


def holds_active_pointer(*, tenant: str | None) -> bool:
    identity = registered_or_default_identity(tenant)
    return active_pointer_exists(tenant_workspace(default_projects_root(), identity.tenant_id))


def _listed(ids: list[str]) -> str:
    return f"{', '.join(ids[:5])}{'…' if len(ids) > 5 else ''}"


def _ambiguity_refused(needle: str, matches: list[str], what: str) -> list[str]:
    """Refuses on ambiguity: picking one of several is worse than resolving none."""
    if len(matches) > 1:
        raise BadRequestError(
            f"{needle!r} matches {len(matches)} {what}: {_listed(matches)}. Pass the full id.",
            code="ambiguous_id",
        )
    return matches


def _campaign_matches(stores: Stores, needle: str) -> list[str]:
    return _ambiguity_refused(needle, stores.campaigns.match_campaign_ids(needle), "campaigns")


def _leaf(stores: Stores, subject: Subject) -> tuple[Stores, Subject]:
    leaf_stores = descend_store(stores, subject.inside)
    matches = _campaign_matches(leaf_stores, subject.campaign_id)
    return leaf_stores, subject._replace(campaign_id=next(iter(matches), subject.campaign_id))


def resolve_subject(stores: Stores, subject: Subject) -> Subject:
    """Every door's one resolution of an addressed campaign id prefix, in the chain it names; left
    UNCHANGED where nothing matches, so the read or command it rides answers ``not_found``."""
    return _leaf(stores, subject)[1]


def named_campaign(stores: Stores, needle: str) -> Campaign:
    """The caller's campaign an id prefix names, resolved as every door's (`resolve_subject`);
    another user's or none is ``not_found`` (`CampaignStore.campaign`)."""
    return stores.campaigns.campaign(resolve_subject(stores, Subject(needle)).campaign_id)


def resolve_cycle(stores: Stores, subject: Subject) -> Subject:
    """`resolve_subject`, then the cycle id prefix in the campaign it resolved, by the
    same rule."""
    leaf_stores, resolved = _leaf(stores, subject)
    campaign_id, needle = resolved.campaign_id, resolved.cycle_id
    if campaign_id not in leaf_stores.campaigns.list_campaign_ids():
        return resolved
    cycles = leaf_stores.campaigns.match_cycle_ids(campaign_id, needle)
    matches = _ambiguity_refused(needle, cycles, f"cycles in {campaign_id!r}")
    return resolved._replace(cycle_id=next(iter(matches), needle))


def cycle_target(stores: Stores, campaign: str, cycle: str) -> CycleHop:
    """A terminal's named cycle: a named campaign (an id prefix) never takes the latest launch's
    cycle, and a cycle named alone is the latest launch's campaign's; each a prefix of its id."""
    if campaign:
        matches = _campaign_matches(stores, campaign)
        if not matches:
            raise NotFoundError(f"no campaign matches {campaign!r}.")
        campaign_id = matches[0]
        return CycleHop(
            campaign_id=campaign_id, cycle_id=resolve_cycle_id(stores, campaign_id, cycle or None)
        )
    hop = launched(stores)
    cycle_id = resolve_cycle_id(stores, hop.campaign_id, cycle) if cycle else hop.cycle_id
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
        else f"campaign {campaign_id!r} has {len(matches)} cycles; name one. "
        f"Available: {_listed(matches)}.",
        code="ambiguous_id",
        next_acts=(NextAct(field="cycle_id", value="an id prefix of one"),),
    )


@dataclass
class SessionCtx:
    """A terminal verb's cycle, loaded by the dispatcher its command goes through, never here."""

    store: Stores
    campaign_id: str
    cycle_id: str

    @property
    def hop(self) -> CycleHop:
        """Derived, never stored: ``cycle_id`` is reassigned IN PLACE when a verb forks."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)
