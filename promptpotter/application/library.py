from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from pydantic import Field

from promptpotter.domain.prompt_block import (
    AUTHORED_SOURCE,
    Authored,
    BlockPack,
    Cited,
    PackRefusal,
    PromptPart,
    Snippet,
    snippet_id,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.account_packs import bank_into_account_pack
from promptpotter.infrastructure.store.stores import own_campaign, owned_draft_data
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.identity import acting_principal

if TYPE_CHECKING:
    from promptpotter.config.prompt_blocks import PackShelf
    from promptpotter.infrastructure.store.stores import Stores


class LibrarySource(StrictModel):
    """One source a snippet names: a cited work as its pack declares it, or the authored one."""

    id: str = Field(description="A snippet's `source`: the work's arXiv id, or `authored`")
    title: str = Field(description="The work's title; for `authored`, the word itself")
    short_title: str = Field(description="The title up to its first colon, for a chip")
    first_author: str | None = Field(description="Absent for `authored`")
    year: int | None = Field(description="Absent for `authored`")


class LibrarySnippet(StrictModel):
    """One snippet of one pack the account reads, as a library surface lists it."""

    id: str = Field(
        description="Its content id, the suffix of each pool entry `{part}.{id}` it expands into; "
        "one text in two packs is two rows under one id"
    )
    text: str
    suits: list[PromptPart] = Field(
        description="The prompt parts it suits, in `PROMPT_FIELDS` order: a drop onto another "
        "part is allowed and shown as such"
    )
    tags: list[str] = Field(description="Free labels for search alone; nothing else reads them")
    source: str = Field(description="A `sources` id: its cited work's arXiv id, or `authored`")
    method: str | None = Field(
        description="The cited technique's family as its pack records it; absent for an authored snippet"
    )
    pack: str = Field(description="The pack holding it, as a `packs` knob names it")
    version: int = Field(
        description="0 for a cited snippet; an authored one's is one past the snippet it supersedes"
    )
    supersedes: str | None = Field(
        description="The `id` of the snippet it was edited from; absent for a cited snippet and "
        "one written from nothing"
    )
    author: str | None = Field(description="Who wrote an authored snippet; absent for a cited one")


class LibraryResponse(StrictModel):
    """Every snippet the account reads: the install's packs beside its own, for target and optimizer prompts alike."""

    parts: list[PromptPart] = Field(
        description="The closed set `suits` draws from, in `PROMPT_FIELDS` order"
    )
    sources: list[LibrarySource] = Field(
        description="Every source a snippet names, once, in first-named order"
    )
    snippets: list[LibrarySnippet] = Field(description="By pack name, then each pack's own order")
    refused: list[PackRefusal] = Field(
        description="Each pack held but served none of, by name, and why: the rest serve on"
    )


def library(shelf: PackShelf) -> LibraryResponse:
    snippets: list[LibrarySnippet] = []
    sources: dict[str, LibrarySource] = {}
    refused: list[PackRefusal] = []
    for name, pack in shelf.shelved().items():
        if isinstance(pack, PackRefusal):
            refused.append(pack)
            continue
        for s in pack.snippets:
            cited = s.provenance if isinstance(s.provenance, Cited) else None
            authored = s.provenance if isinstance(s.provenance, Authored) else None
            if s.source not in sources:
                work = pack.works[cited.work] if cited else None
                title = work.title if work else AUTHORED_SOURCE
                sources[s.source] = LibrarySource(
                    id=s.source,
                    title=title,
                    short_title=title.split(":", 1)[0],
                    first_author=work.first_author if work else None,
                    year=work.year if work else None,
                )
            snippets.append(
                LibrarySnippet(
                    id=snippet_id(s.text),
                    text=s.text,
                    suits=list(s.parts),
                    tags=list(s.tags),
                    source=s.source,
                    method=cited.technique_family if cited else None,
                    pack=name,
                    version=s.version,
                    supersedes=authored.supersedes if authored else None,
                    author=authored.author if authored else None,
                )
            )
    return LibraryResponse(
        parts=list(PromptPart), sources=list(sources.values()), snippets=snippets, refused=refused
    )


def _written_in(stores: Stores, experiment: str) -> None:
    """A provenance names a campaign or a draft of the caller's own, never another account's."""
    if stores.campaigns.campaign_owner(experiment) is not None:
        own_campaign(stores, experiment)
    else:
        owned_draft_data(stores, experiment)


def bank_snippet(stores: Stores, *, supersedes: str, text: str, experiment: str) -> None:
    """An edit of a library snippet banked into the account's own pack as its next version; a text the library already holds is that snippet."""
    if snippet_id(text) == supersedes:
        raise PayloadInvalidError(
            "the text is the snippet's own: an unchanged edit is no new version",
            code="snippet_unchanged",
        )
    shelf = stores.block_packs
    held = {
        snippet_id(s.text): s
        for pack in shelf.shelved().values()
        if isinstance(pack, BlockPack)
        for s in pack.snippets
    }
    parent = held.get(supersedes)
    if parent is None:
        raise NotFoundError(f"no snippet {supersedes!r} in this library", code="snippet_not_found")
    _written_in(stores, experiment)
    if snippet_id(text) in held:
        return
    bank_into_account_pack(
        shelf,
        Snippet(
            text=text,
            suits=parent.suits,
            tags=parent.tags,
            presumes=parent.presumes,
            provenance=Authored(
                kind="authored",
                author=acting_principal(stores.identity).display,
                date=datetime.datetime.now(datetime.UTC).date(),
                experiment=experiment,
                version=parent.version + 1,
                supersedes=supersedes,
                notes=None,
            ),
        ),
    )
