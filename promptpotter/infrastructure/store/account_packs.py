from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.config.settings import LOCK_TIMEOUT
from promptpotter.domain.prompt_block import ACCOUNT_PACK, BlockPack
from promptpotter.infrastructure.store.io import (
    held_lock,
    read_bytes_optional,
    write_lock_name,
    write_text,
)

if TYPE_CHECKING:
    from promptpotter.config.prompt_blocks import PackShelf
    from promptpotter.domain.prompt_block import Snippet


def bank_into_account_pack(shelf: PackShelf, snippet: Snippet) -> None:
    """Read and rewritten under the pack's lock, so two banks at once keep both; a text it holds stays one snippet."""
    file = shelf.account_file(ACCOUNT_PACK)
    with held_lock(file.with_name(write_lock_name(file.name)), wait_s=LOCK_TIMEOUT):
        raw = read_bytes_optional(file)
        # Absent until its first bank: an account's pack is made by writing into it.
        held = (
            BlockPack(works={}, snippets=()) if raw is None else BlockPack.model_validate_json(raw)
        )
        if any(s.text == snippet.text for s in held.snippets):
            return
        banked = BlockPack(works=held.works, snippets=(*held.snippets, snippet))
        write_text(file, banked.model_dump_json(indent=2))
