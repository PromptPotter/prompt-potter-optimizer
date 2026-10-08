"""Re-score a cached origin *k* times to measure the backend's own run-to-run noise. A fenced debug diagnostic,
not a loop mechanism — the loop never learns it exists, and persistence is a ``diagnostics/`` sidecar only."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.initialization.loop_start import (
    arm_diagnostic_scoring,
    diagnostic_pass,
    diagnostic_trace,
)
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.results import (
    DiagnosticRunRecord,
    candidate_label,
    diagnostic_held,
)
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.statistics import mean_ci

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = ["NoiseFloorError", "measure_noise_floor"]


class NoiseFloorError(Exception):
    """A resolved-state failure: campaign/cycle/round-0 missing on disk, or the
    pipeline schema is unavailable. The CLI shell maps this to a clean ``SystemExit``."""


@dataclass(frozen=True)
class NoiseFloorOutcome:
    record: DiagnosticRunRecord
    artifact_path: str


async def measure_noise_floor(
    *,
    stores: Stores,
    hop: CycleHop,
    k: int,
    log: Callable[[str], None] | None = None,
) -> NoiseFloorOutcome:
    """Re-score the cached round-0 origin *k* times with ``force_fresh`` and report the spread. On a pp-self cycle the
    origin's backend IS the recursion, so this reads the inner noise floor. ``k``× real spend, never loop-triggered."""

    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NoiseFloorError(f"campaign {hop.campaign_id!r} has no manifest on disk.")

    round_file = stores.campaigns.load_round_file(hop, 0)
    if round_file is None:
        raise NoiseFloorError(
            f"round_0000.json missing in {hop.campaign_id}/{hop.cycle_id} — the origin was never scored."
        )
    if not round_file.candidate_scores:
        raise NoiseFloorError(
            f"round_0000.json in {hop.campaign_id}/{hop.cycle_id} carries no origin arm."
        )
    origin = round_file.origin
    origin_rows = round_file.all_candidate_results[origin.candidate_id]
    sample_ids = {int(r["sample_id"]) for r in origin_rows if r.get("sample_id") is not None}
    if not sample_ids:
        raise NoiseFloorError(
            f"origin arm in {hop.campaign_id}/{hop.cycle_id} round 0 carries no scored samples."
        )

    session, campaign_config = await bind_cycle_session(stores, campaign, hop)
    # A pp-self origin's backend IS the inner recursion — the `promptpotter` connector
    # needs this cycle published as the spawn context before it can dispatch an inner
    # campaign per sample. Normally done once by `run_optimization`; this use-case
    # bypasses that runner, so it publishes for itself (no-op on a non-recursive cycle).
    publish_inner_spawn_context(session, campaign_config)

    log_fn = log or (lambda *_a, **_k: None)
    arm_diagnostic_scoring(session, campaign_config, source=RunSource.NOISE_FLOOR, log=log_fn)

    schema = session.pipeline_schema
    jsp = origin.searchpoint(
        schema=schema,
        framing=campaign_framing(stores, campaign_config, campaign.dataset_name),
        demo=session.scoring.require_partition().demo,
    )
    scoring_set = [s for s in session.samples if s.id in sample_ids]
    if not scoring_set:
        raise NoiseFloorError(
            f"none of round 0's {len(sample_ids)} scored sample id(s) resolve against "
            f"the current {campaign.dataset_name} bank."
        )
    config_hash = jsp.sp_hash(schema)
    if (source_composite := round_file.composite_fitness) is None:
        raise NoiseFloorError(
            f"round 0 of {hop.campaign_id}/{hop.cycle_id} read no cell, so there is no recorded "
            "composite for the band to stand beside."
        )

    logger.info(
        "noise-floor %s/%s: re-scoring origin C0 %dx (force_fresh) on %d samples",
        hop.campaign_id,
        hop.cycle_id,
        k,
        len(scoring_set),
    )
    composites: list[float] = []
    accuracies: list[float] = []
    for i in range(k):
        with diagnostic_trace(stores, hop):
            scored = await diagnostic_pass(
                NoiseFloorError,
                score_search_point(
                    jsp,
                    scoring_set,
                    session,
                    label=f"noise_floor_{i}",
                    measured=None,
                    on_sample_scored=lambda *_a, **_k: None,
                    on_sample_starting=lambda *_a, **_k: None,
                    force_fresh=True,
                ),
            )
        accuracy, composite = scored.scores["accuracy"], scored.scores["composite_fitness"]
        if accuracy is None or composite is None:
            raise NoiseFloorError(
                f"noise-floor rescore {i + 1}/{k} of {hop.campaign_id}/{hop.cycle_id} measured no "
                f"cell — the band would describe the outage, not the backend."
            )
        composites.append(composite)
        accuracies.append(accuracy)
        log_fn(f"noise-floor rescore {i + 1}/{k}: composite={composites[-1]:.4f}")

    mean_composite, ci_lo, ci_hi = mean_ci(composites)

    source_accuracy = round_file.accuracy
    workspace_accuracy = sum(accuracies) / len(accuracies)

    record = DiagnosticRunRecord(
        ts=utcnow_iso(),
        dataset=campaign.dataset_name,
        source_campaign=hop.campaign_id,
        source_cycle=hop.cycle_id,
        source_label=candidate_label(0, 0),
        source_candidate_id=origin.candidate_id,
        config_hash=config_hash[:12],
        samples_requested=len(scoring_set),
        samples_added=0,
        workspace_n=len(scoring_set),
        workspace_accuracy=workspace_accuracy,
        workspace_composite=mean_composite,
        source_campaign_accuracy=source_accuracy,
        source_campaign_composite=source_composite,
        source_campaign_n=round_file.total,
        held=diagnostic_held(workspace_accuracy, source_accuracy),
        noise_floor_k=k,
        noise_floor_mean=mean_composite,
        noise_floor_ci_lo=ci_lo,
        noise_floor_ci_hi=ci_hi,
        noise_floor_raw=composites,
    )
    path = stores.diagnostic_runs.save(record)
    logger.info("noise-floor: wrote diagnostic-run record → %s", path)
    return NoiseFloorOutcome(record=record, artifact_path=str(path))
