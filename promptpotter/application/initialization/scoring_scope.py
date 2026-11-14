from __future__ import annotations

import logging
from asyncio import CancelledError
from collections.abc import AsyncIterator, Awaitable, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.initialization.session import ScorerSetup, Session
from promptpotter.application.initialization.wiring import bind_cycle_session
from promptpotter.application.pipeline_resolve import configure_and_apply_pipeline, cycle_config
from promptpotter.application.preflight import refuse_below_reasoning_floor
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.inner.tasks import (
    inner_spawn_of,
)
from promptpotter.application.scoring.classification import build_degradation_checks
from promptpotter.application.scoring.evaluators import (
    connector_report_owner,
    observation_owner,
)
from promptpotter.application.scoring.judges.registry import build_evaluators
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import BankPartition, partition_bank
from promptpotter.domain.campaign import Campaign, Compare, Optimize
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.phases import STOP_REASON_INFO
from promptpotter.domain.records.run_records import StopLoop
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import active_cycle_ledger
from promptpotter.infrastructure.run_scope import RunScope, run_scope, scoped
from promptpotter.infrastructure.store.archive_queries import (
    ArmOf,
    CampaignWalks,
    CellOf,
    MemoryScope,
    OpenAt,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import DiagnosticError

if TYPE_CHECKING:
    from promptpotter.application.scoring.search_point_scorer import ScoredWalk

logger = logging.getLogger(__name__)


def scorer_setup(
    session: Session, campaign_config: CampaignConfig, *, partition: BankPartition
) -> ScorerSetup:
    """What *session* scores under *campaign_config*, built whole once per launch or pass."""
    connector = session.backend_client.connector
    return ScorerSetup(
        scorer=scorer_of(
            campaign_config,
            verifier_graded=all_verifier_graded(s.ground_truth for s in session.bank.samples),
        ),
        display_metric=campaign_config.display_metric,
        partition=partition,
        degradation_checks=tuple(build_degradation_checks(campaign_config)),
        judges=build_evaluators(
            campaign_config.judges,
            beside=(
                connector_report_owner(connector.name, connector.cell_report_keys),
                observation_owner(session.pipeline_schema.observation_keys),
            ),
        ),
    )


def run_memory(session: Session) -> MemoryScope:
    """What a run on *session* remembers, by its purpose; an L4 cell keeps the scope its spawner
    opened (`runner/inner/spawn.py::_run_inner_campaign`)."""
    if isinstance(cell := run_scope().memory, CellOf):
        return cell
    match purpose := session.purpose:
        case Optimize():
            return OpenAt()
        case Compare(arm=arm):
            record = session.store.campaigns.load_head_to_head(arm.head_to_head_id)
            assert record is not None, "an arm's mint declares its head-to-head"
            walks = CampaignWalks(session.store.campaigns, session.campaign_id)
            return ArmOf(walks, record.ruler_version)
        case _:
            assert_never(purpose)


@contextmanager
def session_scope(session: Session) -> Iterator[RunScope]:
    """The scope's fields a session lays, spelled once for a run (`runner/entry.py::run_optimization`)
    and a pass outside one: what it remembers, its grader cache and the spawn its cells take."""
    # The spawn unconditional, and entered under the memory just scoped: its ruler reads it.
    with (
        scoped(memory=run_memory(session), judge_cache=session.store.judge_reuse),
        scoped(spawn=inner_spawn_of(session)) as scope,
    ):
        yield scope


@contextmanager
def arm_diagnostic_scoring(session: Session, campaign_config: CampaignConfig) -> Iterator[None]:
    """The pass's scoring, under the scope a run on *session* would take, for the block."""
    # A run is refused at its mint (`jobs/mint.py::refuse_unrunnable`); a pass outside one, here.
    refuse_below_reasoning_floor(session.pipeline_params.nodes.items())
    with session_scope(session):
        session.state.scoring = scorer_setup(
            session,
            campaign_config,
            partition=cycle_partition(session, session.bank.samples, campaign_config),
        )
        yield


@dataclass(frozen=True)
class OpenedDiagnostic:
    campaign: Campaign
    session: Session
    config: CampaignConfig
    framing: TaskDecomposition

    @property
    def points(self) -> PointContext:
        return self.session.points(self.framing)


@asynccontextmanager
async def open_diagnostic(
    stores: Stores, hop: CycleHop, *, acting: str | None = None
) -> AsyncIterator[OpenedDiagnostic]:
    """The one opener of every verb that scores a cycle's points outside its loop: its scope is
    the block's, so a pass the loop runs leaves the loop's own standing. *acting* is the command
    the pass acts for, whose `cancel` stops it."""
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise DiagnosticError(f"campaign {hop.campaign_id!r} has no manifest on disk.")
    cycle = cycle_config(stores, campaign, hop)
    config = cycle.config
    async with bind_cycle_session(stores, campaign, hop) as bound:
        session = configure_and_apply_pipeline(bound, cycle)
        # Its cycle's pause stops the pass; the loop's skip and look-ahead are the loop's to spend.
        session.state.control = RunControl(
            commands=stores.commands,
            enclosing=(stores.subject(hop),),
            acting=() if acting is None else (acting,),
        )
        # Its scope spawns as `run_optimization`'s: a pp-self point's connector dispatches no inner run without it.
        with arm_diagnostic_scoring(session, config):
            yield OpenedDiagnostic(
                campaign, session, config, campaign_framing(stores, config, session.dataset)
            )


def cycle_partition(
    session: Session, rows: Sequence[Sample], campaign_config: CampaignConfig
) -> BankPartition:
    """What the bound cycle drew (`Session.bound`); *rows* are drawn only outside a cycle."""
    if session.drawn is not None:
        return session.drawn
    return partition_bank(rows, campaign_config.dataset_split, seed=campaign_config.run_seed)


def _refused(stop: StopLoop) -> DiagnosticError:
    info = STOP_REASON_INFO[stop.reason]
    unmeasured = "" if stop.unmeasured is None else f", {stop.unmeasured} cell(s) unmeasured"
    return DiagnosticError(f"{info.label}{unmeasured}. {info.next_step}".strip())


async def diagnostic_pass(scoring: Awaitable[ScoredWalk], session: Session) -> ScoredWalk:
    """Nothing above a diagnostic verb catches the round loop's ``StopLoop``; a pause it asked
    comes back with the cells its pass took."""
    try:
        scored = await scoring
    except StopLoop as stop:
        raise _refused(stop) from None
    if (halt := scored.halt) is not None:
        if isinstance(halt, StopLoop):
            raise _refused(halt) from None
        if not isinstance(halt, KeyboardInterrupt | CancelledError) or not (
            session.state.control.paused(halt)
        ):
            raise halt
        return scored
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
    if (book := run_scope().book) is not None and book.ledger is None:
        book.ledger = ledger
    with scoped(ledger=ledger):
        yield ledger
