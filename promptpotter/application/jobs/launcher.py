"""Background-task launcher — mints + spawns a campaign run from one command.

The ``mint-campaign`` apply path mirrors CLI ``new <dataset>``: build
session → load campaign config → prepare cycle → ``auto_mint_session``
→ build observers → ``asyncio.create_task`` for ``run_optimization``.
The 202 returns the moment the campaign exists on disk; the run
proceeds in background tracked by :class:`JobRegistry`.

``_CYCLE_LEDGER`` ContextVar isolation (set by ``build_run_observers``,
cleared by ``drain_all``) lets multiple concurrent campaigns coexist
without leaking ledger state across asyncio tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from promptpotter.application.bootstrap import init_services
from promptpotter.application.bootstrap.session import Session, auto_mint_session
from promptpotter.application.config import (
    CampaignConfig,
    configure_and_apply_pipeline,
    load_campaign_config,
)
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    check_launch_quotas,
    effective_spend_cap_usd,
)
from promptpotter.application.jobs.registry import Job, JobRegistry
from promptpotter.application.origin import load_origin_prompt
from promptpotter.application.runner import build_origin_cycle_id
from promptpotter.application.runner.entry import run_optimization
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.infrastructure.store import Stores

logger = logging.getLogger(__name__)


class LaunchError(RuntimeError):
    """Raised on mint-time failures (missing dataset, malformed config, …)."""


async def mint_campaign_command(
    *,
    stores: Stores,
    dataset_name: str,
    job_registry: JobRegistry,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> tuple[str, str, Job]:
    """Mint a fresh campaign + cycle, then spawn the runner in the background.

    Returns ``(campaign_id, cycle_id, job)``. The asyncio task is detached;
    the caller's 202 response goes out the moment this returns. Background
    progress shows up via the canonical ledger + `dashboard.json` stream.
    """
    dataset_root = _datasets_root() / dataset_name
    if not dataset_root.is_dir():
        raise LaunchError(f"dataset not found: {dataset_name!r} (no {dataset_root}/)")

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=_claim_email(stores),
    )
    check_launch_quotas(user=user, job_registry=job_registry, rate_limited=True)
    spend_budget_usd = effective_spend_cap_usd(
        requested_cap_usd=spend_budget_usd,
        user=user,
        job_registry=job_registry,
        stores=stores,
    )

    session = await init_services(
        backend_url=backend_url,
        dataset_name=dataset_name,
        identity=stores.identity,
    )

    file_config = _load_dataset_campaign_config(dataset_root)
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = load_campaign_config({**profile, **file_config})

    train_data = session.samples or []
    pipeline_params = configure_and_apply_pipeline(
        session, campaign_config, log=lambda *_a, **_k: None
    )
    schema = session.pipeline_schema
    origin = load_origin_prompt(
        session.experiment_extract,
        prompt_node_names=schema.prompt_node_names() if schema else [],
        dataset_name=session.dataset_name,
    )
    cycle_id = build_origin_cycle_id(origin, schema, train_data)

    _session_id, campaign_id, cycle_id = auto_mint_session(
        session,
        campaign_config,
        cycle_id=cycle_id,
        origin_prompt_fields=origin.prompt_field_dict(),
        dataset_size=len(train_data),
        experiment_id=session.experiment_id,
        pipeline_params=pipeline_params,
        active_steps=list(pipeline_params.get("steps", [])),
    )

    job = job_registry.create(
        user_id=str(stores.identity.user_id),
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
    )

    task = asyncio.create_task(
        _run_in_background(
            session=session,
            campaign_config=campaign_config,
            train_data=train_data,
            job_registry=job_registry,
            job_id=job.job_id,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
        ),
        name=f"job-{job.job_id}",
    )
    job_registry.attach_task(job.job_id, task)

    logger.info(
        "mint-campaign: minted %s/%s for user %s (job %s)",
        campaign_id,
        cycle_id,
        stores.identity.user_id,
        job.job_id,
    )
    return campaign_id, cycle_id, job


async def start_run_command(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    campaign_id: str,
    cycle_id: str,
    kind: str,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> Job:
    """Spawn a runner against an existing cycle. ``kind`` ∈ ``{"new", "resume"}``.

    Mirrors CLI ``resume`` for ``kind="resume"`` and CLI ``new`` re-mint for
    ``kind="new"`` (used after pause or to retry an interrupted launch).
    """
    if kind not in ("new", "resume"):
        raise LaunchError(f"start-run kind must be 'new' or 'resume', got {kind!r}")

    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise LaunchError(f"campaign not found or not owned: {campaign_id}")

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=_claim_email(stores),
    )
    check_launch_quotas(user=user, job_registry=job_registry, rate_limited=False)
    spend_budget_usd = effective_spend_cap_usd(
        requested_cap_usd=spend_budget_usd,
        user=user,
        job_registry=job_registry,
        stores=stores,
    )

    dataset_name = campaign.dataset_name
    session = await init_services(
        backend_url=backend_url,
        dataset_name=dataset_name,
        identity=stores.identity,
    )

    file_config = _load_dataset_campaign_config(_datasets_root() / dataset_name)
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = load_campaign_config({**profile, **file_config})

    train_data = session.samples or []
    configure_and_apply_pipeline(session, campaign_config, log=lambda *_a, **_k: None)
    session.state.cycle_id = cycle_id

    job = job_registry.create(
        user_id=str(stores.identity.user_id),
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
    )

    task = asyncio.create_task(
        _run_in_background(
            session=session,
            campaign_config=campaign_config,
            train_data=train_data,
            job_registry=job_registry,
            job_id=job.job_id,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
        ),
        name=f"job-{job.job_id}",
    )
    job_registry.attach_task(job.job_id, task)
    return job


async def _run_in_background(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    train_data: list[Any],
    job_registry: JobRegistry,
    job_id: str,
    halt_at_accuracy: float | None,
    spend_budget_usd: float | None,
) -> None:
    """Asyncio task body — drives the run, updates registry on transitions."""
    from promptpotter.application.run_observers import build_run_observers

    job_registry.mark_started(job_id)
    try:
        observers = build_run_observers(
            session=session,
            campaign_config=campaign_config,
            dataset=train_data,
            display=None,
            resumed_from_round=None,
            origin_accuracy=0.0,
        )
        result = await run_optimization(
            train_data,
            campaign_config,
            session=session,
            observers=observers,
            experiment_id=session.experiment_id,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
        )
        stop_reason = getattr(result, "stop_reason", None)
        status = "completed"
        if stop_reason and str(stop_reason).upper() == "INTERRUPTED":
            status = "stopped"
        job_registry.mark_finished(
            job_id,
            status=status,  # type: ignore[arg-type]
            stop_reason=str(stop_reason) if stop_reason else None,
        )
    except asyncio.CancelledError:
        job_registry.mark_finished(job_id, status="stopped", stop_reason="task_cancelled")
        raise
    except Exception as exc:
        logger.exception("job %s failed", job_id)
        job_registry.mark_finished(job_id, status="failed", stop_reason=str(exc))


def _datasets_root() -> Path:
    """Resolve the datasets/ root from this module's location."""
    return Path(__file__).resolve().parents[3] / "datasets"


def _claim_email(stores: Stores) -> str | None:
    """Best-effort read of the OIDC email claim off ``IdentityContext.claims``."""
    raw = stores.identity.claims.get("email")
    return raw if isinstance(raw, str) else None


def _load_dataset_campaign_config(dataset_root: Path) -> dict[str, Any]:
    """Read ``datasets/{name}/campaign.json``; return empty dict when absent."""
    path = dataset_root / "campaign.json"
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    return json.loads(raw)  # type: ignore[no-any-return]


__all__ = ["LaunchError", "QuotaExceededError", "mint_campaign_command", "start_run_command"]
