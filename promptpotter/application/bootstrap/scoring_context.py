"""ScoringContext build + hot-update reconciliation + cycle bootstrap.

Three concerns live here:

* ``populate_session_scoring`` — builds the ``ScoringContext`` block on a
  freshly-wired ``Session``: scorer + per-round scorer, observability
  bridge, error-tolerance dials.
* ``bootstrap_cycle`` — resume an existing cycle or create one, refreshing
  the ``HOT_UPDATEABLE_KEYS`` config keys against the active snapshot.
* ``init_optimization_loop`` — the optimization-loop entry point used by
  :mod:`runner`: preflight, baseline OSP build, ``Cycle.start``, fork on
  divergence, observability + scoring + axes, ``INIT.exit`` emit.

``Session`` itself, ``ScoringContext`` (the dataclass), and the per-cycle
identity helpers live in :mod:`session`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from promptpotter.application.bootstrap.session import Session
from promptpotter.domain.opt_search_point import OptSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.baseline import CampaignBaseline
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.observers import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
    from promptpotter.infrastructure.tracing import ObservabilityBridge


logger = logging.getLogger(__name__)


# Hot-updateable on resume — these don't change WHAT the cycle solves, only HOW it searches.
HOT_UPDATEABLE_KEYS: frozenset[str] = frozenset(
    {
        "max_rounds",
        "l1_patience",
        "l2_patience",
        "l3_patience",
        "degradation_threshold",
        "model",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "sp_budget_ttest",
    }
)


def bootstrap_cycle(
    config: CampaignConfig,
    session: Session,
    baseline_jsp: JobSearchPoint,
    baseline_accuracy: float,
    dataset: list,
    cycle_id_override: str | None,
    *,
    parent_session_id: str = "",
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resume an existing cycle or create one. Returns (cycle_id, resumed_from_round).

    Hot-updateable config keys refresh from the current snapshot when
    cycle_id_override is set.
    """
    from promptpotter.application.runner import cycle_config_identity

    if not session.backend_id:
        return None, 0
    try:
        store = session.store.campaigns
        resolved = cycle_id_override or cycle_config_identity(baseline_jsp, dataset)
        if resume_from_round_override is not None:
            store.rewind_to_round(session.backend_id, resolved, resume_from_round_override)
        config_snapshot = config.model_dump(mode="json")
        existing = store.load(session.backend_id, resolved)
        if existing is not None:
            if cycle_id_override:
                stored_cfg = existing.get("config", {}) or {}
                changed = {
                    k: config_snapshot.get(k)
                    for k in HOT_UPDATEABLE_KEYS
                    if stored_cfg.get(k) != config_snapshot.get(k)
                }
                if changed:
                    stored_cfg.update(changed)
                    store.update(session.backend_id, resolved, {"config": stored_cfg})
                    logger.info("Updated loop-control config for %s", resolved)
            # Diag forks inherit parent's baseline_accuracy but re-measure
            # against their own JSP — refresh top-level field if drift.
            if existing.get("baseline_accuracy") != baseline_accuracy:
                store.update(session.backend_id, resolved, {"baseline_accuracy": baseline_accuracy})
            return resolved, len(existing.get("rounds", []))
        store.create(
            session.backend_id,
            resolved,
            {
                "type": "optimization_loop",
                "config": config_snapshot,
                "baseline_accuracy": baseline_accuracy,
                "parent_session_id": parent_session_id,
            },
        )
        return resolved, 0
    except (OSError, json.JSONDecodeError, KeyError):
        logger.warning("Cycle resume setup failed — running fresh", exc_info=True)
        return None, 0


def populate_session_scoring(
    session: Session,
    *,
    obs: ObservabilityBridge | None,
    scoring_formula: str | None,
    scoring_round_formula: str | None = None,
    scorer_id: str | None = None,
    experiment_id: str = "",
    cycle_id: str | None = None,
    max_consecutive_errors: int = 3,
    stale_data_load_protocol: list[str] | None = None,
    source: str = "optimization_loop",
) -> None:
    """Attach the scoring block onto ``session`` (mutates in place)."""
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
    session.max_consecutive_errors = max_consecutive_errors
    session.stale_data_load_protocol = stale_data_load_protocol
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
    baseline: CampaignBaseline,
    task_context: TaskDecomposition,
    scoring_round_formula: str | None,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cycle_id: str | None,
    resume_from_round_override: int | None,
) -> tuple[Cycle, OptSearchPoint, str | None, int]:
    """Build baseline OSP + Cycle.start + bootstrap storage; raise on missing baseline_ps."""
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.scoring.formula import compile_round_scorer

    if baseline.baseline_ps is None:
        raise ValueError("baseline.baseline_ps is required; run baseline scoring first.")
    baseline_osp = OptSearchPoint.from_prompt_fields(baseline.baseline_ps)
    baseline_round_scorer = (
        compile_round_scorer(scoring_round_formula) if scoring_round_formula else None
    )
    cycle = Cycle.start(
        baseline_osp,
        baseline.baseline_acc,
        task_context=task_context,
        schema=session.pipeline_schema,
        baseline_results=baseline.baseline_results,
        round_scorer=baseline_round_scorer,
        session=session,
        config=config,
    )

    base_pp = session.pipeline_schema.to_pipeline_params() if session.pipeline_schema else {}
    baseline_jsp = baseline_osp.to_job_search_point(
        base_pipeline_params=base_pp, schema=session.pipeline_schema
    )
    resolved_cycle_id, resumed_from_round = bootstrap_cycle(
        config,
        session,
        baseline_jsp,
        baseline.baseline_acc,
        dataset,
        cycle_id,
        parent_session_id=session.session_id,
        resume_from_round_override=resume_from_round_override,
    )
    return cycle, baseline_osp, resolved_cycle_id, resumed_from_round


def _start_observability_and_scoring(
    session: Session,
    config: CampaignConfig,
    baseline: CampaignBaseline,
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

    opt = config.optimization
    tracing_campaign_id = resolved_cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs = ObservabilityBridge.start_campaign(
        session.project_root,
        session.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        baseline_accuracy=baseline.baseline_acc,
        dataset=dataset,
        tracing_campaign_id=tracing_campaign_id,
        langfuse_session_id=langfuse_session_id or resolved_cycle_id,
        langfuse=session.langfuse,
    )
    populate_session_scoring(
        session,
        obs=obs,
        experiment_id=experiment_id,
        cycle_id=resolved_cycle_id,
        max_consecutive_errors=opt.max_consecutive_errors,
        stale_data_load_protocol=opt.stale_data_load_protocol,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )
    return tracing_campaign_id, obs


def _apply_resume_fork(
    session: Session,
    cycle: Cycle,
    baseline: CampaignBaseline,
    baseline_osp: OptSearchPoint,
    resolved_cycle_id: str | None,
    resumed_from_round: int,
    *,
    no_divergence_check: bool,
    fork_on_divergence: bool,
) -> tuple[str | None, int]:
    """Replay decisions; fork on divergence; register baseline alias. Returns possibly-rebound (id, round)."""
    from promptpotter.application.optimization.cycle import resume_with_divergence_check

    if resumed_from_round > 0 and resolved_cycle_id:
        fork_result = resume_with_divergence_check(
            session.store.campaigns,
            session.backend_id,
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
        session.store.archive.register_prompt_alias(
            session.backend_id, baseline.instruction, baseline_osp.render()
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
    from promptpotter.application.datasets.datasets import sample_dataset
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.application.optimization.elimination import build_degradation_checks
    from promptpotter.domain.phases import CampaignPhase, emit_phase

    cycle.axes = AxisIndex.ensure_for(
        session.store,
        session.backend_id,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
    )

    if resolved_cycle_id:
        session.state.cycle_id = resolved_cycle_id
        # Idempotent: runner.py may have pre-opened the ledger.
        if session.state.ledger is None:
            session.state.ledger = _open_cycle_ledger(session, resolved_cycle_id)
    session.state.tracing_campaign_id = tracing_campaign_id
    session.scoring.scoring_set = sample_dataset(dataset, config.sp_budget_ttest)
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
    baseline: CampaignBaseline,
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
    """Build Cycle + attach loop infra: baseline, resume/fork, obs, scoring, axes."""
    await _emit_preflight_and_init_session(config, dataset, cb, session)

    cycle, baseline_osp, resolved_cycle_id, resumed_from_round = _build_cycle_and_bootstrap(
        baseline,
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
        baseline,
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
        baseline,
        baseline_osp,
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
