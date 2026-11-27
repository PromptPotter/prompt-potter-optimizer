from __future__ import annotations

from promptpotter.application import bulletins
from promptpotter.application.commands.dispatcher import Applier, Dispatched
from promptpotter.domain.command_kinds import (
    DismissBulletinPayload,
    PostBulletinPayload,
    WithdrawBulletinPayload,
)


async def post_bulletin(dispatched: Dispatched[PostBulletinPayload]) -> Applier[object]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    return Applier.blocking(
        lambda: bulletins.post_bulletin(stores, text=payload.text, pops_up=payload.pops_up)
    )


async def withdraw_bulletin(dispatched: Dispatched[WithdrawBulletinPayload]) -> Applier[object]:
    stores, bulletin_id = dispatched.dispatcher.stores, dispatched.payload.bulletin_id

    return Applier.blocking(lambda: bulletins.withdraw_bulletin(stores, bulletin_id))


async def dismiss_bulletin(dispatched: Dispatched[DismissBulletinPayload]) -> Applier[object]:
    stores, bulletin_id = dispatched.dispatcher.stores, dispatched.payload.bulletin_id

    return Applier.blocking(lambda: bulletins.dismiss_bulletin(stores, bulletin_id))
