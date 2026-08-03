"""Verify use-case — re-score one campaign candidate on N additional samples.

Not a cycle/fork/sweep: no ledger event, no round_id; persistence is into the
workspace ``archive/`` tree only. The CLI shell
(``presentation/cli/commands/verify.py``) owns arg-parsing, needle resolution
(campaign/cycle/label), and human-string formatting; this module owns
resolving the candidate off disk, scoring it, and assembling the
``DiagnosticRunRecord``.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.rendering import display_fitness
from promptpotter.domain.results import DiagnosticRunRecord
from promptpotter.infrastructure.store import archive_views
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.domain.sample import Measurement
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.shared.identity import IdentityContext

logger = logging.getLogger(__name__)

__all__ = ["VerifyError", "VerifyOutcome", "verify_candidate"]


class VerifyError(Exception):
    """A resolved-state failure: campaign/round/candidate missing on disk, or
    the pipeline schema is unavailable. The CLI shell maps this to a clean
    ``SystemExit`` — this module never raises ``SystemExit`` itself."""


@dataclass(frozen=True)
class VerifyOutcome:
    """``record`` is ``None`` on the "every sample already measured" path — both
    human-readable verdict strings are formatted by the CLI shell from this
    outcome, never here.
    """

    dataset_name: str
    already_measured: int
    record: DiagnosticRunRecord | None = None
    cache_replays: int = 0


def _archive_measurement_to_qm(m: Measurement) -> QueryMeasurement:
    return cast(
        "QueryMeasurement",
        {
            "sample_id": m.sample_id,
            "query": m.query,
            "ground_truth": m.ground_truth,
            "predicted": m.predicted,
            "fitness": m.fitness,
            "error": None,
            "pipeline_data": m.pipeline_data or {},
        },
    )


async def verify_candidate(
    *,
    stores: Stores,
    identity: IdentityContext,
    campaign_id: str,
    cycle_id: str,
    round_num: int,
    cand_idx: int,
    label: str,
    samples: int,
    seed: int | None,
    log: Callable[[str], None] | None = None,
) -> VerifyOutcome:
    """Re-score the ``C{round_num}.{cand_idx+1}`` candidate on *samples* unmeasured samples.

    Raises :class:`VerifyError` when the candidate can't be resolved off disk
    (missing campaign manifest / round file / candidate / pipeline schema).
    """
    from promptpotter.application.bootstrap.scoring_context import populate_session_scoring
    from promptpotter.application.bootstrap.wiring import init_services
    from promptpotter.application.config import configure_and_apply_pipeline
    from promptpotter.application.config import (
        load_campaign_config as validate_campaign_config,
    )
    from promptpotter.application.optimization.l1.population import merge_pipeline_params
    from promptpotter.application.scoring.formula import rescore_results, split_scoring_block
    from promptpotter.application.scoring.metrics import compute_composite_fitness
    from promptpotter.application.scoring.search_point_scorer import score_search_point

    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise VerifyError(f"campaign {campaign_id!r} has no manifest on disk.")

    if round_num == 0:
        raise VerifyError(
            "verifying C0 (origin) is not implemented yet — "
            "the origin's prompt fields don't live in the round-candidate cache. "
            "Pass a C{round}.{n} label instead."
        )
    _cached = stores.campaigns.load_round_candidates(campaign_id, cycle_id, round_num)
    proposals = _cached[0] if _cached else None
    if not proposals:
        raise VerifyError(
            f"no cached candidates for round {round_num} in "
            f"{campaign_id}/{cycle_id} — looked under .runtime/cache/candidates/."
        )
    if cand_idx >= len(proposals):
        raise VerifyError(
            f"round {round_num} only has {len(proposals)} candidates; "
            f"{label!r} requested index {cand_idx + 1}."
        )
    proposal = proposals[cand_idx]
    opt_sp = OptSearchPoint.model_validate(proposal["opt_sp"])
    pp_override = proposal.get("pipeline_params_override") or {}

    round_file = stores.campaigns.load_round_file(campaign_id, cycle_id, round_num)
    if round_file is None:
        raise VerifyError(f"round_{round_num:04d}.json missing in {campaign_id}/{cycle_id}.")
    cand_scores = round_file.candidate_scores
    cand_score = next((c for c in cand_scores if c.label == label), None)
    if cand_score is None:
        raise VerifyError(
            f"round_{round_num:04d}.json carries labels "
            f"{[c.label for c in cand_scores]} — none match {label!r}."
        )
    source_campaign_accuracy = cand_score.accuracy
    source_campaign_composite = cand_score.composite_fitness
    source_campaign_n = cand_score.scored_samples
    source_candidate_id = cand_score.candidate_id

    session = await init_services(
        backend_id=campaign.backend_id or campaign.dataset_name,
        dataset_name=campaign.dataset_name,
        identity=identity,
        store=stores,
    )
    session.campaign_id = campaign_id
    session.state.cycle_id = cycle_id

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
        source=f"verify:{campaign_id}:{label}",
    )

    schema = session.pipeline_schema
    effective_pipeline_params = merge_pipeline_params(pipeline_params, pp_override, schema) or {}
    jsp = opt_sp.to_job_search_point(effective_pipeline_params, schema=schema)
    node_configs = schema.node_configs(effective_pipeline_params)
    predicate: dict[str, dict[str, Any]] = dict(node_configs)
    config_hash = schema.sp_hash(effective_pipeline_params)

    # Find samples this exact config has not yet been measured on.
    prior = archive_views.measurements_for_config(
        stores,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    measured_ids = {m.sample_id for m in prior}
    unmeasured = [s for s in session.samples if s.id not in measured_ids]
    if not unmeasured:
        return VerifyOutcome(dataset_name=campaign.dataset_name, already_measured=len(measured_ids))

    rng = random.Random(seed)
    n_to_pick = min(int(samples), len(unmeasured))
    picked = rng.sample(unmeasured, n_to_pick)

    logger.info(
        "verify %s (%s/%s): scoring %d new sample(s); %d already in archive for this config",
        label,
        campaign_id,
        cycle_id,
        n_to_pick,
        len(measured_ids),
    )
    await score_search_point(
        jsp,
        picked,
        session,
        label="verify",
        # `verify` replays a RECORDED config against fresh samples; the optimizer state that
        # produced it is not in scope here, and the workspace side it is compared against
        # (`compute_composite_fitness` below) has none either.
        opt_sp=None,
        measured=None,
        on_sample_scored=lambda *_a, **_k: None,
        on_sample_starting=lambda *_a, **_k: None,
        source=f"verify:{campaign_id}:{label}",
    )

    # Workspace aggregate: archive rows matching this candidate's node-configs, deduped per sample (latest wins).
    workspace_measurements = archive_views.measurements_for_config(
        stores,
        predicate=predicate,
        dataset_name=campaign.dataset_name,
    )
    by_sample: dict[int, QueryMeasurement] = {}
    for m in workspace_measurements:
        by_sample[m.sample_id] = _archive_measurement_to_qm(m)
    workspace_qms = list(by_sample.values())

    if session.scoring.scorer is not None:
        rescore_results(
            cast("list[dict[str, Any]]", workspace_qms),
            session.scoring.scorer,
            session.scoring.scorer_id,
            session.scoring.scorer_formula,
        )
    workspace_scores = compute_composite_fitness(
        workspace_qms,
        schema,
        # The source campaign's side of this comparison has no opt_sp either (see the
        # `score_search_point` call above) — a lift read across two different bases is not
        # a lift.
        opt_sp=None,
        round_scorer=session.scoring.round_scorer,
    )

    workspace_n = len(workspace_qms)
    # Same rule as the source side above: `compute_accuracy` returns None on no scoreable rows,
    # and a 0.0 here reads as "the fresh re-score collapsed", the exact verdict `verify` reports.
    if workspace_scores.get("accuracy") is None:
        raise VerifyError(
            f"the fresh re-score of {label!r} produced no scoreable sample — there is nothing "
            "to compare against the recorded measurement."
        )
    workspace_accuracy = float(workspace_scores["accuracy"])
    workspace_composite = display_fitness(
        workspace_scores.get("composite_fitness"), workspace_accuracy
    )
    samples_added = max(0, workspace_n - len(measured_ids))

    record = DiagnosticRunRecord(
        ts=utcnow_iso(),
        dataset=campaign.dataset_name,
        source_campaign=campaign_id,
        source_cycle=cycle_id,
        source_label=label,
        source_candidate_id=source_candidate_id,
        config_hash=config_hash[:12],
        samples_requested=int(samples),
        samples_added=samples_added,
        workspace_n=workspace_n,
        workspace_accuracy=workspace_accuracy,
        workspace_composite=workspace_composite,
        source_campaign_accuracy=source_campaign_accuracy,
        source_campaign_composite=source_campaign_composite,
        source_campaign_n=source_campaign_n,
    )
    sidecar_path = stores.diagnostic_runs.save(record)
    logger.info("verify: wrote diagnostic-run record → %s", sidecar_path)

    cache_replays = max(0, workspace_n - samples_added - len(measured_ids))
    return VerifyOutcome(
        dataset_name=campaign.dataset_name,
        already_measured=len(measured_ids),
        record=record,
        cache_replays=cache_replays,
    )
