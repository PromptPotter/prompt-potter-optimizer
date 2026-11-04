"""The embedded launch entry: a host program drives one campaign in its own loop, holding a share
of its ceiling and no machine slot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.commands import archive_compaction, measuring
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import complete_registries, init_services
from promptpotter.application.jobs.launcher.admission import (
    embedded_share,
    probe_backend,
    release_slot,
)
from promptpotter.application.jobs.launcher.head_to_head import DatasetArms, admit_head_to_head
from promptpotter.application.jobs.launcher.run_job import run_held_job
from promptpotter.application.jobs.mint import (
    declare,
    fresh_campaign_id,
    mint_framed_cycle,
    resolve_resume,
)
from promptpotter.application.jobs.quota import land_job, sends_on
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.views.readout import StatusFn
from promptpotter.config.logging import setup_logging
from promptpotter.domain.campaign import Optimize
from promptpotter.domain.campaign_config import effective_config
from promptpotter.domain.command_kinds import (
    CompactArchivePayload,
    GradeBenchPayload,
    OriginGateDecisionPayload,
    SubmitArmsPayload,
    SubmittedArm,
)
from promptpotter.domain.job import EmbeddedRun
from promptpotter.domain.results import CycleResult
from promptpotter.domain.sample import Bank
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.store.dataset_access import resolve_dataset, runnable_definition
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.maintenance.archive_maintenance import ArchiveReport
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.bench import BenchScore
    from promptpotter.domain.campaign import RunDeclaration
    from promptpotter.domain.campaign_config import CampaignConfig, CycleConfig
    from promptpotter.domain.command_kinds import HeadToHeadRequest
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.phases import GateDecision
    from promptpotter.domain.run_mode import RunMode
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.spend import Budget
    from promptpotter.infrastructure.store.stores import Stores

# This module IS the embedded surface: a capability it does not name is one a host cannot find.
__all__ = [
    "compact_archive",
    "grade_bench",
    "open_session",
    "reindex_measurement_archive",
    "run_campaign",
    "run_head_to_head",
    "submit_arms",
    "submit_gate_decision",
]


async def open_session(
    dataset_name: str,
    *,
    stores: Stores | None = None,
    program: object | None = None,
    rows: Sequence[Sample] | None = None,
) -> Session:
    """Raises what ``probe_backend`` refuses a launch on; *rows* replace the definition's bank, which the mint labels."""
    setup_logging()
    complete_registries(every_treatment=False)
    if stores is None:
        stores = local_stores(tenant=None)
    down = await probe_backend(
        runnable_definition(resolve_dataset(stores, dataset_name).directory).backend_type
    )
    if down is not None:
        raise down
    session = await init_services(
        dataset_name=dataset_name,
        identity=stores.identity,
        stores=stores,
        program=program,
    )
    if rows is None:
        return session
    bank = Bank.of(rows)
    stores.tenant_datasets.put_bank(bank)
    return replace(session, bank=bank)


async def submit_gate_decision(stores: Stores, hop: CycleHop, decision: GateDecision) -> None:
    """Answer a cycle holding at its origin gate.

    Raises ``ConflictError`` where it is not holding: an early decision is refused, never kept.
    """
    await CommandDispatcher(stores).dispatch(
        CommandCall(
            OriginGateDecisionPayload(
                campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, decision=decision
            )
        )
    )


async def submit_arms(
    stores: Stores, hop: CycleHop, arms: Sequence[SubmittedArm], keep: Sequence[int] = ()
) -> None:
    """Answer a cycle holding for a round's arms: *arms* of the host's own, drafts kept by index.

    Raises ``ConflictError`` where it is not holding, or where *keep* names no draft of the hold.
    """
    await CommandDispatcher(stores).dispatch(
        CommandCall(
            SubmitArmsPayload(
                campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, arms=list(arms), keep=list(keep)
            )
        )
    )


async def grade_bench(stores: Stores, campaign_id: str) -> BenchScore:
    """Raises ``ConflictError`` where the served ``BenchStatus.refusal`` stands."""
    sent = await CommandDispatcher(stores).answer(
        measuring.grade_bench, CommandCall(GradeBenchPayload(campaign_id=campaign_id))
    )
    return sent.result


async def compact_archive(
    stores: Stores, *, mode: str, dataset: str | None = None, apply: bool = False
) -> ArchiveReport:
    """Raises what the dispatcher refuses the browser's and the terminal's compaction on."""
    payload = PayloadInvalidError.validating(
        CompactArchivePayload, {"mode": mode, "dataset": dataset, "apply": apply}
    )
    sent = await CommandDispatcher(stores).answer(
        archive_compaction.compact_archive, CommandCall(payload)
    )
    return sent.result


@dataclass(frozen=True, slots=True)
class _Mint:
    """A session on a dataset: the run mints the campaign it declares."""

    declaration: RunDeclaration


@dataclass(frozen=True, slots=True)
class _Resume:
    """A session bound to a campaign: the run resumes it under the selection its resume checked."""

    cycle: CycleConfig
    optimizer: SelectedOptimizer


async def run_campaign(
    session: Session,
    campaign_config: CampaignConfig | None = None,
    *,
    readout_sink: StatusFn | None = None,
    langfuse_session_id: str | None = None,
    budget: Budget,
    step_rounds: int | None = None,
    mode: RunMode,
) -> tuple[Session, CycleResult]:
    """Answers the session bound to the cycle the run ended on. Blocks under ``origin_gate: strict``
    until ``submit_gate_decision`` answers, and under an ``arm_gate`` until ``submit_arms`` does."""
    start: _Mint | _Resume
    if session.campaign_id:
        if campaign_config is not None:
            raise PayloadInvalidError(
                f"{session.hop.cycle_id} resumes under its campaign's declaration: pass no "
                "config to resume it",
                code="resume_declared",
            )
        campaign = session.store.campaigns.load_campaign(session.campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {session.campaign_id}")
        plan = resolve_resume(session, campaign, session.bank.samples)
        session, start = plan.session, _Resume(plan.cycle, plan.optimizer)
        campaign_config = plan.cycle.config
        connector = campaign.connector
    else:
        if campaign_config is None:
            raise PayloadInvalidError(
                "a mint declares its campaign: pass the config to run", code="mint_undeclared"
            )
        declaration = declare(session, campaign_config, purpose=Optimize())
        start = _Mint(declaration)
        campaign_config = effective_config(declaration)
        connector = declaration.environment.connector

    async def _run(held: HeldBudget) -> tuple[Session, CycleResult]:
        running = session
        match start:
            case _Resume(cycle=cycle, optimizer=optimizer):
                pass
            case _Mint(declaration=declaration):
                minted = await mint_framed_cycle(
                    running,
                    declaration,
                    running.bank.samples,
                    campaign_id=fresh_campaign_id(running),
                    task_text=None,
                )
                running, optimizer, cycle = minted.session, minted.optimizer, minted.cycle
                land_job(
                    running.store,
                    JobRegistry.attach(running.store.jobs_dir),
                    held.job_id,
                    running.hop,
                    held.share,
                )
        return await run_optimization(
            list(running.bank.samples),
            cycle,
            session=running,
            observers=build_run_observers(
                session=running,
                campaign_config=cycle.config,
                readout_sink=readout_sink,
            ),
            langfuse_session_id=langfuse_session_id,
            budget=held,
            mode=mode,
            optimizer=optimizer,
        )

    # Admitted BEFORE the mint, whose framing call is paid: a refusal leaves nothing written.
    return await embedded_share(
        campaign_config,
        stores=session.store,
        dataset_name=session.measured_dataset,
        hop=session.hop if session.cycle_id else None,
        requested=budget,
        step_rounds=step_rounds,
        sends=sends_on(session.store, campaign_config, connector),
        run=_run,
    )


async def run_head_to_head(
    session: Session,
    campaign_config: CampaignConfig,
    request: HeadToHeadRequest,
    *,
    mode: RunMode,
    readout_sink: StatusFn | None = None,
) -> list[tuple[str, Session, CycleResult]]:
    """Arms of one head-to-head on the session's dataset, each answered by its key: every arm
    planned and admitted before any is minted, then run one after another in the host's loop.

    Raises what ``mint-head-to-head`` refuses the browser's and the terminal's act on.
    """
    if session.campaign_id:
        raise PayloadInvalidError(
            f"a head-to-head mints its arms, and this session is bound to {session.campaign_id}: "
            "open it on the dataset",
            code="arms_unbound",
        )
    stores = session.store
    job_registry = JobRegistry.attach(stores.jobs_dir)
    admitted = await admit_head_to_head(
        DatasetArms(session, campaign_config),
        request,
        job_registry=job_registry,
        holder=EmbeddedRun(session.measured_dataset),
    )
    arms = await admitted.minted()
    ran: list[tuple[str, Session, CycleResult]] = []
    for i, arm in enumerate(arms):
        held = arm.held
        try:
            result, _observers = await run_held_job(
                job_registry,
                held.job_id,
                held.session,
                held.cycle,
                mode=mode,
                budget=held.budget,
                optimizer=held.optimizer,
                readout_sink=readout_sink,
            )
        except BaseException as exc:
            # The arms not yet run hold their shares, and no other process would hand them back.
            for rest in arms[i + 1 :]:
                release_slot(
                    job_registry, rest.held.job_id, exc, stores=stores, hop=rest.held.session.hop
                )
            raise
        ran.append((arm.arm_key, held.session, result))
    return ran
