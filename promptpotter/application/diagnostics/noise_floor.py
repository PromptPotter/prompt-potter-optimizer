from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.initialization.loop_start import (
    diagnostic_pass,
    diagnostic_trace,
    open_diagnostic,
)
from promptpotter.application.jobs.launcher.admission import paid_pass
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.results import DiagnosticRunRecord, RescoreCount
from promptpotter.domain.spend import Budget
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import DiagnosticError
from promptpotter.shared.statistics import mean_ci

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


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
    async with paid_pass(stores=stores, verb="noise-floor", hop=hop, declared=Budget()):
        return await _rescore_origin(stores, hop, k)


async def _rescore_origin(stores: Stores, hop: CycleHop, k: RescoreCount) -> NoiseFloorOutcome:
    standing = stores.campaigns.standing_rounds(hop, moment=None).rounds.get(0)
    if standing is None:
        raise DiagnosticError(
            f"round 0 never closed in {hop.campaign_id}/{hop.cycle_id} — the origin was never scored."
        )
    round_file = standing.close
    if round_file.election is None:
        raise DiagnosticError(f"round 0 of {hop.campaign_id}/{hop.cycle_id} carries no origin arm.")
    origin = round_file.origin
    sample_keys = {key for key, _, _ in round_file.cells.arms[round_file.election.ended_on]}
    if not sample_keys:
        raise DiagnosticError(
            f"origin arm in {hop.campaign_id}/{hop.cycle_id} round 0 carries no scored samples."
        )

    opened = await open_diagnostic(stores, hop, source=RunSource.NOISE_FLOOR)
    session = opened.session
    schema = session.pipeline_schema
    jsp = opened.points.measured(origin)
    scoring_set = [s for s in session.bank.samples if s.key in sample_keys]
    if not scoring_set:
        raise DiagnosticError(
            f"none of round 0's {len(sample_keys)} scored sample(s) is in the bank the cycle drew."
        )
    config_hash = jsp.sp_hash(schema)
    source = round_file.fold
    if source is None or (source_composite := source.composite_fitness) is None:
        raise DiagnosticError(
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
            raise DiagnosticError(
                f"noise-floor rescore {i + 1}/{k} of {hop.campaign_id}/{hop.cycle_id} measured no "
                f"cell — the band would describe the outage, not the backend."
            )
        composites.append(composite)
        accuracies.append(accuracy)
        logger.info("noise-floor rescore %d/%d: composite=%.4f", i + 1, k, composite)

    band = mean_ci(composites)
    assert band is not None  # a RescoreCount is two or more
    mean_composite, ci_lo, ci_hi = band

    record = DiagnosticRunRecord(
        ts=utcnow_iso(),
        dataset=opened.campaign.dataset_name,
        source_campaign=hop.campaign_id,
        source_cycle=hop.cycle_id,
        source_candidate_id=origin.individual.id,
        config_hash=config_hash[:12],
        workspace_n=len(scoring_set),
        workspace_accuracy=sum(accuracies) / len(accuracies),
        source_campaign_accuracy=source.accuracy,
        source_campaign_composite=source_composite,
        source_campaign_n=source.total,
        noise_floor_k=k,
        noise_floor_mean=mean_composite,
        noise_floor_ci_lo=ci_lo,
        noise_floor_ci_hi=ci_hi,
        noise_floor_raw=composites,
    )
    path = stores.diagnostic_runs.save(record)
    logger.info("noise-floor: wrote diagnostic-run record → %s", path)
    return NoiseFloorOutcome(record=record, artifact_path=str(path))
