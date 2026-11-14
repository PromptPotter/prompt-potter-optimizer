from __future__ import annotations

from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.domain.prompt_block import ACCOUNT_PACK, BlockPack, Snippet
from promptpotter.infrastructure.store.io import write_lock, write_text


def bank_into_account_pack(shelf: PackShelf, snippet: Snippet) -> None:
    """Read fresh and rewritten under the pack's lock, so two banks at once keep both; a text any
    pack on the shelf holds is that snippet already, so one text stays one snippet."""
    file = shelf.account_file(ACCOUNT_PACK)
    with write_lock(file):
        locked = PackShelf(account=shelf.account)
        if any(
            s.text == snippet.text
            for p in locked.shelved().values()
            if isinstance(p, BlockPack)
            for s in p.snippets
        ):
            return
        held = locked.written(ACCOUNT_PACK)
        banked = BlockPack(works=held.works, snippets=(*held.snippets, snippet))
        write_text(file, banked.model_dump_json(indent=2))
