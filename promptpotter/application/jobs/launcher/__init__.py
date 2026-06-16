"""Background-task launcher package — mint + spawn a campaign run from one command.

The launch / commit orchestration (``mint_campaign_command``,
``start_run_command``, ``commit_draft_to_dataset``,
``mint_campaign_from_draft_command``, ``persist_origin_candidate_library``,
the reserve/admit/preflight/background-run plumbing, and the ``LaunchError`` /
``OriginIncompleteError`` types) lives in :mod:`.core`. The pure
draft → on-disk-artifact + wire builders live in :mod:`.draft_build`.

The public surface is re-exported here so callers keep importing from
``promptpotter.application.jobs.launcher`` unchanged.
"""

from __future__ import annotations

from promptpotter.application.jobs.launcher.core import (
    LaunchError,
    OriginIncompleteError,
    commit_draft_to_dataset,
    mint_campaign_command,
    mint_campaign_from_draft_command,
    persist_origin_candidate_library,
    start_run_command,
)
from promptpotter.application.jobs.launcher.draft_build import (
    derive_optimizer_locks,
    draft_pipeline_dependencies,
    draft_wire_with_locks,
    merge_pipeline_overlay,
    split_overlay,
)

__all__ = [
    "LaunchError",
    "OriginIncompleteError",
    "commit_draft_to_dataset",
    "derive_optimizer_locks",
    "draft_pipeline_dependencies",
    "draft_wire_with_locks",
    "merge_pipeline_overlay",
    "mint_campaign_command",
    "mint_campaign_from_draft_command",
    "persist_origin_candidate_library",
    "split_overlay",
    "start_run_command",
]
