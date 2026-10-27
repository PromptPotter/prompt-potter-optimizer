from __future__ import annotations

import logging
from collections.abc import Awaitable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, assert_never

from promptpotter.application.bench.cycle import Cycle
from promptpotter.application.bench.resume_and_fork.resume import (
    resume_with_divergence_check,
)
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.initialization.session import ScorerSetup, Session
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline
from promptpotter.application.preflight import (
    refuse_arm_below_round,
    run_preflight_checks,
)
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.inner.spawn_context import (
    publish_inner_spawn_context,
    retarget_inner_spawn,
)
from promptpotter.application.scoring.classification import build_degradation_checks
from promptpotter.application.scoring.formula.compiler import cell_channels_of
from promptpotter.application.scoring.sample_measurement import cell_bound
from promptpotter.application.views.ingress import init_enter, init_exit
from promptpotter.domain.bench import BankPartition, partition_bank
from promptpotter.domain.campaign import Compare, Optimize
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.l4.inner_origin import inner_origin_of
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.phases import STOP_REASON_INFO, CampaignPhase, StopLoop
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import PointContext
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
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    CellOf,
    OpenAt,
    bind_memory_scope,
    memory_scope,
)
from promptpotter.judges.registry import build_evaluators
from promptpotter.shared.errors import DiagnosticError

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.origin import CampaignOrigin
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)


def init_cycle(session: Session, *, resume_from_round_override: int | None) -> None:
    """A present-but-BROKEN cycle propagates: swallowing it re-spends the campaign from scratch."""
    store = session.store.campaigns
    hop = session.hop
    # Before anything that can raise: a fork's state is its parent's until this line.
    session.state.resumed_from_round = (
        _next_round(session)
        if resume_from_round_override is None
        else store.rewind_enters(hop, resume_from_round_override)
    )
    # Restated on every launch: the backend may have moved its axes since.
    store.write_resolved_run(
        hop,
        declaration=session.pipeline_declaration,
        experiment=session.backend_client.workload.experiment,
    )
    store.write_optimized_surface(
        hop,
        session.pipeline_schema.value_tree(
            prompt_delivery=session.backend_client.prompt_delivery(session.pipeline_params)
        ),
    )
    existing = store.load(hop, moment=None)
    if existing is not None:
        session.state.human_intervened = existing.human_intervened


def _next_round(session: Session) -> int:
    """The round NUMBER after the highest one closed, never a count: the origin is round 0."""
    last = session.store.campaigns.standing_rounds(session.hop, moment=None).last_closed
    return 1 if last is None else last + 1


def populate_session_scoring(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    source: RunSource,
    partition: BankPartition,
) -> None:
    """The one writer of ``state.scoring``, built whole: `{}` judges declares none, never "keep"."""
    session.state.source = source
    session.state.scoring = ScorerSetup(
        scorer=scorer_of(
            campaign_config,
            verifier_graded=all_verifier_graded(s.ground_truth for s in session.bank.samples),
        ),
        display_metric=campaign_config.display_metric,
        partition=partition,
        degradation_checks=tuple(build_degradation_checks(campaign_config)),
        judges=tuple(build_evaluators(campaign_config.judges, cache=session.store.judge_reuse)),
    )


def scope_memory(session: Session) -> None:
    """An L4 cell keeps the scope its spawner bound (`runner/inner/spawn_context.py::enter_inner_cell`)."""
    if isinstance(memory_scope(), CellOf):
        return
    match purpose := session.purpose:
        case Optimize():
            bind_memory_scope(OpenAt())
        case Compare(arm=arm):
            record = session.store.campaigns.load_head_to_head(arm.head_to_head_id)
            assert record is not None, "an arm's mint declares its head-to-head"
            walks = CampaignWalks(session.store.campaigns, session.campaign_id)
            bind_memory_scope(ArmOf(walks, record.ruler_version))
        case _:
            assert_never(purpose)


def arm_diagnostic_scoring(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    source: RunSource,
) -> None:
    scope_memory(session)
    if bound_spend_book() is None:
        bind_spend_book(unbounded_spend_book())
    populate_session_scoring(
        session,
        campaign_config,
        source=source,
        partition=cycle_partition(session, session.bank.samples, campaign_config),
    )


@dataclass(frozen=True)
class OpenedDiagnostic:
    campaign: Campaign
    session: Session
    config: CampaignConfig
    framing: TaskDecomposition

    @property
    def points(self) -> PointContext:
        return self.session.points(self.framing)


async def open_diagnostic(stores: Stores, hop: CycleHop, *, source: RunSource) -> OpenedDiagnostic:
    """The one opener of every verb that scores a cycle's points outside its loop."""
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise DiagnosticError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
    bound, config = await bind_cycle_session(stores, campaign, hop)
    session = configure_and_apply_pipeline(bound, config)
    # Its cycle's pause stops the pass; the loop's skip and look-ahead are the loop's to spend.
    session.state.control = RunControl(enclosing=(stores.campaigns.cycle_dir(hop),))
    # As `run_optimization` does: a pp-self point's connector dispatches no inner run without it.
    publish_inner_spawn_context(session)
    arm_diagnostic_scoring(session, config, source=source)
    return OpenedDiagnostic(
        campaign, session, config, campaign_framing(stores, config, session.dataset)
    )


def cycle_partition(
    session: Session, rows: Sequence[Sample], campaign_config: CampaignConfig
) -> BankPartition:
    """What the bound cycle drew (`Session.bound`); *rows* are drawn only outside a cycle."""
    if session.drawn is not None:
        return session.drawn
    return partition_bank(rows, campaign_config.dataset_split, seed=campaign_config.run_seed)


async def diagnostic_pass(scoring: Awaitable[ScoredWalk]) -> ScoredWalk:
    """Nothing above a diagnostic verb catches the round loop's ``StopLoop``."""
    try:
        scored = await scoring
    except StopLoop as stop:
        info = STOP_REASON_INFO[stop.reason]
        unmeasured = "" if stop.unmeasured is None else f", {stop.unmeasured} cell(s) unmeasured"
        raise DiagnosticError(f"{info.label}{unmeasured}. {info.next_step}".strip()) from None
    if scored.stopped is not None:
        raise DiagnosticError(
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
        on_models, dataset_hash=session.bank.dataset_hash, newest=_PRICED_RUNS
    ):
        # An archive row carries no grade, and the price is no formula's reading.
        cost = cell_channels_of(m.cell, None).get("cost")
        if cost is not None:
            total += cost
            cells += 1
    return total / cells if cells else None


def _campaigns_on_origin(session: Session) -> list[str]:
    campaigns = session.store.campaigns
    own = campaigns.load_campaign(session.campaign_id) if session.campaign_id else None
    if own is None:
        return []
    return sorted(
        other.campaign_id
        for other in campaigns.list_campaigns(lifecycle="all")
        if other.campaign_id != own.campaign_id and other.root_content_hash == own.root_content_hash
    )


def _banked_instruments(session: Session) -> list[str]:
    campaigns = session.store.campaigns
    dataset_name = session.measured_dataset
    banked: list[str] = []
    for campaign in campaigns.list_campaigns(dataset_name, lifecycle="all"):
        origin = campaigns.standing_rounds(campaign.root_hop, moment=None).rounds.get(0)
        instrument = (
            None
            if origin is None
            else inner_origin_of(origin.close.origin.individual.pipeline_params)
        )
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
        banked_instruments=[] if instrument is None else _banked_instruments(session),
    )
    for w in preflight_warnings:
        logger.warning("preflight[%s]: %s — %s", w.code, w.title, w.detail)
    refuse_arm_below_round(session.purpose, preflight_warnings, measured_cell_usd=measured_usd)
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


async def init_optimization_loop(
    origin: CampaignOrigin,
    dataset: list[Sample],
    config: CampaignConfig,
    *,
    cb: RunCallbacks,
    no_divergence_check: bool,
    fork_on_divergence: bool,
    resume_from_round_override: int | None,
    session: Session,
) -> tuple[Cycle, CycleHop | None]:
    """The cycle holds the session it runs on, and beside it the cycle a divergence forked it from.

    INIT stays open: its caller moves onto a fork first, then closes it (`finalize_loop_state`).
    """
    init_cycle(session, resume_from_round_override=resume_from_round_override)
    await _emit_preflight_and_init_session(config, dataset, cb, session, origin)
    if resume_from_round_override is not None:
        # Only an admitted launch retracts rounds: a refused arm or preflight raised above.
        session.store.campaigns.rewind_to_round(session.hop, resume_from_round_override)
    cycle = Cycle.start(origin.origin, framing=origin.framing, session=session, config=config)
    forked_from: CycleHop | None = None
    # =1 is fresh (origin only); real resumes are >=2 (>=1 L1 round on disk).
    if session.state.resumed_from_round > 1:
        fork = await resume_with_divergence_check(
            session.store.campaigns,
            session.hop,
            session.state.resumed_from_round,
            session,
            cycle,
            dataset,
            skip_divergence_check=no_divergence_check,
            fork_on_divergence=fork_on_divergence,
        )
        if fork is not None:
            forked_from = session.hop
            cycle.session = session.bound(
                CycleHop(campaign_id=session.campaign_id, cycle_id=fork), purpose=session.purpose
            )
            # Off the fork's own chain: one cut at round 0 closes the origin again, then runs round 1.
            cycle.session.state.resumed_from_round = _next_round(cycle.session)
    return cycle, forked_from


def finalize_loop_state(cycle: Cycle, cb: RunCallbacks) -> None:
    """*cb* is the cycle's own: INIT exits on the cycle the loop runs, a fork's included."""
    session = cycle.session
    cycle.sample_index = SampleIndex.ensure_for(
        session.store,
        scorer=session.state.scoring.require_scorer(),
        bank=session.bank,
        sample_ids=session.state.scoring.require_partition().admitted_ids,
    )
    # A RUNNING cycle's `log.md` reads its formula off this bracket (`scorer_cell_formula`).
    cb.on_phase(
        CampaignPhase.INIT,
        "exit",
        view=init_exit(cb.view_context, cycle=cycle, session=session),
    )
    # The cycle id is FINAL only here; the spawn context was published before a fork could move it.
    retarget_inner_spawn(session)
