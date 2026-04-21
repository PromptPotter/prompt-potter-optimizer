"""Service bootstrap helpers for the CLI — makes the application layer runnable.

All the "glue" between argparse and the application layer lives here:
verbose-mode toggle, logging setup, service initialization, pipeline
configuration, scoring-context preparation, and startup summary logging.
Commands import these instead of building services by hand.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig

logger = logging.getLogger("promptpotter.presentation.cli")

_VERBOSE = False


def set_verbose(value: bool) -> None:
    """Toggle verbose mode. Called once from ``main()`` before dispatch."""
    global _VERBOSE
    _VERBOSE = value


def _noop(*_: object, **__: object) -> None:
    pass


def _status_sink(msg: str) -> None:
    """Route setup status through the normal logger only in verbose mode."""
    if _VERBOSE:
        logger.info(msg)


def log_startup_summary(
    session: Session,
    pipeline_params: dict | None,
    dataset_len: int,
    backend_url: str,
    dataset_name: str | None,
) -> None:
    """One-line collapsed summary of pipeline + backend + dataset + active nodes."""
    ps = session.pipeline_schema
    pipe = f"{ps.name} v{ps.version}" if ps else "pipeline unavailable"
    active = list((pipeline_params or {}).get("steps") or [])
    nodes = f"{len(active)} node{'s' if len(active) != 1 else ''}"
    if active:
        nodes += f" ({', '.join(active)})"
    ds = f"{dataset_name or '?'} ({dataset_len} queries)"
    logger.info("%s · %s · backend %s · dataset %s", pipe, nodes, backend_url, ds)


async def init_services_cli(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = DEFAULT_BACKEND_ID,
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    dataset_name: str | None = None,
    take_over: bool = False,
    tenant_id: str = "default",
) -> Session:
    """Initialize services (logging + service init) for a CLI command."""
    from promptpotter.application.campaign.campaign_setup import init_services
    from promptpotter.config.logging import setup_logging

    setup_logging(style="full" if _VERBOSE else "cli")
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    return await init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=_status_sink,
        take_over=take_over,
        tenant_id=tenant_id,
    )


def configure_pipeline(session: Session, campaign_config: CampaignConfig) -> dict:
    """Configure pipeline, apply filtered schema to session. Returns pipeline_params."""
    from promptpotter.application.campaign.config import configure_and_apply_pipeline

    log = logger.info if _VERBOSE else _noop
    return configure_and_apply_pipeline(session, campaign_config, log=log)


async def prepare_scoring_context(
    session: Session,
    train_data: list | None,
    campaign_config: CampaignConfig | None = None,
    pipeline_params: dict | None = None,
    listener: Any | None = None,
):
    """Load baseline + dataset, run baseline eval on full dataset."""
    from promptpotter.application.campaign.data import prepare_scoring_context as _svc_prepare

    baseline, dataset, campaign_rounds, baseline_results = await _svc_prepare(
        session.experiment_extract,
        train_data,
        campaign_config,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
        listener=listener,
    )

    if _VERBOSE:
        logger.info("Evaluation data: %d queries", len(dataset))
    return baseline, dataset, campaign_rounds, baseline_results


def load_cli_baseline(session: Session):
    """Shared baseline-prompt load used by ``scan`` and ``show-scan``."""
    from promptpotter.application.campaign.campaign_setup import load_baseline_prompt

    prompt_nodes = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    return load_baseline_prompt(
        session.experiment_extract,
        prompt_node_names=prompt_nodes,
        dataset_name=session.dataset_name,
    )
