"""The embedded launch entry — a host Python program driving one campaign inside its own event loop.

Peer of ``jobs/launcher/mint_and_start.py``, which detaches the run onto a background task and holds
the capacity-1 machine slot; this one blocks in the caller's loop and takes no slot. Three steps
rather than one because every caller does its own work between them — build the config, resolve the
pipeline, slice the trainset, read the origin before deciding to spend.

Not to be confused with ``Connector.execution = "in_process"``, which is the BACKEND running inside
our process; this is us running inside someone else's program.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.mint import fresh_campaign_id, prepare_fresh_cycle
from promptpotter.application.origin import CampaignOrigin, prepare_scoring_context
from promptpotter.application.run_observers import RunObservers, build_run_observers
from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.application.runner.origin_gate import submit_gate_decision
from promptpotter.config.logging import setup_logging
from promptpotter.config.settings import DEFAULT_BACKEND_ID, DEFAULT_BACKEND_URL
from promptpotter.domain.results import CycleResult

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.presentation.views.live.display import LiveDisplay
    from promptpotter.shared.identity import IdentityContext

# `submit_gate_decision` is re-exported under its OWN name because this module IS the embedded
# surface — a capability it does not name is one a host program cannot find, and a second spelling
# for it would be a synonym rather than a channel. A host with no TTY and no HTTP client had no
# third way to answer: `origin_gate` defaults to `strict`, `_spawn_stdin_reader` returns None off a
# Jupyter/harness stdin, and `_await_gate_decision` then polls forever.
__all__ = [
    "mint_and_score_origin",
    "open_session",
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
) -> Session:
    """``identity`` and ``stores`` pass straight through to :func:`init_services`. Dropping them
    here pinned every embedded host to ``projects/default/`` — this is an adapter over that call,
    so a parameter it declines to forward is a capability the host cannot reach at all."""
    setup_logging()
    session = await init_services(
        dataset_name=dataset_name,
        backend_url=backend_url,
        backend_id=backend_id,
        on_status=on_status,
        identity=identity,
        stores=stores,
    )
    if on_status is not None:
        on_status(f"Dataset    : {dataset_name} ({len(session.samples)} queries)")
        on_status(f"Session terms: {len(session.index_terms)}")
    return session


async def mint_and_score_origin(
    session: Session,
    train_data: list[Sample],
    campaign_config: CampaignConfig,
    *,
    pipeline_params: dict[str, Any] | None = None,
    display: LiveDisplay | None = None,
    on_status: StatusFn | None = None,
) -> tuple[RunObservers, list[Sample], CampaignOrigin]:
    """Mint the campaign, bind observers, score the origin — through ``prepare_fresh_cycle``, the same
    prologue ``new`` and the web mint run, never a second path for this caller alone."""
    if not session.campaign_id:
        prepare_fresh_cycle(
            session,
            campaign_config,
            train_data,
            campaign_id=fresh_campaign_id(session, campaign_config),
        )

    observers = build_run_observers(
        session=session,
        campaign_config=campaign_config,
        dataset=train_data,
        display=display,
    )

    origin, dataset = await prepare_scoring_context(
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=observers.callbacks,
    )
    if display is not None:
        display.set_origin(origin.report.accuracy)
    if on_status is not None:
        on_status(
            f"Evaluation data: {len(dataset)} queries  |  Origin: {origin.report.accuracy:.1%}"
        )
    return observers, dataset, origin


async def run_campaign(
    observers: RunObservers,
    dataset: list[Sample],
    origin: CampaignOrigin,
    campaign_config: CampaignConfig,
    *,
    session: Session,
    langfuse_session_id: str | None = None,
    spend_budget_usd: float | None = None,
    token_budget: int | None = None,
    mode: RunMode | None = None,
) -> CycleResult:
    """Run the loop over an origin this caller already scored. The two ceilings and ``mode`` are the
    same flags the web launcher passes — an embedded run that omits a ceiling keeps the campaign's
    own value for it."""
    return await run_optimization(
        dataset,
        campaign_config,
        session=session,
        observers=observers,
        origin=origin,
        langfuse_session_id=langfuse_session_id,
        spend_budget_usd=spend_budget_usd,
        token_budget=token_budget,
        mode=mode,
    )
