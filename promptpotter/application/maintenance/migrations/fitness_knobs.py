"""Respells `accuracy` as `fitness` in an authored definition's knobs."""

from __future__ import annotations

import re
from pathlib import Path

from promptpotter.infrastructure.store.data_root import Staged
from promptpotter.infrastructure.store.dataset_access import DEFINITION_FILE
from promptpotter.infrastructure.store.tenant_dataset_store import TenantDatasetStore
from promptpotter.infrastructure.store.user_store import account_dirs

_RESPELLED = (
    (
        re.compile(r"""\b(display_metric['"]?\s*:\s*)(['"]?)accuracy(?![\w-])\2"""),
        r"\1\2fitness\2",
    ),
    (re.compile(r"\bhalt_at_accuracy(?![\w-])"), "halt_at_fitness"),
    (re.compile(r"\baccuracy_ceiling(?![\w-])"), "fitness_ceiling"),
)


def staged(projects_root: Path) -> list[Staged]:
    out: list[Staged] = []
    for workspace in account_dirs(projects_root):
        root = TenantDatasetStore(workspace).committed_datasets_root()
        for path in sorted(root.glob(f"**/{DEFINITION_FILE}")):
            said = path.read_text(encoding="utf-8")
            respelled = said
            for pattern, spelling in _RESPELLED:
                respelled = pattern.sub(spelling, respelled)
            if respelled != said:
                out.append(
                    Staged(
                        path, respelled.encode("utf-8"), f"{path.parent.name}: knobs say fitness"
                    )
                )
    return out
