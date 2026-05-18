"""ScoringContext build + cycle bootstrap.

Three concerns live here:

* ``populate_session_scoring`` — builds the ``ScoringContext`` block on a
  freshly-wired ``Session``: scorer + per-round scorer, observability
  bridge, error-tolerance dials.
* ``bootstrap_cycle`` — resume an existing cycle or create one. Snapshot
  maintenance (refresh on policy-only diff, leave alone on data-affecting
  fork) lives in ``resume_with_divergence_check`` —
  :meth:`CampaignConfig.classify_diff_against` is the single source of
  truth for which fields are policy vs data.
* ``init_optimization_loop`` — the optimization-loop entry point used by
  :mod:`runner`: preflight, origin OSP build, ``Cycle.start``, fork on
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
    config: CampaignConfig,
    session: Session,
    origin_jsp: JobSearchPoint,
    origin_accuracy: float,
    dataset: list,
    cycle_id_override: str | None,
    *,
    parent_session_id: str = "",
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resume an existing cycle or create one — step 3 of the bootstrap
    chain. Returns (cycle_id, resumed_from_round).

    **Preconditions:**
    - :func:`init_services` already ran (session has ``store``,
      ``backend_id``, ``pipeline_schema``, ``samples``).
    - :func:`populate_session_scoring` already ran (session has ``scoring``
      block populated — needed because the cycle may immediately re-score).

    **Postconditions:**
    - On hit: cycle exists in ``store.campaigns``; ``resumed_from_round`` =
      ``len(rounds) + 1``. The stored ``config`` snapshot stays untouched
      here — ``resume_with_divergence_check`` owns refresh decisions based
      on :meth:`CampaignConfig.classify_diff_against`.
    - On miss: cycle freshly created at round 0; ``resumed_from_round = 1``.

    ``resumed_from_round`` is the next L1 round_num to execute. Origin is
    round 0 (always already scored by the time this returns); the first L1
    round is round 1.
    """
    from promptpotter.application.runner import cycle_config_identity

    if not session.backend_id:
        return None, 1
    try:
        store = session.store.campaigns
        resolved = cycle_id_override or cycle_config_identity(origin_jsp, dataset)
        if resume_from_round_override is not None:
            store.rewind_to_round(session.backend_id, resolved, resume_from_round_override)
        existing = store.load(session.backend_id, resolved)
        if existing is not None:
            # Diag forks inherit parent's origin_accuracy but re-measure
            # against their own JSP — refresh top-level field if drift.
            if existing.get("origin_accuracy") != origin_accuracy:
                store.update(session.backend_id, resolved, {"origin_accuracy": origin_accuracy})
            return resolved, len(existing.get("rounds", [])) + 1
        store.create(
            session.backend_id,
            resolved,
            {
                "type": "optimization_loop",
                "config": config.model_dump(mode="json"),
                "origin_accuracy": origin_accuracy,
                "parent_session_id": parent_session_id,
            },
        )
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
    """Attach the scoring block onto ``session`` (mutates in place) — step 2
    of the bootstrap chain.

    **Preconditions:**
    - :func:`init_services` already ran (session has ``store``,
      ``backend_id``, ``pipeline_schema``).
    - ``scoring_formula`` already resolved from
      ``campaign.json::scoring`` by the caller.

    **Postconditions:**
    - ``session.scoring`` populated: ``scorer`` (compiled callable),
      ``scorer_id`` (auto-derived if not given), ``scorer_formula``,
      ``round_scorer`` (when ``scoring_round_formula`` is non-empty).
    - ``session.state.obs`` set (may be ``None`` if obs disabled).
    - ``session.experiment_id`` derived from cycle_id when not explicit.
    """
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
    origin_osp = OptSearchPoint.from_prompt_fields(origin.origin_ps)
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

    # session.pipeline_params carries the dataset overlay (provider/model/etc.)
    # merged by configure_and_apply_pipeline. Use it so the origin JSP — and the
    # cycle-id derived from its content_hash — is sensitive to overlay edits.
    base_pp = session.pipeline_params or (
        session.pipeline_schema.to_pipeline_params() if session.pipeline_schema else {}
    )
    origin_jsp = origin_osp.to_job_search_point(
        base_pipeline_params=base_pp, schema=session.pipeline_schema
    )
    resolved_cycle_id, resumed_from_round = bootstrap_cycle(
        config,
        session,
        origin_jsp,
        origin.origin_acc,
        dataset,
        cycle_id,
        parent_session_id=session.session_id,
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

    # resumed_from_round=1 is fresh (only origin done, no L1 round to replay).
    # Real resumes are >=2 (at least one L1 round on disk to walk).
    if resumed_from_round > 1 and resolved_cycle_id:
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
    from promptpotter.application.datasets import sample_dataset
    from promptpotter.application.intelligence.exploration import seed_initial_scoring_set
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.application.optimization.pobb.elimination import build_degradation_checks
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
    exp_cfg = config.optimization.exploration
    seeded: list[Sample] | None = None
    if (
        exp_cfg.seed_initial_scoring_set_from_archive
        and len(cycle.archive_observations) >= config.optimization.elimination_n_min
    ):
        seeded = seed_initial_scoring_set(
            cycle.archive_observations, dataset, config.sp_budget_ttest
        )
    session.scoring.scoring_set = seeded or sample_dataset(dataset, config.sp_budget_ttest)
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
