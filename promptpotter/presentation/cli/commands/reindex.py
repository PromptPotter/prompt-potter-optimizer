"""``reindex`` — rebuild the measurement index from the detail files and GC orphans.

The measurement index (``measurements/index.jsonl``) is a *derived* read model over
the ``{run_id}.json`` detail files, so it can always be reconstructed from them and
loses nothing. This verb does exactly that: fold every detail file, keep the latest
run per ``content_hash``, rewrite a compacted index, and delete detail files no index
row references. Use it after a crash mid-append, after hand-editing the store, or to
reclaim orphaned detail bytes. Pure disk work — zero LLM spend, no cycle mutation.
"""

from __future__ import annotations

import argparse
import logging

from promptpotter.infrastructure.store import archive_views, build_stores
from promptpotter.presentation.cli.commands._shared import CommandResult, identity_from_args

logger = logging.getLogger("promptpotter.presentation.cli.reindex")

__all__ = ["cmd_reindex"]


async def cmd_reindex(args: argparse.Namespace) -> CommandResult:
    """Rebuild ``measurements/index.jsonl`` from the detail files; report the counts."""
    stores = build_stores(identity_from_args(args))
    counts = archive_views.reindex_measurements(stores)
    human = (
        f"reindex: {counts['indexed']} run(s) indexed from "
        f"{counts['details_scanned']} detail file(s); "
        f"{counts['orphans_removed']} orphan(s) removed."
    )
    return CommandResult(data=counts, human=human)
