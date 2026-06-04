"""ScorerSetup build + cycle bootstrap.

- ``populate_session_scoring`` — attach scorer + per-round scorer + obs to a Session.
- ``bootstrap_cycle`` — resume existing cycle or create one.
- ``init_optimization_loop`` — runner entry: preflight, origin OSP, Cycle.start,
  fork-on-divergence, obs + scoring + axes, INIT.exit."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from promptpotter.application.bootstrap.session import Session
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.infrastructure.store import archive_views

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.origin import CampaignOrigin
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
    from promptpotter.infrastructure.tracing import ObservabilityBridge


logger = logging.getLogger(__name__)


def bootstrap_cycle(
    session: Session,
    origin_jsp: JobSearchPoint,
    origin_accuracy: float,
    dataset: list[Sample],
    cycle_id_override: str | None,
    *,
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resolve cycle (step 3 of bootstrap) → ``(cycle_id, resumed_from_round)``.
    Opens, optionally rewinds, reports next L1 round. Drift handling in ``resume_with_divergence_check``."""
    from promptpotter.application.runner import cycle_config_identity

    if not session.backend_id:
        return None, 1
    try:
        store = session.store.campaigns
        campaign_id = session.campaign_id
        resolved = cycle_id_override or cycle_config_identity(origin_jsp, dataset)
        if resume_from_round_override is not None:
            store.rewind_to_round(campaign_id, resolved, resume_from_round_override)
        existing = store.load(campaign_id, resolved)
        if existing is not None:
            # Diag forks re-measure against their own JSP; refresh on drift from inherited value.
            if existing.get("origin_accuracy") != origin_accuracy:
                store.update(campaign_id, resolved, {"origin_accuracy": origin_accuracy})
            return resolved, len(existing.get("rounds", [])) + 1
        return resolved, 1
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, 1


def populate_session_scoring(
    session: Session,
    *,
    obs: ObservabilityBridge | None,
    scoring_formula: str | None,
    scoring_round_formula: str | None = None,
    scorer_id: str | None = None,
    experiment_id: str = "",
    cycle_id: str | None = None,
    source: str = "optimization_loop",
) -> None:
    """Attach scoring + obs to *session* in place (step 2 of bootstrap).
    Requires ``init_services`` already ran; ``scoring_formula`` resolved from ``campaign.json::scoring`` by the caller."""
    from promptpotter.application.scoring.formula import (
        auto_scorer_id,
        compile_round_scorer,
        compile_scorer,
    )

    session.experiment_id = experiment_id or (
        cycle_id.replace("cycle_", "")[:12] if cycle_id else ""
    )
    session.state.obs = obs
    session.source = source
    session.scoring.scorer = compile_scorer(scoring_formula)
    session.scoring.scorer_id = scorer_id or auto_scorer_id(scoring_formula)
    session.scoring.scorer_formula = scoring_formula
    session.scoring.round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )
    session.scoring.scorer_round_formula = scoring_round_formula


async def _emit_preflight_and_init_session(
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunCallbacks,
    session: Session,
) -> None:
    """Preflight + emit INIT.enter + backend.init_session."""
    from promptpotter.application.config import run_preflight_checks
    from promptpotter.domain.phases import CampaignPhase, emit_phase

    preflight_warnings = run_preflight_checks(config, dataset)
    for w in preflight_warnings:
        logger.warning("preflight[%s]: %s — %s", w.code, w.title, w.detail)
    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "enter",
        config=config,
        dataset=dataset,
        env=session,
        warnings=preflight_warnings,
    )

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)


def _build_cycle_and_bootstrap(
    origin: CampaignOrigin,
    task_context: TaskDecomposition,
    scoring_round_formula: str | None,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cycle_id: str | None,
    resume_from_round_override: int | None,
) -> tuple[Cycle, OptSearchPoint, str | None, int]:
    """Build origin OSP + Cycle.start + bootstrap storage; raise on missing origin_ps."""
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.scoring.formula import compile_round_scorer

    if origin.origin_ps is None:
        raise ValueError("origin.origin_ps is required; run origin scoring first.")
    # origin_ps is the resolved origin OptSearchPoint (lineage + memory intact) — use it
    # directly; no re-roundtrip through from_prompt_fields, which would drop the lineage.
    origin_osp = origin.origin_ps
    origin_round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )
    cycle = Cycle.start(
        origin_osp,
        origin.origin_acc,
        task_context=task_context,
        schema=session.pipeline_schema,
        origin_results=origin.origin_results,
        round_scorer=origin_round_scorer,
        session=session,
        config=config,
    )

    # session.pipeline_params (overlay-merged) makes origin JSP + cycle-id sensitive to overlay edits.
    base_pp = session.pipeline_params or (
        session.pipeline_schema.to_pipeline_params() if session.pipeline_schema else {}
    )
    origin_jsp = origin_osp.to_job_search_point(
        base_pipeline_params=base_pp, schema=session.pipeline_schema
    )
    resolved_cycle_id, resumed_from_round = bootstrap_cycle(
        session,
        origin_jsp,
        origin.origin_acc,
        dataset,
        cycle_id,
        resume_from_round_override=resume_from_round_override,
    )
    return cycle, origin_osp, resolved_cycle_id, resumed_from_round


def _start_observability_and_scoring(
    session: Session,
    config: CampaignConfig,
    origin: CampaignOrigin,
    dataset: list[Sample],
    *,
    resolved_cycle_id: str | None,
    started_at: str,
    langfuse_session_id: str | None,
    experiment_id: str,
    scoring_formula: str | None,
    scoring_round_formula: str | None,
    scorer_id: str,
) -> tuple[str, ObservabilityBridge | None]:
    """Start ObservabilityBridge + populate scoring; obs may be None on failure."""
    from promptpotter.infrastructure.tracing import ObservabilityBridge

    tracing_campaign_id = resolved_cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs = ObservabilityBridge.start_campaign(
        session.project_root,
        session.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        origin_accuracy=origin.origin_acc,
        dataset=dataset,
        tracing_campaign_id=tracing_campaign_id,
        campaign_id=session.campaign_id,
        langfuse_session_id=langfuse_session_id or resolved_cycle_id,
        langfuse=session.langfuse,
    )
    populate_session_scoring(
        session,
        obs=obs,
        experiment_id=experiment_id,
        cycle_id=resolved_cycle_id,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )
    return tracing_campaign_id, obs


def _apply_resume_fork(
    session: Session,
    cycle: Cycle,
    origin: CampaignOrigin,
    origin_osp: OptSearchPoint,
    resolved_cycle_id: str | None,
    resumed_from_round: int,
    *,
    no_divergence_check: bool,
    fork_on_divergence: bool,
) -> tuple[str | None, int]:
    """Replay decisions; fork on divergence; register origin alias. Returns possibly-rebound (id, round)."""
    from promptpotter.application.optimization.resume_and_fork import (
        resume_with_divergence_check,
    )

    # =1 is fresh (origin only); real resumes are >=2 (>=1 L1 round on disk).
    if resumed_from_round > 1 and resolved_cycle_id:
        fork_result = resume_with_divergence_check(
            session.store.campaigns,
            session.campaign_id,
            resolved_cycle_id,
            resumed_from_round,
            session,
            cycle,
            skip_divergence_check=no_divergence_check,
            fork_on_divergence=fork_on_divergence,
        )
        if fork_result is not None:
            resolved_cycle_id = fork_result.new_cycle_id
            resumed_from_round = fork_result.new_resumed_from_round
    if session.store:
        archive_views.register_prompt_alias(
            session.store, session.backend_id, origin.instruction, origin_osp.render()
        )
    return resolved_cycle_id, resumed_from_round


def _finalize_loop_state(
    cycle: Cycle,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunCallbacks,
    *,
    resolved_cycle_id: str | None,
    tracing_campaign_id: str,
    resumed_from_round: int,
) -> None:
    """Init AxisIndex, write final session/cycle state, emit ``INIT.exit``."""
    from promptpotter.application.bootstrap.session import _open_cycle_ledger
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.application.optimization.pobb.elimination import build_degradation_checks
    from promptpotter.domain.phases import CampaignPhase, emit_phase

    cycle.axes = AxisIndex.ensure_for(
        session.store,
        session.backend_id,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
        dataset_name=session.dataset_name,
    )

    if resolved_cycle_id:
        session.state.cycle_id = resolved_cycle_id
        # Idempotent — runner.py may have pre-opened the ledger.
        if session.state.ledger is None:
            session.state.ledger = _open_cycle_ledger(session, resolved_cycle_id)
    session.state.tracing_campaign_id = tracing_campaign_id
    # Full train split = bank; per-round adaptive queue mechanism narrows to ``sp_budget_ttest``.
    session.scoring.scoring_set = list(dataset)
    session.scoring.degradation_checks = build_degradation_checks(config)
    session.state.resumed_from_round = resumed_from_round

    emit_phase(
        cb.on_phase,
        CampaignPhase.INIT,
        "exit",
        state=cycle,
        env=session,
        config=config,
        dataset=dataset,
    )


async def init_optimization_loop(
    origin: CampaignOrigin,
    dataset: list[Sample],
    config: CampaignConfig,
    *,
    cb: RunCallbacks,
    task_context: TaskDecomposition,
    scoring_formula: str | None,
    scoring_round_formula: str | None,
    scorer_id: str,
    no_divergence_check: bool,
    fork_on_divergence: bool,
    langfuse_session_id: str | None,
    cycle_id: str | None,
    resume_from_round_override: int | None,
    experiment_id: str,
    session: Session,
    started_at: str,
) -> Cycle:
    """Build Cycle + attach loop infra: origin, resume/fork, obs, scoring, axes."""
    await _emit_preflight_and_init_session(config, dataset, cb, session)

    cycle, origin_osp, resolved_cycle_id, resumed_from_round = _build_cycle_and_bootstrap(
        origin,
        task_context,
        scoring_round_formula,
        session,
        config,
        dataset,
        cycle_id,
        resume_from_round_override,
    )

    tracing_campaign_id, _obs = _start_observability_and_scoring(
        session,
        config,
        origin,
        dataset,
        resolved_cycle_id=resolved_cycle_id,
        started_at=started_at,
        langfuse_session_id=langfuse_session_id,
        experiment_id=experiment_id,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )

    resolved_cycle_id, resumed_from_round = _apply_resume_fork(
        session,
        cycle,
        origin,
        origin_osp,
        resolved_cycle_id,
        resumed_from_round,
        no_divergence_check=no_divergence_check,
        fork_on_divergence=fork_on_divergence,
    )

    _finalize_loop_state(
        cycle,
        session,
        config,
        dataset,
        cb,
        resolved_cycle_id=resolved_cycle_id,
        tracing_campaign_id=tracing_campaign_id,
        resumed_from_round=resumed_from_round,
    )
    return cycle


__all__ = [
    "bootstrap_cycle",
    "init_optimization_loop",
    "populate_session_scoring",
]
