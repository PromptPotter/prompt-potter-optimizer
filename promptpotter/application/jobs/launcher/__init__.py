"""Background-task launcher package — mint + spawn a campaign run from one command.

The launch orchestration (``mint_campaign_command``, ``start_run_command``,
``materialize_and_write_origin``, ``persist_origin_candidate_library``, the
reserve/admit/preflight/background-run plumbing, and the ``LaunchError`` /
``OriginIncompleteError`` types) lives in :mod:`.core`; the two durable check-in
transitions (``create_checkin_campaign`` / ``start_checkin_campaign``) +
draft load/save seams live in :mod:`.checkin`. The pure draft → on-disk-artifact
+ wire builders live in :mod:`.draft_build`.

Nothing is re-exported here — every consumer imports the leaf directly, e.g.
``from promptpotter.application.jobs.launcher.core import start_run_command``.
"""
