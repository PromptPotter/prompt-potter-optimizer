"""What the host tells every account: the host posts a bulletin, each account reads and dismisses it."""

from __future__ import annotations

from pydantic import Field

from promptpotter.domain.bulletin import Bulletin
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.identity import bulletin_board
from promptpotter.infrastructure.identity.grants import default_identity_paths
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


def account_bulletins(stores: Stores) -> AccountBulletins:
    board = bulletin_board.read_board(default_identity_paths().bulletins)
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


def post_bulletin(stores: Stores, *, text: str, pops_up: bool) -> Bulletin:
    if not stores.identity.host:
        raise PayloadInvalidError(
            "Only whoever runs this box posts a bulletin.", code="bulletin_posted_by_host"
        )
    return bulletin_board.post(default_identity_paths().bulletins, text=text, pops_up=pops_up)


def dismiss_bulletin(stores: Stores, bulletin_id: str) -> None:
    account_id = str(stores.identity.user_id)
    paths = default_identity_paths()
    if not bulletin_board.dismiss(paths.bulletins, account_id=account_id, bulletin_id=bulletin_id):
        raise NotFoundError(f"No bulletin {bulletin_id!r} was posted.", code="bulletin_unknown")
