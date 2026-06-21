"""Background-task launcher package — mint + spawn a campaign run from one command.

The launch orchestration (``mint_campaign_command``, ``start_run_command``,
``materialize_and_write_origin``, ``persist_origin_candidate_library``, the
reserve/admit/preflight/background-run plumbing, and the ``LaunchError`` /
``OriginIncompleteError`` types) lives in :mod:`.core`; the two durable check-in
transitions (``create_checkin_campaign`` / ``start_checkin_campaign``) +
draft load/save seams live in :mod:`.checkin`. The pure draft → on-disk-artifact
+ wire builders live in :mod:`.draft_build`.

The public surface is re-exported here so callers keep importing from
``promptpotter.application.jobs.launcher`` unchanged.
"""

from __future__ import annotations

from promptpotter.application.jobs.launcher.checkin import (
    PreparedCheckinRun,
    create_checkin_campaign,
    load_checkin_draft,
    load_checkin_for_start,
    prepare_checkin_run,
    save_checkin_draft,
    start_checkin_campaign,
)
from promptpotter.application.jobs.launcher.core import (
    LaunchError,
    OriginIncompleteError,
    mint_campaign_command,
    persist_origin_candidate_library,
    start_run_command,
)
from promptpotter.application.jobs.launcher.draft_build import (
    derive_optimizer_locks,
    draft_pipeline_dependencies,
    draft_wire_with_locks,
    split_overlay,
)

__all__ = [
    "LaunchError",
    "OriginIncompleteError",
    "PreparedCheckinRun",
    "create_checkin_campaign",
    "derive_optimizer_locks",
    "draft_pipeline_dependencies",
    "draft_wire_with_locks",
    "load_checkin_draft",
    "load_checkin_for_start",
    "mint_campaign_command",
    "persist_origin_candidate_library",
    "prepare_checkin_run",
    "save_checkin_draft",
    "split_overlay",
    "start_checkin_campaign",
    "start_run_command",
]
