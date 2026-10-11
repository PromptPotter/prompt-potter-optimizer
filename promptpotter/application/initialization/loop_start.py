from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.resume import (
    resume_with_divergence_check,
)
from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.initialization.session import Session
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolved_dataset_name,
)
from promptpotter.application.preflight import (
    refuse_arm_below_round,
    refuse_below_reasoning_floor,
    run_preflight_checks,
)
from promptpotter.application.runner.campaign_ids import cycle_config_identity
from promptpotter.application.runner.inner.spawn_context import retarget_inner_spawn
from promptpotter.application.scoring.classification import build_degradation_checks
from promptpotter.application.scoring.formula import cell_channels_of
from promptpotter.application.scoring.sample_measurement import cell_bound
from promptpotter.application.views.ingress import init_enter, init_exit
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.l4.inner_origin import inner_origin_of
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase, StopLoop
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import (
    bind_spend_book,
    bound_spend_book,
    unbounded_spend_book,
)
from promptpotter.infrastructure.llm.telemetry import (
    active_cycle_ledger,
    filed_as,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.judges.registry import build_evaluators
from promptpotter.shared.hashing import dataset_hash

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.origin import CampaignOrigin
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def init_cycle(
    session: Session,
    origin_jsp: JobSearchPoint,
    dataset: list[Sample],
    cycle_id_override: str | None,
    *,
    resume_from_round_override: int | None = None,
) -> tuple[str | None, int]:
    """A present-but-BROKEN cycle propagates: swallowing it re-spends the campaign from scratch."""

    if not session.backend_id:
        return None, 1
    store = session.store.campaigns
    campaign_id = session.campaign_id
    resolved = cycle_id_override or cycle_config_identity(origin_jsp, dataset)
    hop = CycleHop(campaign_id=campaign_id, cycle_id=resolved)
    # Re-written on a resume on purpose: the backend may have changed its axes since.
    if session.pipeline_declaration:
        store.write_resolved_pipeline(hop, session.pipeline_declaration)
    # Write-once, unlike the line above: a roster a connector only NAMES can move under its name.
    store.write_resolved_experiment(hop, session.backend_client.workload.experiment)
    store.write_bank_partition(
        hop, session.scoring.require_partition().record(dataset_hash=dataset_hash(session.samples))
    )
    store.write_optimized_surface(
        hop,
        session.pipeline_schema.value_tree(
            prompt_delivery=session.backend_client.prompt_delivery(session.pipeline_params)
        ),
    )
    if resume_from_round_override is not None:
        store.rewind_to_round(hop, resume_from_round_override)
    existing = store.load(hop)
    if existing is not None:
        session.human_intervened = existing.human_intervened
        # The round NUMBER after the highest one closed, never a count: the origin is round 0.
        return resolved, max(store.standing_rounds(hop).rounds, default=0) + 1
    return resolved, 1


def populate_session_scoring(
    session: Session, campaign_config: CampaignConfig, *, source: RunSource
) -> None:
    session.source = source
    session.scoring.scorer = scorer_of(
        campaign_config,
        verifier_graded=all_verifier_graded(s.ground_truth for s in session.samples),
    )
    session.scoring.display_metric = campaign_config.display_metric
    # Assigned unconditionally: `{}` declares no judges, and never means "keep what is armed".
    session.scoring.judges = build_evaluators(
        campaign_config.judges, cache=session.store.judge_reuse
    )


def arm_diagnostic_scoring(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    source: RunSource,
) -> None:
    if bound_spend_book() is None:
        bind_spend_book(unbounded_spend_book())
    configure_and_apply_pipeline(session, campaign_config)
    populate_session_scoring(session, campaign_config, source=source)
    session.scoring.partition = partition_bank(session.samples, campaign_config.dataset_split)


async def diagnostic_pass(
    error: Callable[[str], Exception], scoring: Awaitable[ScoredWalk]
) -> ScoredWalk:
    """Nothing above a diagnostic verb catches the round loop's ``StopLoop``."""
    try:
        scored = await scoring
    except StopLoop as stop:
        info = STOP_REASON_INFO[stop.reason]
        unmeasured = "" if stop.unmeasured is None else f", {stop.unmeasured} cell(s) unmeasured"
        raise error(f"{info.label}{unmeasured}. {info.next_step}".strip()) from None
    if scored.stopped is not None:
        raise error(
            f"Scoring stopped after {len(scored.sheet)} cell(s): {scored.stopped}. A reading "
            "over part of the pass would describe the stop, not the configuration."
        )
    return scored


@contextmanager
def verb_ledger(stores: Stores, hop: CycleHop | None) -> Iterator[CycleEventLog]:
    """Opens one only when none is bound: a second handle on one file is a second appender."""
    if (bound := active_cycle_ledger()) is not None:
        yield bound
        return
    ledger = (
        CycleEventLog.open_workspace(stores.campaigns.workspace)
        if hop is None
        else CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop)))
    )
    # So what an L4 cell spends beneath the verb lands here, not only on the sandbox's ledger.
    if (book := bound_spend_book()) is not None and book.ledger is None:
        book.ledger = ledger
    token = set_cycle_ledger(ledger)
    try:
        yield ledger
    finally:
        reset_cycle_ledger(token)


@contextmanager
def diagnostic_trace(stores: Stores, hop: CycleHop | None) -> Iterator[None]:
    with verb_ledger(stores, hop), filed_as("diagnostic"):
        yield


_PRICED_RUNS = 32


def measured_cell_usd(
    session: Session, node_configs: list[tuple[str, dict[str, Any]]]
) -> float | None:
    on_models = {name: {"model": cfg["model"]} for name, cfg in node_configs if cfg.get("model")}
    total, cells = 0.0, 0
    for m in session.store.archive.measurements_for_config(
        on_models, dataset_name=session.dataset_name, newest=_PRICED_RUNS
    ):
        # An archive row carries no grade, and the price is no formula's reading.
        cost = cell_channels_of(m.cell, None).get("cost")
        if cost is not None:
            total += cost
            cells += 1
    return total / cells if cells else None


def _campaigns_on_origin(session: Session) -> list[str]:
    """A cycle id is content-addressed: every other campaign holding it ran this origin."""
    cycle_id = session.state.cycle_id
    if not cycle_id:
        return []
    return sorted(
        {
            entry.campaign_id
            for entry in session.store.campaigns.enumerate_cycles()
            if entry.cycle_id == cycle_id and entry.campaign_id != session.campaign_id
        }
    )


def _banked_instruments(session: Session, config: CampaignConfig) -> list[str]:
    campaigns = session.store.campaigns
    dataset_name = resolved_dataset_name(session, config)
    banked: list[str] = []
    for campaign_dir in campaigns.iter_campaign_dirs():
        campaign = campaigns.load_campaign(campaign_dir.name)
        if campaign is None or campaign.dataset_name != dataset_name:
            continue
        origin = campaigns.standing_rounds(campaign.root_hop).rounds.get(0)
        instrument = None if origin is None else inner_origin_of(origin.close.pipeline_params)
        if instrument is not None:
            banked.append(instrument)
    return banked


async def _emit_preflight_and_init_session(
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunCallbacks,
    session: Session,
    origin: CampaignOrigin,
) -> None:

    target_node_configs = list(node_config_items(session.pipeline_params))
    target_models = tuple(str(v["model"]) for _, v in target_node_configs if v.get("model"))

    refuse_below_reasoning_floor(config, session.pipeline_params)

    bound = await cell_bound(session, session.pipeline_params or {})
    measured_usd = measured_cell_usd(session, target_node_configs)
    instrument = inner_origin_of(session.pipeline_params)
    preflight_warnings = run_preflight_checks(
        config,
        dataset,
        target_models,
        framing=origin.framing,
        cell_usd=None if bound is None else bound.usd,
        measured_cell_usd=measured_usd,
        origin_campaigns=_campaigns_on_origin(session),
        instrument=instrument,
        banked_instruments=[] if instrument is None else _banked_instruments(session, config),
    )
    for w in preflight_warnings:
        logger.warning("preflight[%s]: %s — %s", w.code, w.title, w.detail)
    refuse_arm_below_round(session.arm, preflight_warnings, measured_cell_usd=measured_usd)
    cb.on_phase(
        CampaignPhase.INIT,
        "enter",
        view=init_enter(
            cb.view_context,
            config=config,
            dataset=dataset,
            session=session,
            warnings=preflight_warnings,
        ),
    )

    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)


def _build_and_start_cycle(
    origin: CampaignOrigin,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cycle_id: str | None,
    resume_from_round_override: int | None,
) -> tuple[Cycle, str | None, int]:

    if origin.resolved_origin is None:
        raise ValueError("origin.resolved_origin is required; run origin scoring first.")
    cycle = Cycle.start(
        origin.resolved_origin,
        origin.report,
        schema=session.pipeline_schema,
        framing=origin.framing,
        origin_results=origin.origin_results,
        session=session,
        config=config,
    )
    assert cycle.tracking.current_sp is not None
    resolved_cycle_id, resumed_from_round = init_cycle(
        session,
        cycle.tracking.current_sp,
        dataset,
        cycle_id,
        resume_from_round_override=resume_from_round_override,
    )
    return cycle, resolved_cycle_id, resumed_from_round


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
    return resolved_cycle_id, resumed_from_round


def _finalize_loop_state(
    cycle: Cycle,
    session: Session,
    config: CampaignConfig,
    dataset: list[Sample],
    cb: RunCallbacks,
    *,
    resolved_cycle_id: str | None,
    resumed_from_round: int,
) -> None:

    cycle.sample_index = SampleIndex.ensure_for(
        session.store,
        scorer=session.scoring.require_scorer(),
        dataset_name=session.dataset_name,
        sample_ids=session.scoring.require_partition().admitted_ids,
    )

    if resolved_cycle_id:
        session.state.cycle_id = resolved_cycle_id
        # Idempotent — runner/entry.py may have pre-opened the ledger.
        if session.state.ledger is None:
            session.state.ledger = CycleEventLog.open(
                CycleDir(session.store.campaigns.cycle_dir(session.hop))
            )
        # The first moment the lock from `Cycle.start` has a cycle id to be written under.
        cycle.difficulty.persist(round_num=len(cycle.rounds) - 1)
    session.scoring.degradation_checks = build_degradation_checks(config)
    session.state.resumed_from_round = resumed_from_round

    # A RUNNING cycle's `log.md` reads its formula off this bracket (`scorer_cell_formula`).
    cb.on_phase(
        CampaignPhase.INIT,
        "exit",
        view=init_exit(cb.view_context, cycle=cycle, session=session),
    )


async def init_optimization_loop(
    origin: CampaignOrigin,
    dataset: list[Sample],
    config: CampaignConfig,
    *,
    cb: RunCallbacks,
    no_divergence_check: bool,
    fork_on_divergence: bool,
    cycle_id: str | None,
    resume_from_round_override: int | None,
    session: Session,
) -> Cycle:
    await _emit_preflight_and_init_session(config, dataset, cb, session, origin)

    cycle, resolved_cycle_id, resumed_from_round = _build_and_start_cycle(
        origin,
        session,
        config,
        dataset,
        cycle_id,
        resume_from_round_override,
    )

    session.source = RunSource.OPTIMIZATION_LOOP

    resolved_cycle_id, resumed_from_round = await _apply_resume_fork(
        session,
        cycle,
        resolved_cycle_id,
        resumed_from_round,
        dataset,
        no_divergence_check=no_divergence_check,
        fork_on_divergence=fork_on_divergence,
    )
    # The cycle id is FINAL only here; the spawn context was published before a fork could move it.
    retarget_inner_spawn(session)

    _finalize_loop_state(
        cycle,
        session,
        config,
        dataset,
        cb,
        resolved_cycle_id=resolved_cycle_id,
        resumed_from_round=resumed_from_round,
    )
    return cycle


__all__ = [
    "diagnostic_pass",
    "init_cycle",
    "init_optimization_loop",
    "populate_session_scoring",
]
