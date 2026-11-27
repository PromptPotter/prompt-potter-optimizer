"""What the host posts to every account, and each account's dismissals: one survivor file."""

from __future__ import annotations

from pydantic import Field

from promptpotter.domain.strict_model import FrozenModel

BULLETIN_TEXT_CHARS = 2000


class Bulletin(FrozenModel):
    id: str
    posted_at: str
    text: str = Field(min_length=1, max_length=BULLETIN_TEXT_CHARS)
    # Opens at sign-in and on every page load until the account dismisses it; else it waits in the list.
    pops_up: bool


class BulletinBoard(FrozenModel):
    # Oldest first: a post appends.
    bulletins: tuple[Bulletin, ...]
    # By the account's id, the bulletins it dismissed.
    dismissed: dict[str, tuple[str, ...]]
