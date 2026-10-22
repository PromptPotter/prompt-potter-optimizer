"""The embedded launch entry: a host program drives one campaign in its own loop, holding a share
of its ceiling and no machine slot."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.account import local_stores
from promptpotter.application.campaign_config import effective_config
from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.payloads import CompactArchivePayload
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import complete_registries, init_services
from promptpotter.application.jobs.launcher.admission import embedded_share, probe_backend
from promptpotter.application.jobs.mint import (
    declare,
    fresh_campaign_id,
    mint_framed_cycle,
    refuse_drifted_resume,
)
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.maintenance.archive_maintenance import reindex_measurement_archive
from promptpotter.application.pipeline_resolve import (
    configure_and_apply_pipeline,
    resolve_campaign_config,
)
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import run_optimization
from promptpotter.application.views.readout import StatusFn
from promptpotter.config.logging import setup_logging
from promptpotter.domain.campaign import Compare, Optimize, Purpose
from promptpotter.domain.command_kinds import (
    GradeBenchPayload,
    OriginGateDecisionPayload,
    SubmitArmsPayload,
    SubmittedArm,
)
from promptpotter.domain.results import CycleResult
from promptpotter.domain.sample import Bank
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.shared.errors import NotFoundError, PayloadInvalidError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.maintenance.archive_maintenance import ArchiveReport
    from promptpotter.domain.bench import BenchScore
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
    down = await probe_backend(backend_type_of_dataset(stores, dataset_name))
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
    sent = await CommandDispatcher(stores).dispatch(
        CommandCall(GradeBenchPayload(campaign_id=campaign_id))
    )
    score: BenchScore = sent.result
    return score


async def compact_archive(
    stores: Stores, *, mode: str, dataset: str | None = None, apply: bool = False
) -> ArchiveReport:
    """Raises what the dispatcher refuses the browser's and the terminal's compaction on."""
    payload = PayloadInvalidError.validating(
        CompactArchivePayload, {"mode": mode, "dataset": dataset, "apply": apply}
    )
    sent = await CommandDispatcher(stores).dispatch(CommandCall(payload))
    report: ArchiveReport = sent.result
    return report


async def run_campaign(
    session: Session,
    campaign_config: CampaignConfig | None = None,
    *,
    readout_sink: StatusFn | None = None,
    langfuse_session_id: str | None = None,
    budget: Budget,
    step_rounds: int | None = None,
    mode: RunMode,
    purpose: Purpose = Optimize(),  # noqa: B008 — frozen
) -> tuple[Session, CycleResult]:
    """Answers the session bound to the cycle the run ended on. Blocks under ``origin_gate: strict``
    until ``submit_gate_decision`` answers, and under an ``arm_gate`` until ``submit_arms`` does."""
    declaration = None
    if session.campaign_id:
        if campaign_config is not None or isinstance(purpose, Compare):
            raise PayloadInvalidError(
                f"{session.hop.cycle_id} resumes under its campaign's declaration: pass no "
                "config and no arm to resume it",
                code="resume_declared",
            )
        campaign = session.store.campaigns.load_campaign(session.campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {session.campaign_id}")
        refuse_drifted_resume(session, campaign, session.bank.samples)
        campaign_config = resolve_campaign_config(session.store, campaign, session.hop)
        session = configure_and_apply_pipeline(session, campaign_config)
    else:
        if campaign_config is None:
            raise PayloadInvalidError(
                "a mint declares its campaign: pass the config to run", code="mint_undeclared"
            )
        declaration = declare(session, campaign_config, purpose=purpose)
        campaign_config = effective_config(declaration)
    # Admitted BEFORE the mint, whose framing call is paid: a refusal leaves nothing written.
    async with embedded_share(
        campaign_config,
        stores=session.store,
        dataset_name=session.measured_dataset,
        hop=session.hop if session.cycle_id else None,
        requested=budget,
        step_rounds=step_rounds,
    ) as (job_id, held):
        if declaration is not None:
            minted = await mint_framed_cycle(
                session,
                declaration,
                session.bank.samples,
                campaign_id=fresh_campaign_id(session),
                task_text=None,
            )
            session = minted.session
            JobRegistry.attach().update_target(job_id, hop=session.hop)
        return await run_optimization(
            list(session.bank.samples),
            campaign_config,
            session=session,
            observers=build_run_observers(
                session=session,
                campaign_config=campaign_config,
                readout_sink=readout_sink,
            ),
            langfuse_session_id=langfuse_session_id,
            budget=held,
            mode=mode,
        )
