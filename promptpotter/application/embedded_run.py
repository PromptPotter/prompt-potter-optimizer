"""The embedded launch entry — a host Python program driving one campaign inside its own event loop.

Peer of ``jobs/launcher/mint_and_start.py``, which detaches the run onto a background task and takes
a machine slot or queues for one; this one blocks in the caller's loop and takes no slot. Two steps
rather than one because every caller does its own work between them — build the config, resolve the
pipeline, slice the trainset.

Not to be confused with ``Connector.execution = "in_process"``, which is the BACKEND running inside
our process; this is us running inside someone else's program.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.mint import fresh_campaign_id, prepare_fresh_cycle
from promptpotter.application.jobs.quota import unadmitted_limits
from promptpotter.application.maintenance.archive_maintenance import (
    compact_measurement_archive,
    purge_cold_store,
    reindex_measurement_archive,
    restore_measurement_archive,
)
from promptpotter.application.run_observers import build_run_observers
from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.application.runner.origin_gate import submit_gate_decision
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import DEFAULT_BACKEND_ID, DEFAULT_BACKEND_URL
from promptpotter.domain.results import CycleResult

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.launch_limits import LaunchLimits
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.presentation.terminal.live.display import LiveDisplay
    from promptpotter.shared.identity import IdentityContext

# `submit_gate_decision` is re-exported under its OWN name because this module IS the embedded
# surface — a capability it does not name is one a host program cannot find, and a second spelling
# for it would be a synonym rather than a channel. A host with no TTY and no HTTP client had no
# third way to answer: `origin_gate` defaults to `strict`, `_spawn_stdin_reader` returns None off a
# Jupyter/harness stdin, and `_await_gate_decision` then polls forever.
# The archive-maintenance passes are re-exported for the same reason: a host driving campaigns in
# its own loop owns the measurement archive they fill, and reclaiming it through the CLI would mean
# leaving the process that has the `Stores` already built.
__all__ = [
    "compact_measurement_archive",
    "open_session",
    "purge_cold_store",
    "reindex_measurement_archive",
    "restore_measurement_archive",
    "run_campaign",
    "submit_gate_decision",
]

# Where a host program's progress lines go. ``None`` is silent, which is the right default for a
# library: a caller that wants the run readout passes a ``LiveDisplay`` to the next step instead.
StatusFn = Callable[[str], None]


async def open_session(
    dataset_name: str,
    *,
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    on_status: StatusFn | None = None,
    identity: IdentityContext | None = None,
    stores: Stores | None = None,
    program: object | None = None,
) -> Session:
    """``identity``, ``stores`` and ``program`` pass straight through to :func:`init_services`: a
    parameter this adapter declines to forward is a capability no host can reach."""
    setup_logging()
    session = await init_services(
        dataset_name=dataset_name,
        backend_url=backend_url,
        backend_id=backend_id,
        on_status=on_status,
        identity=identity,
        stores=stores,
        program=program,
    )
    if on_status is not None:
        on_status(f"Dataset    : {dataset_name} ({len(session.samples)} queries)")
        on_status(f"Session terms: {len(session.index_terms)}")
    return session


async def run_campaign(
    session: Session,
    train_data: list[Sample],
    campaign_config: CampaignConfig,
    *,
    display: LiveDisplay | None = None,
    langfuse_session_id: str | None = None,
    limits: LaunchLimits,
    mode: RunMode,
) -> CycleResult:
    """Mint through ``prepare_fresh_cycle``, the prologue ``new`` and the web mint run, then run the
    loop from its origin. With no slot there is no admission: the run holds its declaration as-is —
    the config's budget under *limits*' — composed exactly as every admitted launch composes it, and
    ``LaunchLimits()`` adds nothing to the config's own."""
    if not session.campaign_id:
        prepare_fresh_cycle(
            session,
            campaign_config,
            train_data,
            campaign_id=fresh_campaign_id(session, campaign_config),
        )
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
            dataset=train_data,
            display=display,
        ),
        langfuse_session_id=langfuse_session_id,
        limits=held,
        mode=mode,
    )
