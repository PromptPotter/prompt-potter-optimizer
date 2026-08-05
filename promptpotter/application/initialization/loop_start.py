"""Run init steps 2-4 — a wired ``Session`` to a running round loop.

- ``populate_session_scoring`` — attach scorer + per-round scorer + obs to a Session.
- ``init_cycle`` — resume existing cycle or create one.
- ``init_optimization_loop`` — runner entry: preflight, origin OSP, Cycle.start,
  fork-on-divergence, obs + scoring + axes, INIT.exit."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.session import Session
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import node_config_items

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.origin import CampaignOrigin
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge


logger = logging.getLogger(__name__)


def next_resume_round(round_summaries: list[dict[str, Any]]) -> int:
    """Next L1 round to run on resume = highest persisted round NUMBER + 1.

    Keyed on the round number, never ``len()``: the origin is round 0 in
    ``index.json::rounds`` now, so counting entries over-counts by one and the loop
    skips a round (round_0000, round_0002, never round_0001). A clean fresh start
    (origin only) → max 0 → next round 1.

    Entries are ``index.json::rounds`` (``RoundSummary``, ``round: int`` required),
    so the key is read directly — no ``.get`` default papering over an absent key."""
    return max((int(r["round"]) for r in round_summaries), default=0) + 1


def init_cycle(
    session: Session,
    origin_jsp: JobSearchPoint,
    dataset: list[Sample],
    cycle_id_override: str | None,
    *,
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """Resolve cycle (step 3 of run init) → ``(cycle_id, resumed_from_round)``.
    Drift handling lives in ``resume_with_divergence_check``.

    A genuinely-absent cycle is the ``existing is None`` branch (``store.load``
    returns ``None`` for a missing index) → a fresh start at round 1. A
    *present-but-broken* state — a corrupt index (``JSONDecodeError``), a disk
    fault (``OSError``), a malformed round summary (``KeyError`` in
    ``next_resume_round``), or an invalid ``--from N`` rewind — is NOT caught: it
    propagates and halts loud. Swallowing it would silently discard the prior
    rounds and re-spend the campaign from scratch under a fresh anonymous cycle."""
    from promptpotter.application.runner.campaign_ids import cycle_config_identity

    if not session.backend_id:
        return None, 1
    store = session.store.campaigns
    campaign_id = session.campaign_id
    resolved = cycle_id_override or cycle_config_identity(origin_jsp, dataset)
    hop = CycleHop(campaign_id=campaign_id, cycle_id=resolved)
    if resume_from_round_override is not None:
        store.rewind_to_round(hop, resume_from_round_override)
    existing = store.load(hop)
    if existing is not None:
        # No origin_accuracy stamp here — the index derives it from rounds[0]
        # (`origin_accuracy_of`); any re-measure re-emits round 0 through
        # emit_origin_round → save_round_file, so the row is always fresh.
        # A babysat cycle carries `human_intervened` on its index — surface it so a
        # resume grades its runs non-clean without re-reading the seed.
        session.human_intervened = bool(existing.get("human_intervened", False))
        if existing.get("finished_at"):
            # About to run rounds on a cycle still carrying its terminal latch (a reaped
            # inner cell, an operator resume past a stop). `derive_run_phase` returns
            # TERMINAL on `finished_at` BEFORE it consults anything else, so leaving it
            # set makes a live run report finished to the cycle list, the picker and the
            # reaper — and masks a `paused` declaration, which ranks below it. Both levels
            # reach here (an inner cell runs this same `run_optimization`), so one clear
            # serves both. Ordering is safe by construction: `build_run_observers` has
            # already refreshed `dashboard.json`, so the cycle steps TERMINAL → RUNNING;
            # clearing while the producer was stale would derive DETACHED — what the
            # reaper's `_is_dead` collects.
            store.reopen_for_continuation(hop)
        return resolved, next_resume_round(existing["rounds"])
    return resolved, 1


def populate_session_scoring(
    session: Session,
    *,
    obs: ObservabilityBridge | None,
    scoring_formula: str | None,
    scoring_round_formula: str | None = None,
    scorer_id: str | None = None,
    source: str = "optimization_loop",
) -> None:
    """Attach scoring + obs to *session* in place (step 2 of run init).
    Requires ``init_services`` already ran; ``scoring_formula`` resolved from ``campaign.json::scoring`` by the caller."""
    from promptpotter.application.scoring.formula import (
        auto_scorer_id,
        compile_round_scorer,
        compile_scorer,
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
    from promptpotter.application.config import (
        check_model_reasoning_floors,
        run_preflight_checks,
    )
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        get_optimizer_schema,
    )
    from promptpotter.domain.phases import CampaignPhase, emit_phase

    target_node_configs = list(node_config_items(session.pipeline_params))
    target_models = tuple(str(v["model"]) for _, v in target_node_configs if v.get("model"))

    # HARD block before any spend: a reasoning model pinned below its token floor (e.g. the
    # inner optimizer's l1_critique) burns its whole budget reasoning and emits zero content,
    # stalling the loop silently. Both surfaces carry model+max_tokens: the dataset/target
    # nodes (session.pipeline_params) and the optimizer nodes (promptpotter/assets/optimizer/pipeline.yaml).
    optimizer_node_configs = [(n.name, n.current_config) for n in get_optimizer_schema().nodes]
    if floor_violations := check_model_reasoning_floors(
        target_node_configs + optimizer_node_configs
    ):
        raise ValueError(
            "Model-profile preflight block — a reasoning model is configured below its token "
            "floor and would emit zero content:\n  - " + "\n  - ".join(floor_violations)
        )

    preflight_warnings = run_preflight_checks(config, dataset, target_models)
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


def _build_and_start_cycle(
    origin: CampaignOrigin,
    task_context: TaskDecomposition,
    scoring_round_formula: str | None,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cycle_id: str | None,
    resume_from_round_override: int | None,
) -> tuple[Cycle, str | None, int]:
    """Build origin OSP + Cycle.start + open storage; raise on missing resolved_origin."""
    from promptpotter.application.optimization.cycle import Cycle

    if origin.resolved_origin is None:
        raise ValueError("origin.resolved_origin is required; run origin scoring first.")
    # resolved_origin is the resolved origin OptSearchPoint (lineage + memory intact) — use it
    # directly; no re-roundtrip through from_prompt_fields, which would drop the lineage.
    resolved_origin = origin.resolved_origin
    cycle = Cycle.start(
        resolved_origin,
        origin.report,
        task_context=task_context,
        schema=session.pipeline_schema,
        origin_results=origin.origin_results,
        session=session,
        config=config,
    )

    # session.pipeline_params (overlay-merged) makes origin JSP + cycle-id sensitive to overlay edits.
    base_pp = session.pipeline_params or session.pipeline_schema.to_pipeline_params()
    origin_jsp = resolved_origin.to_job_search_point(
        base_pipeline_params=base_pp, schema=session.pipeline_schema
    )
    resolved_cycle_id, resumed_from_round = init_cycle(
        session,
        origin_jsp,
        dataset,
        cycle_id,
        resume_from_round_override=resume_from_round_override,
    )
    return cycle, resolved_cycle_id, resumed_from_round


def _start_observability_and_scoring(
    session: Session,
    config: CampaignConfig,
    origin: CampaignOrigin,
    dataset: list[Sample],
    *,
    resolved_cycle_id: str | None,
    started_at: str,
    langfuse_session_id: str | None,
    scoring_formula: str | None,
    scoring_round_formula: str | None,
    scorer_id: str,
) -> tuple[str, ObservabilityBridge | None]:
    """Start ObservabilityBridge + populate scoring; obs may be None on failure."""
    from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge

    tracing_campaign_id = resolved_cycle_id or f"campaign_{started_at[:19].replace(':', '')}"
    obs = ObservabilityBridge.start_campaign(
        session.tenant_root,
        session.backend_id,
        config_snapshot=config.model_dump(mode="json"),
        origin_accuracy=origin.report.accuracy,
        dataset=dataset,
        tracing_campaign_id=tracing_campaign_id,
        campaign_id=session.campaign_id,
        langfuse_session_id=langfuse_session_id or resolved_cycle_id,
        langfuse=session.langfuse,
    )
    populate_session_scoring(
        session,
        obs=obs,
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )
    return tracing_campaign_id, obs


async def _apply_resume_fork(
    session: Session,
    cycle: Cycle,
    resolved_cycle_id: str | None,
    resumed_from_round: int,
    dataset: list[Sample],
    *,
    no_divergence_check: bool,
    fork_on_divergence: bool,
) -> tuple[str | None, int]:
    """Repair holed rounds, replay decisions, fork on divergence. Returns (id, round)."""
    from promptpotter.application.optimization.escalation.state import EscalationFSM
    from promptpotter.application.optimization.resume_and_fork.resume import (
        resume_with_divergence_check,
    )

    # =1 is fresh (origin only); real resumes are >=2 (>=1 L1 round on disk).
    if resumed_from_round > 1 and resolved_cycle_id:
        fork_result = await resume_with_divergence_check(
            session.store.campaigns,
            CycleHop(campaign_id=session.campaign_id, cycle_id=resolved_cycle_id),
            resumed_from_round,
            session,
            cycle,
            dataset,
            skip_divergence_check=no_divergence_check,
            fork_on_divergence=fork_on_divergence,
        )
        if fork_result is not None:
            resolved_cycle_id = fork_result.new_cycle_id
            resumed_from_round = fork_result.new_resumed_from_round
        # The FSM is rebuilt from the ledger whichever way that went — halt, fork, or carry
        # on — because every one of them replays priors and `replay_priors` deliberately does
        # not touch escalation. It was written at each of the four exits inside; a
        # postcondition of the call belongs at the call.
        cycle.escalation = EscalationFSM.from_ledger(
            session.state.ledger, lives=cycle.config.optimization.lives
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
    from promptpotter.application.initialization.session import open_cycle_ledger
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.application.optimization.pobb.checks import (
        build_degradation_checks,
    )
    from promptpotter.domain.phases import CampaignPhase, emit_phase

    cycle.axes = AxisIndex.ensure_for(
        session.store,
        scorer=session.scoring.scorer,
        scorer_id=session.scoring.scorer_id,
        scorer_formula=session.scoring.scorer_formula,
        dataset_name=session.dataset_name,
    )

    if resolved_cycle_id:
        session.state.cycle_id = resolved_cycle_id
        # Idempotent — runner.py may have pre-opened the ledger.
        if session.state.ledger is None:
            session.state.ledger = open_cycle_ledger(session, resolved_cycle_id)
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
    session: Session,
    started_at: str,
) -> Cycle:
    await _emit_preflight_and_init_session(config, dataset, cb, session)

    cycle, resolved_cycle_id, resumed_from_round = _build_and_start_cycle(
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
        scoring_formula=scoring_formula,
        scoring_round_formula=scoring_round_formula,
        scorer_id=scorer_id,
    )

    resolved_cycle_id, resumed_from_round = await _apply_resume_fork(
        session,
        cycle,
        resolved_cycle_id,
        resumed_from_round,
        dataset,
        no_divergence_check=no_divergence_check,
        fork_on_divergence=fork_on_divergence,
    )
    # The cycle id is FINAL here — a resume fork retargets it above, and the spawn context was
    # published before any of that resolved (a child may recurse before this point). Local
    # import: `runner.inner.cycle` reaches back into this package for `Session`.
    from promptpotter.application.runner.inner.cycle import retarget_inner_spawn

    retarget_inner_spawn(session)

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
    "init_cycle",
    "init_optimization_loop",
    "populate_session_scoring",
]
