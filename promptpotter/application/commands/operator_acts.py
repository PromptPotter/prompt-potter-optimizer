from __future__ import annotations

from promptpotter.application import account
from promptpotter.application.commands.dispatcher import Applier, Dispatched
from promptpotter.config.paths import default_projects_root
from promptpotter.domain.command_kinds import (
    BlockEmailPayload,
    CommandPayload,
    DeleteAccountPayload,
    GrantDelegationPayload,
    RevokeDelegationPayload,
    SetCouponPayload,
    UnblockEmailPayload,
)
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.identity import CAMPAIGN_CAP_BY_NAME, capabilities_from_names


def _host_acts[P: CommandPayload](dispatched: Dispatched[P]) -> str:
    """The host's act, refused any other; its audit's actor is the door it came through."""
    account.require_host(dispatched.dispatcher.stores, dispatched.door)
    return f"door:{dispatched.door.value}"


async def block_email(dispatched: Dispatched[BlockEmailPayload]) -> Applier[object]:
    actor, email = _host_acts(dispatched), dispatched.payload.email
    return Applier.blocking(lambda: account.block_email(email, actor=actor))


async def unblock_email(dispatched: Dispatched[UnblockEmailPayload]) -> Applier[object]:
    actor, email = _host_acts(dispatched), dispatched.payload.email
    return Applier.blocking(lambda: account.unblock_email(email, actor=actor))


async def grant_delegation(dispatched: Dispatched[GrantDelegationPayload]) -> Applier[object]:
    actor, payload = _host_acts(dispatched), dispatched.payload
    caps = capabilities_from_names(payload.capabilities.split(","))
    if not caps:
        raise PayloadInvalidError(
            f"Name at least one capability ({', '.join(sorted(CAMPAIGN_CAP_BY_NAME))})."
        )

    def apply() -> None:
        delegator = account.host_user_id()
        if delegator is None:
            raise PayloadInvalidError(
                "No host account to delegate from: sign in once first.", code="host_unsigned"
            )
        account.grant(payload.user_id, delegator=delegator, capabilities=caps, actor=actor)

    return Applier.blocking(apply)


async def revoke_delegation(dispatched: Dispatched[RevokeDelegationPayload]) -> Applier[object]:
    actor, user_id = _host_acts(dispatched), dispatched.payload.user_id
    return Applier.blocking(lambda: account.revoke_grant(user_id, actor=actor))


async def delete_account(dispatched: Dispatched[DeleteAccountPayload]) -> Applier[object]:
    actor, user_id = _host_acts(dispatched), dispatched.payload.user_id
    return Applier.blocking(
        lambda: account.delete_account(default_projects_root(), user_id, actor=actor)
    )


async def set_coupon(dispatched: Dispatched[SetCouponPayload]) -> Applier[object]:
    _host_acts(dispatched)
    payload = dispatched.payload
    return Applier.blocking(
        lambda: account.set_coupon(
            default_projects_root(), payload.user_id, payload.budget, afresh=payload.afresh
        )
    )
