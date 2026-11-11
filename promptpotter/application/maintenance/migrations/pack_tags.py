"""A pack built before its snippets' tags were refused from repeating their provenance tags each
cited snippet with its work's arXiv id and its technique family, which Source and Method already
show; those tags are dropped, every other one kept in order."""

from __future__ import annotations

import json
from pathlib import Path

from promptpotter.config.paths import BLOCK_PACKS_DIR
from promptpotter.config.prompt_blocks import PACK_FILE
from promptpotter.infrastructure.store.data_root import Staged
from promptpotter.infrastructure.store.io import read_text_optional


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    packs = projects_root.parent / BLOCK_PACKS_DIR
    for path in sorted(
        [*packs.glob(f"*/{PACK_FILE}"), *projects_root.glob(f"*/{BLOCK_PACKS_DIR}/*/{PACK_FILE}")]
    ):
        pack = json.loads(read_text_optional(path) or "{}")
        moved = False
        for snippet in pack.get("snippets", ()):
            cited = snippet.get("provenance", {})
            if cited.get("kind") != "cited":
                continue
            said = {cited.get("work"), cited.get("technique_family")}
            kept = [tag for tag in snippet.get("tags", ()) if tag not in said]
            if kept != snippet.get("tags"):
                snippet["tags"], moved = kept, True
        if moved:
            data = json.dumps(pack, indent=2, ensure_ascii=False).encode("utf-8")
            out.append(Staged(path, data, f"{path.parent.name}: tags no longer repeat provenance"))
    return out
