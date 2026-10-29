from __future__ import annotations

from pydantic import Field

from promptpotter.config.prompt_blocks import installed_packs, read_pack
from promptpotter.domain.prompt_block import PromptPart, snippet_id
from promptpotter.domain.strict_model import StrictModel


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
    source: str = Field(description="Its cited work's arXiv id, or `authored`")
    pack: str = Field(description="The installed pack holding it, as a `packs` knob names it")


class LibraryResponse(StrictModel):
    """Every snippet the installed block packs hold, one library for target and optimizer prompts alike."""

    parts: list[PromptPart] = Field(
        description="The closed set `suits` draws from, in `PROMPT_FIELDS` order"
    )
    snippets: list[LibrarySnippet] = Field(description="By pack name, then each pack's own order")


def library() -> LibraryResponse:
    return LibraryResponse(
        parts=list(PromptPart),
        snippets=[
            LibrarySnippet(
                id=snippet_id(s.text),
                text=s.text,
                suits=list(s.parts),
                tags=list(s.tags),
                source=s.source,
                pack=name,
            )
            for name in installed_packs()
            for s in read_pack(name).snippets
        ],
    )
