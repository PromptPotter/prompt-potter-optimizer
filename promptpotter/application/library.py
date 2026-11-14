from __future__ import annotations

import datetime
from typing import Annotated, Literal

from pydantic import Field

from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.domain.prompt_block import (
    Authored,
    BlockPack,
    Cited,
    Presumption,
    PromptPart,
    Snippet,
    snippet_id,
)
from promptpotter.domain.refusal import Refusal
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.account_packs import bank_into_account_pack
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError
from promptpotter.shared.identity import acting_principal


class LibrarySource(StrictModel):
    """One cited work a snippet names, as its pack declares it; an authored snippet names none."""

    id: str = Field(description="The work's arXiv id, a cited snippet's `source`")
    title: str
    short_title: str = Field(description="The title up to its first colon, for a chip")
    first_author: str
    year: int


class LibraryCited(StrictModel):
    """A snippet its pack cites from a published work."""

    kind: Literal["cited"]
    source: str = Field(description="The work's arXiv id, a `sources` id")
    method: str = Field(description="The cited technique's family as its pack records it")


class LibraryAuthored(StrictModel):
    """A snippet an account wrote: a version of the one it was edited from, or written from nothing."""

    kind: Literal["authored"]
    author: str
    version: int = Field(
        ge=1, description="One past the snippet it supersedes, a cited one counting 0"
    )
    supersedes: str | None = Field(
        description="The `id` of the snippet it was edited from; null for one written from nothing"
    )


class LibrarySnippet(StrictModel):
    """One snippet of one pack the account reads, as a library surface lists it."""

    id: str = Field(
        description="Its content id, the suffix of each pool entry `{part}.{id}` it expands into; "
        "one on the shelf, since a pack repeating another's text is refused"
    )
    text: str
    suits: list[PromptPart] = Field(
        description="The prompt parts it suits, in `PROMPT_FIELDS` order: a drop onto another "
        "part is allowed and shown as such"
    )
    presumes: list[Presumption] = Field(
        description="What it takes for granted of the run it lands in: a run whose chain or "
        "matcher lacks one never draws it into a pool"
    )
    tags: list[str] = Field(description="Free labels for search alone; nothing else reads them")
    pack: str = Field(description="The pack holding it, as a `packs` knob names it")
    provenance: Annotated[LibraryCited | LibraryAuthored, Field(discriminator="kind")]


class LibraryResponse(StrictModel):
    """Every snippet the account reads: the install's packs beside its own, for target and optimizer prompts alike."""

    parts: list[PromptPart] = Field(
        description="The closed set `suits` draws from, in `PROMPT_FIELDS` order"
    )
    sources: list[LibrarySource] = Field(
        description="Every work a cited snippet names, once, in first-named order"
    )
    snippets: list[LibrarySnippet] = Field(
        description="The install's packs by name, then the account's, each in its own order"
    )
    refused: list[Refusal] = Field(
        description="Each pack held but served none of, then each snippet of the account's "
        "skipped as an install pack's, and why, its pack in `details.pack`: the rest serve on"
    )


def library(shelf: PackShelf) -> LibraryResponse:
    snippets: list[LibrarySnippet] = []
    sources: dict[str, LibrarySource] = {}
    refused: list[Refusal] = []
    for name, pack in shelf.shelved().items():
        if isinstance(pack, Refusal):
            refused.append(pack)
            continue
        for s in pack.snippets:
            provenance: LibraryCited | LibraryAuthored
            match s.provenance:
                case Cited(work=cited, technique_family=method):
                    provenance = LibraryCited(kind="cited", source=cited, method=method)
                    if cited not in sources:
                        work = pack.works[cited]
                        sources[cited] = LibrarySource(
                            id=cited,
                            title=work.title,
                            short_title=work.title.split(":", 1)[0],
                            first_author=work.first_author,
                            year=work.year,
                        )
                case Authored(author=author, version=version, supersedes=supersedes):
                    provenance = LibraryAuthored(
                        kind="authored", author=author, version=version, supersedes=supersedes
                    )
            snippets.append(
                LibrarySnippet(
                    id=snippet_id(s.text),
                    text=s.text,
                    suits=list(s.parts),
                    presumes=sorted(s.presumes),
                    tags=list(s.tags),
                    pack=name,
                    provenance=provenance,
                )
            )
    return LibraryResponse(
        parts=list(PromptPart),
        sources=list(sources.values()),
        snippets=snippets,
        refused=[*refused, *shelf.skipped()],
    )


def _written_in(stores: Stores, experiment: str) -> None:
    """A provenance names a campaign or a draft of the caller's own, never another account's."""
    campaign = stores.campaigns.load_campaign(experiment)
    if campaign is None:
        owned_draft(stores, experiment)


def bank_snippet(stores: Stores, *, supersedes: str, text: str, experiment: str) -> None:
    """An edit of a library snippet banked into the account's own pack as its next version."""
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
