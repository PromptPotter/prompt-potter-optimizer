"""The embedded launch entry: a host program drives one campaign in its own loop, holding no slot."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.payloads import OriginGateDecisionPayload
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.admission import probe_backend
from promptpotter.application.jobs.mint import (
    fresh_campaign_id,
    mint_framed_cycle,
    refuse_drifted_resume,
)
from promptpotter.application.jobs.quota import unadmitted_limits
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    purge_cold_store,
    reindex_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.application.runner.grade_bench import grade_line_bench
from promptpotter.config.logging import setup_logging
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import DEFAULT_BACKEND_ID, DEFAULT_BACKEND_URL
from promptpotter.domain.campaign import ArmRequest
from promptpotter.domain.results import CycleResult
from promptpotter.infrastructure.identity.migration import registered_or_default_identity
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.infrastructure.store.stores import build_stores
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.launch_limits import LaunchLimits
    from promptpotter.domain.phases import GateDecision
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores

# This module IS the embedded surface: a capability it does not name is one a host cannot find.
__all__ = [
    "compact_measurement_archive",
    "grade_line_bench",
    "open_session",
    "purge_cold_store",
    "reindex_measurement_archive",
    "restore_measurement_archive",
    "run_campaign",
    "submit_gate_decision",
]

# A host's readout-line sink. ``None`` is silent; the readout is on disk either way.
StatusFn = Callable[[str], None]


async def open_session(
    dataset_name: str,
    *,
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    stores: Stores | None = None,
    program: object | None = None,
) -> Session:
    """Open a session on *dataset_name*; raises ``BackendUnreachableError`` where its backend is down.

    *stores* names the workspace and whose it is; unset, the local operator's own.
    *program* rides the backend client as ``InProcessWorkload.program``, for an in-process backend.
    """
    setup_logging()
    if stores is None:
        stores = build_stores(registered_or_default_identity(), projects_root=DEFAULT_PROJECTS_ROOT)
    down = await probe_backend(backend_type_of_dataset(stores, dataset_name), backend_url)
    if down is not None:
        raise down
    return await init_services(
        dataset_name=dataset_name,
        backend_url=backend_url,
        backend_id=backend_id,
        identity=stores.identity,
        stores=stores,
        program=program,
    )


async def submit_gate_decision(stores: Stores, hop: CycleHop, decision: GateDecision) -> None:
    """Answer a cycle holding at its origin gate.

    Raises ``ConflictError`` where it is not holding: an early decision is refused, never kept.
    """
    await CommandDispatcher(stores).dispatch_cycle_command(
        CommandCall(
            OriginGateDecisionPayload(
                campaign_id=hop.campaign_id, cycle_id=hop.cycle_id, decision=decision
            ),
            uuid.uuid4().hex,
        ),
        expected_version=None,
    )


async def run_campaign(
    session: Session,
    train_data: list[Sample],
    campaign_config: CampaignConfig,
    *,
    readout_sink: StatusFn | None = None,
    langfuse_session_id: str | None = None,
    limits: LaunchLimits,
    mode: RunMode,
    arm: ArmRequest | None = None,
) -> CycleResult:
    """Mint a campaign and run it from its origin, or resume the cycle *session* is bound to.

    *limits* set the run's budget over the config's own; a host holds no slot, so none is admitted.
    *readout_sink* receives each readout line as it is written.
    Blocks at round 0 under ``origin_gate: strict`` until ``submit_gate_decision`` answers.
    """
    if not session.campaign_id:
        minted = await mint_framed_cycle(
            session,
            campaign_config,
            train_data,
            campaign_id=fresh_campaign_id(session, campaign_config),
            task_text=None,
            arm=arm,
            limits=limits,
        )
        campaign_config = minted.campaign_config
    else:
        campaign = session.store.campaigns.load_campaign(session.campaign_id)
        if campaign is None:
            raise NotFoundError(f"campaign not found: {session.campaign_id}")
        refuse_drifted_resume(session, campaign_config, campaign, session.hop)
    held = unadmitted_limits(
        campaign_config,
        stores=session.store,
        hop=session.hop if session.state.cycle_id else None,
        requested=limits,
    )
    return await run_optimization(
        train_data,
        campaign_config,
        session=session,
        observers=build_run_observers(
            session=session,
            campaign_config=campaign_config,
            readout_sink=readout_sink,
        ),
        langfuse_session_id=langfuse_session_id,
        limits=held,
        mode=mode,
    )
