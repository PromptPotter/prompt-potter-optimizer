from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.initialization.loop_start import (
    arm_diagnostic_scoring,
    diagnostic_pass,
    diagnostic_trace,
)
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.jobs.quota import paid_verb
from promptpotter.application.runner.inner.spawn_context import publish_inner_spawn_context
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.results import (
    DiagnosticRunRecord,
    RescoreCount,
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
    """A resolved-state failure the CLI shell maps to a clean ``SystemExit``."""


@dataclass(frozen=True)
class NoiseFloorOutcome:
    record: DiagnosticRunRecord
    artifact_path: str


async def measure_noise_floor(
    *,
    stores: Stores,
    hop: CycleHop,
    k: RescoreCount,
) -> NoiseFloorOutcome:
    async with paid_verb(stores=stores, bucket="noise-floor", hop=hop):
        return await _rescore_origin(stores, hop, k)


async def _rescore_origin(stores: Stores, hop: CycleHop, k: RescoreCount) -> NoiseFloorOutcome:
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise NoiseFloorError(f"campaign {hop.campaign_id!r} has no manifest on disk.")

    standing = stores.campaigns.standing_rounds(hop).rounds.get(0)
    if standing is None:
        raise NoiseFloorError(
            f"round 0 never closed in {hop.campaign_id}/{hop.cycle_id} — the origin was never scored."
        )
    round_file = standing.close
    if not round_file.candidate_scores:
        raise NoiseFloorError(f"round 0 of {hop.campaign_id}/{hop.cycle_id} carries no origin arm.")
    origin = round_file.origin
    sample_ids = {sample_id for _, sample_id, _, _ in round_file.cells.arms[origin.candidate_id]}
    if not sample_ids:
        raise NoiseFloorError(
            f"origin arm in {hop.campaign_id}/{hop.cycle_id} round 0 carries no scored samples."
        )

    session, campaign_config = await bind_cycle_session(stores, campaign, hop)
    # Normally `run_optimization`'s: a pp-self origin's connector dispatches no inner run without it.
    publish_inner_spawn_context(session, campaign_config)

    arm_diagnostic_scoring(session, campaign_config, source=RunSource.NOISE_FLOOR)

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
                    force_fresh=True,
                ),
            )
        accuracy, composite = scored.scores.accuracy, scored.scores.composite_fitness
        if accuracy is None or composite is None:
            raise NoiseFloorError(
                f"noise-floor rescore {i + 1}/{k} of {hop.campaign_id}/{hop.cycle_id} measured no "
                f"cell — the band would describe the outage, not the backend."
            )
        composites.append(composite)
        accuracies.append(accuracy)
        logger.info("noise-floor rescore %d/%d: composite=%.4f", i + 1, k, composite)

    band = mean_ci(composites)
    assert band is not None  # a RescoreCount is two or more
    mean_composite, ci_lo, ci_hi = band

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
