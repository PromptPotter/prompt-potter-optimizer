"""Docs ↔ registry sync for ``information-flow.md``.

The L1/L2 inbox table in ``docs/architecture/information-flow.md`` must
name exactly the fields declared in ``inbox_registry.INBOX`` under
``LAYER_ORDER[L1] | LAYER_ORDER[L2]``. Silent drift between code and
docs is the single most common way architectural docs rot, so we
enforce it here.
"""

from __future__ import annotations

import re
from pathlib import Path

from promptpotter.application.optimization.nodes.inbox_registry import (
    INBOX,
    LAYER_ORDER,
    Layer,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture" / "information-flow.md"


def _extract_l1_l2_table_field_names() -> list[str]:
    """Pull first-column values from the L1/L2 inbox markdown table."""
    text = DOC.read_text(encoding="utf-8")

    start_idx = text.find("## L1 / L2 inbox")
    assert start_idx != -1, "information-flow.md: missing `## L1 / L2 inbox` section"
    end_idx = text.find("### Mutex rules", start_idx)
    assert end_idx != -1, "information-flow.md: missing `### Mutex rules` section after table"
    section = text[start_idx:end_idx]

    names: list[str] = []
    for line in section.splitlines():
        m = re.match(r"\| `([a-z0-9_]+)` \|", line)
        if m:
            names.append(m.group(1))
    return names


def test_every_registry_field_appears_in_table() -> None:
    table_names = set(_extract_l1_l2_table_field_names())
    union = set(LAYER_ORDER[Layer.L1]) | set(LAYER_ORDER[Layer.L2])
    missing_in_docs = union - table_names
    assert not missing_in_docs, (
        f"information-flow.md L1/L2 table is missing registry fields: {sorted(missing_in_docs)}"
    )


def test_no_stale_field_names_in_table() -> None:
    table_names = set(_extract_l1_l2_table_field_names())
    registry_names = {f.name for f in INBOX}
    stale = table_names - registry_names
    assert not stale, (
        f"information-flow.md L1/L2 table references fields not in INBOX: {sorted(stale)}"
    )
