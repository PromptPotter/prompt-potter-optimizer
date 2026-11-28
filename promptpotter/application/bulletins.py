"""What the host tells every account: the host posts a bulletin, each account reads and dismisses it."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from promptpotter.domain.bulletin import Bulletin
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity import bulletin_board
from promptpotter.infrastructure.identity.grants import IdentityPaths
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError


class ServedBulletin(StrictModel):
    id: str
    posted_at: str
    text: str
    pops_up: bool = Field(description="Opens on its own until the account dismisses it.")
    dismissed: bool = Field(description="This account dismissed it.")


class AccountBulletins(StrictModel):
    bulletins: list[ServedBulletin] = Field(description="Newest first.")


def _board(stores: Stores) -> Path:
    return IdentityPaths(stores.projects_root).bulletins


def account_bulletins(stores: Stores) -> AccountBulletins:
    board = bulletin_board.read_board(_board(stores))
    dismissed = set(board.dismissed.get(str(stores.identity.user_id), ()))
    return AccountBulletins(
        bulletins=[
            ServedBulletin(
                id=b.id,
                posted_at=b.posted_at,
                text=b.text,
                pops_up=b.pops_up,
                dismissed=b.id in dismissed,
            )
            for b in reversed(board.bulletins)
        ]
    )


def _require_host(stores: Stores) -> None:
    if not stores.identity.host:
        raise PayloadInvalidError(
            "Only whoever runs this box posts or withdraws a bulletin.",
            code="bulletin_posted_by_host",
        )


def post_bulletin(stores: Stores, *, text: str, pops_up: bool) -> Bulletin:
    _require_host(stores)
    return bulletin_board.post(_board(stores), text=text, pops_up=pops_up)


def withdraw_bulletin(stores: Stores, bulletin_id: str) -> None:
    _require_host(stores)
    if not bulletin_board.withdraw(_board(stores), bulletin_id=bulletin_id):
        raise NotFoundError(f"No bulletin {bulletin_id!r} was posted.", code="bulletin_unknown")


def dismiss_bulletin(stores: Stores, bulletin_id: str) -> None:
    account_id = str(stores.identity.user_id)
    if not bulletin_board.dismiss(_board(stores), account_id=account_id, bulletin_id=bulletin_id):
        raise NotFoundError(f"No bulletin {bulletin_id!r} was posted.", code="bulletin_unknown")
