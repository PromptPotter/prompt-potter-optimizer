from __future__ import annotations

from pydantic import Field

from promptpotter.config.prompt_blocks import installed_packs, read_pack
from promptpotter.domain.prompt_block import AUTHORED_SOURCE, Cited, PromptPart, snippet_id
from promptpotter.domain.strict_model import StrictModel


class LibrarySource(StrictModel):
    """One source a snippet names: a cited work as its pack declares it, or the authored one."""

    id: str = Field(description="A snippet's `source`: the work's arXiv id, or `authored`")
    title: str = Field(description="The work's title; for `authored`, the word itself")
    short_title: str = Field(description="The title up to its first colon, for a chip")
    first_author: str | None = Field(description="Absent for `authored`")
    year: int | None = Field(description="Absent for `authored`")


class LibrarySnippet(StrictModel):
    """One snippet of one installed pack, as a library surface lists it."""

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
    pack: str = Field(description="The installed pack holding it, as a `packs` knob names it")


class LibraryResponse(StrictModel):
    """Every snippet the installed block packs hold, one library for target and optimizer prompts alike."""

    parts: list[PromptPart] = Field(
        description="The closed set `suits` draws from, in `PROMPT_FIELDS` order"
    )
    sources: list[LibrarySource] = Field(
        description="Every source a snippet names, once, in first-named order"
    )
    snippets: list[LibrarySnippet] = Field(description="By pack name, then each pack's own order")


def library() -> LibraryResponse:
    snippets: list[LibrarySnippet] = []
    sources: dict[str, LibrarySource] = {}
    for name in installed_packs():
        pack = read_pack(name)
        for s in pack.snippets:
            cited = s.provenance if isinstance(s.provenance, Cited) else None
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
                )
            )
    return LibraryResponse(
        parts=list(PromptPart), sources=list(sources.values()), snippets=snippets
    )
