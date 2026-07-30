"""Noise-floor use-case — re-score a campaign's cached origin *k* times to measure
the backend's own run-to-run noise.

Not a cycle/fork/sweep and not a loop mechanism: no config field, no L1 injection, no
``detect_invariants`` exemption. This is a fenced, Claude-Code-invoked debug diagnostic —
the loop never learns it exists. Persistence mirrors ``application/verify.py``: workspace
``archive/`` sidecar only, no ledger event.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.rendering import display_fitness
from promptpotter.domain.results import DiagnosticRunRecord, candidate_label
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.statistics import mean_ci

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

__all__ = ["NoiseFloorError", "NoiseFloorOutcome", "measure_noise_floor"]


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
    identity: IdentityContext,
    campaign_id: str,
    cycle_id: str,
    k: int,
    log: Callable[[str], None] | None = None,
) -> NoiseFloorOutcome:
    """Re-score the cycle's cached round-0 origin *k* times with ``force_fresh=True``
    and report the spread as a :class:`DiagnosticRunRecord`.

    On a pp-self cycle the origin's backend IS the inner recursion, so this measures
    the true inner noise floor; on a plain cycle it measures backend run-to-run noise.
    ``k``x real spend — deliberate and operator-visible, never triggered by the loop.
    """
    from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
    from promptpotter.application.bootstrap.wiring import init_services
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.config import (
        load_campaign_config as validate_campaign_config,
    )
    from promptpotter.application.runner.inner.cycle import publish_inner_spawn_context
    from promptpotter.application.scoring.formula import split_scoring_block
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise NoiseFloorError(f"campaign {campaign_id!r} has no manifest on disk.")

    round_file = stores.campaigns.load_round_file(campaign_id, cycle_id, 0)
    if round_file is None:
        raise NoiseFloorError(
            f"round_0000.json missing in {campaign_id}/{cycle_id} — the origin was never scored."
        )
    if not round_file.all_candidate_results:
        raise NoiseFloorError(f"round_0000.json in {campaign_id}/{cycle_id} carries no origin arm.")
    origin_id = next(iter(round_file.all_candidate_results))
    origin_rows = round_file.all_candidate_results[origin_id]
    sample_ids = {int(r["sample_id"]) for r in origin_rows if r.get("sample_id") is not None}
    if not sample_ids:
        raise NoiseFloorError(
            f"origin arm in {campaign_id}/{cycle_id} round 0 carries no scored samples."
        )
    osp = OptSearchPoint.from_prompt_fields(round_file.prompt_fields)

    session = await init_services(
        backend_id=campaign.backend_id or campaign.dataset_name,
        dataset_name=campaign.dataset_name,
        identity=identity,
        store=stores,
    )
    session.campaign_id = campaign_id
    session.state.cycle_id = cycle_id
    # A pp-self origin's backend IS the inner recursion — the `promptpotter` connector
    # needs this cycle published as the spawn context before it can dispatch an inner
    # campaign per sample. Normally done once by `run_optimization`; this use-case
    # bypasses that runner, so it publishes for itself (no-op on a non-recursive cycle).
    publish_inner_spawn_context(session)

    campaign_config = validate_campaign_config(campaign.config)
    log_fn = log or (lambda *_a, **_k: None)
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log_fn)
    scoring_spec = split_scoring_block(campaign_config.scoring)
    populate_session_scoring(
        session,
        obs=None,
        scoring_formula=scoring_spec.per_sample,
        scoring_round_formula=scoring_spec.per_round,
        scorer_id=scoring_spec.scorer_id,
        cycle_id=cycle_id,
        source=f"noise_floor:{campaign_id}",
    )

    schema = session.pipeline_schema
    if schema is None:
        raise NoiseFloorError("pipeline schema unavailable; cannot resolve the origin config.")
    jsp = osp.to_job_search_point(pipeline_params, schema=schema)
    scoring_set = [s for s in session.samples if s.id in sample_ids]
    if not scoring_set:
        raise NoiseFloorError(
            f"none of round 0's {len(sample_ids)} scored sample id(s) resolve against "
            f"the current {campaign.dataset_name} bank."
        )
    config_hash = schema.sp_hash(pipeline_params or {})

    logger.info(
        "noise-floor %s/%s: re-scoring origin C0 %dx (force_fresh) on %d samples",
        campaign_id,
        cycle_id,
        k,
        len(scoring_set),
    )
    composites: list[float] = []
    accuracies: list[float] = []
    for i in range(k):
        _results, scores, _signal = await score_search_point(
            jsp,
            scoring_set,
            session,
            label=f"noise_floor_{i}",
            # ONE fixed config re-scored k times: the spread between runs IS the measurement,
            # and an opt_sp-aware term is identical across all k, so it can only add a
            # constant offset to a band that exists to isolate backend noise.
            opt_sp=None,
            on_sample_scored=lambda *_a, **_k: None,
            on_sample_starting=lambda *_a, **_k: None,
            source=f"noise_floor:{campaign_id}:C0:{i}",
            force_fresh=True,
        )
        # `scores["accuracy"]` — never `.get(..., 0.0)`. A rescore that measured nothing must
        # not enter the noise band as a 0% run; the KeyError says so.
        accuracy = float(scores["accuracy"])
        composites.append(display_fitness(scores.get("composite_fitness"), accuracy))
        accuracies.append(accuracy)
        log_fn(f"noise-floor rescore {i + 1}/{k}: composite={composites[-1]:.4f}")

    mean_composite, ci_lo, ci_hi = mean_ci(composites)

    source_accuracy = round_file.accuracy

    record = DiagnosticRunRecord(
        ts=utcnow_iso(),
        dataset=campaign.dataset_name,
        source_campaign=campaign_id,
        source_cycle=cycle_id,
        source_label=candidate_label(0, 0),
        source_candidate_id=origin_id,
        config_hash=config_hash[:12],
        samples_requested=len(scoring_set),
        samples_added=0,
        workspace_n=len(scoring_set),
        workspace_accuracy=sum(accuracies) / len(accuracies),
        workspace_composite=mean_composite,
        source_campaign_accuracy=source_accuracy,
        source_campaign_composite=round_file.composite_fitness,
        source_campaign_n=round_file.total,
        noise_floor_k=k,
        noise_floor_mean=mean_composite,
        noise_floor_ci_lo=ci_lo,
        noise_floor_ci_hi=ci_hi,
        noise_floor_raw=composites,
    )
    path = stores.diagnostic_runs.save(record)
    logger.info("noise-floor: wrote diagnostic-run record → %s", path)
    return NoiseFloorOutcome(record=record, artifact_path=str(path))
