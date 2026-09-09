"""``restamp`` — a thin shell over ``application/restamp.py``, which carries the rationale and the two tree shapes.
Dry-run by default; ``--apply`` rewrites."""

from __future__ import annotations

import argparse

from promptpotter.application.restamp import (
    backfill_inner_facts,
    check_round_documents,
    compact_cycle_ledgers,
    lift_campaign_pipeline_config,
    rekey_overlap_results,
    rename_campaign_pipeline_overlay,
    rename_round_trend,
    reproject_cycle_indexes,
    restamp_campaign_configs,
    shrink_measurement_runs,
    stamp_campaign_backend_type,
    stamp_election_bias,
    stamp_election_objective,
)
from promptpotter.presentation.cli.commands._shared import CommandResult

__all__ = ["cmd_restamp"]


async def cmd_restamp(args: argparse.Namespace) -> CommandResult:
    apply = bool(getattr(args, "apply", False))
    # FIRST of all: the prune below drops a key `CampaignConfig` no longer declares, and this
    # rename is the only thing that carries the values across before it can.
    renamed = rename_campaign_pipeline_overlay(apply=apply)
    # BEFORE the config re-stamp: that pass prunes the same document to `Campaign.model_fields`,
    # and a manifest read twice in one invocation should be read in its final shape the second time.
    kinds = stamp_campaign_backend_type(apply=apply)
    # Also before the config re-stamp, and for a second reason: it WRITES into
    # `campaign.json::config`, which that pass then prunes and re-freezes as a delta.
    configs = lift_campaign_pipeline_config(apply=apply)
    counts = restamp_campaign_configs(apply=apply)
    ledgers = compact_cycle_ledgers(apply=apply)
    runs = shrink_measurement_runs(apply=apply)
    indexes = reproject_cycle_indexes(apply=apply)
    elections = stamp_election_bias(apply=apply)
    grades = stamp_election_objective(apply=apply)
    overlaps = rekey_overlap_results(apply=apply)
    inner = backfill_inner_facts(apply=apply)
    trend = rename_round_trend(apply=apply)
    # Read-only, so --apply does not change what it does.
    rounds = check_round_documents()
    verb = "re-stamped" if apply else "would re-stamp"
    runs_line = (
        f"Measurement rows: skipped, {runs['archive_writers']} cycle(s) can still append. "
        if runs["archive_writers"]
        else f"Measurement rows: {runs['runs_shrunk']} run(s), "
        f"{runs['run_bytes_saved'] / (1024 * 1024):.1f} MB reclaimed. "
    )
    human = (
        f"restamp: {verb} {counts['rewritten']} file(s); "
        f"{counts['failed']} still invalid, {counts['skipped']} unreadable. "
        f"Ledgers: {ledgers['cycles']} cycle(s), "
        f"{ledgers['bytes_saved'] / (1024 * 1024):.1f} MB reclaimed, "
        f"{ledgers['record_keys_dropped']} record key(s) the engine no longer declares pruned "
        f"off — every line carrying one was being skipped whole by the reader "
        f"({ledgers['skipped_producing']} producing + "
        f"{ledgers['skipped_checkin']} pre-loop, left alone). "
        f"Rounds: {rounds['rounds_checked'] - rounds['rounds_unreadable']}"
        f"/{rounds['rounds_checked']} load. "
        f"{runs_line}"
        f"Cycle indexes: {indexes['cycle_indexes_reprojected']}"
        f"/{indexes['cycle_indexes']} re-derived from their round documents. "
        f"Elections: parent_bias {verb} onto {elections['elections_stamped']} decision(s) so a "
        f"replay reads the bar each one ran under; objective {verb} onto "
        f"{grades['election_cells_graded']} recorded parent cell(s) — without it the replay "
        f"raises and `resume` reports a divergence that never happened "
        f"({grades['election_cells_ungraded']} cell(s) no round document can grade, left absent). "
        f"Overlap rows: {overlaps['overlap_rows_rekeyed']} row(s) keyed onto the individual they "
        f"measured across {overlaps['overlap_documents']} round document(s), without which the "
        f"document does not load at all ({overlaps['overlap_rows_dropped']} row(s) no reading "
        f"names an arm for, dropped). "
        f"Inner seed facts {verb} onto {inner['inner_rows_filled']} row(s) from the inner "
        f"campaigns themselves; {inner['inner_rows_orphaned']} cell(s) no longer have one on "
        f"disk and stay absent. Peak lift and round budget are never backfilled — no surviving "
        f"record reproduces them exactly. "
        f"Round trend: {trend['trend_keys_moved']} key(s) {'moved' if apply else 'to move'} off "
        f"the retired `trajectory` spelling, which a resume would otherwise read as `healthy`. "
        f"Connector kind: backend_type {verb} onto {kinds['backend_types_stamped']} campaign "
        f"manifest(s) so a campaign says what it RAN rather than what its dataset file says today "
        f"({kinds['backend_types_current']} already frozen, {kinds['backend_types_orphaned']} whose "
        f"dataset dir is gone and which nothing on disk can answer for). "
        f"Node config: {'lifted' if apply else 'would lift'} onto "
        f"{configs['pipeline_configs_lifted']} campaign(s) — "
        f"{configs['pipeline_configs_from_rounds']} off round 0, the config they were MEASURED "
        f"under, {configs['pipeline_configs_from_dataset']} off the dataset file because they "
        f"never ran. Until a campaign carries its own, an edit to the shared "
        f"`pipeline.yaml` silently re-answers for it "
        f"({configs['pipeline_configs_current']} already did; "
        f"{configs['pipeline_configs_identity_unknown']} skipped because their connector's "
        f"identity keys cannot be read on this box — re-run where its extra is installed). "
        f"Campaign delta: pipeline_overrides -> pipeline_overlay on "
        f"{renamed['pipeline_overlay_renamed']} manifest(s), one name for the per-node delta at "
        f"every layer that carries one. Without this the prune above would drop the key WITH its "
        f"values, silently un-setting every model and temperature a campaign held."
    )
    return CommandResult(
        data={
            **renamed,
            **kinds,
            **configs,
            **counts,
            **ledgers,
            **runs,
            **rounds,
            **indexes,
            **elections,
            **grades,
            **overlaps,
            **inner,
            **trend,
        },
        human=human,
    )
