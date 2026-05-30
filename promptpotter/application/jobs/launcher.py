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

from promptpotter import connectors
from promptpotter.application.bootstrap import init_services
from promptpotter.application.bootstrap.session import Session, auto_mint_session
from promptpotter.application.bootstrap.wiring import resolve_dataset_config_dir
from promptpotter.application.config import (
    CampaignConfig,
    configure_and_apply_pipeline,
    load_campaign_config,
)
from promptpotter.application.datasets.csv_ingest import Table, materialize_samples
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    DraftCampaignRegistry,
)
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
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


class OriginIncompleteError(RuntimeError):
    """Raised when the origin-readiness checklist still has gaps at mint time.

    Carries the blocking :class:`FieldGap`s so the API can surface every
    unresolved field (422 ``origin_incomplete``). The draft is left intact —
    the operator resolves the gaps and retries.
    """

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(f"origin incomplete — unresolved fields: {fields}")


async def _run_preflight(backend_type: str, backend_url: str) -> None:
    """Resolve the connector and run its reachability probe.

    Connectors opt out of preflight by leaving ``Connector.preflight = None``
    (the ``promptpotter`` in-process connector does this — nothing to probe).
    Probe raises :class:`~promptpotter.connectors.BackendUnreachableError`
    on failure; the dispatcher's central catch in ``_record_and_apply`` maps
    it to HTTP 503 with ``details.backend_type`` + ``details.backend_url``.
    """
    connector = connectors.get(backend_type)
    if connector.preflight is None:
        return
    await connector.preflight(backend_url)


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
    dataset_root = resolve_dataset_config_dir(stores, _repo_root(), dataset_name)
    if not dataset_root.is_dir():
        raise LaunchError(f"dataset not found: {dataset_name!r} (no {dataset_root}/)")

    backend_type = _read_backend_type_from_dataset(dataset_root, dataset_name)
    await _run_preflight(backend_type, backend_url)

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


async def mint_campaign_from_draft_command(
    *,
    stores: Stores,
    draft: DraftCampaign,
    draft_registry: DraftCampaignRegistry,
    job_registry: JobRegistry,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> tuple[str, str, Job]:
    """Commit a draft to disk + mint a campaign + spawn the runner.

    Wraps :func:`mint_campaign_command` after materializing the four Origin
    files (`cache.json`, `pipeline.json`, `task_description.md`,
    `prompts/default.json`) and the sibling `campaign.json` per
    ``docs/specs/m13-chat-first-user-web.md § Commit path``.
    """
    if stores.tenant_datasets.slug_exists(draft.slug):
        raise LaunchError(
            f"slug collision at commit: {draft.slug!r} already exists in this tenant's collection"
        )

    # Deterministic origin gate BEFORE anything irreversible. A false-ready
    # never reaches mint — the checklist, not the operator, decides.
    readiness = origin_readiness(draft)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)

    # Preflight BEFORE commit_draft so a backend-down failure preserves the
    # draft — the operator can fix the backend and retry without re-uploading.
    await _run_preflight(draft.connector, backend_url)

    # Materialize raw rows → Samples now that the column mapping is confirmed,
    # overwriting the draft cache so commit_draft's rename yields a proper
    # dataset cache.json. Materialization may surface a per-row data failure
    # (e.g. a blank mapped cell) as IngestError — propagated to a 422.
    cache = stores.tenant_datasets.load_draft_cache(draft.draft_id)
    if cache is None:
        raise LaunchError(f"draft {draft.draft_id!r} has no cached rows to materialize")
    table = Table(headers=draft.headers, rows=tuple(cache.get("items", [])))
    samples = materialize_samples(
        table, query_col=draft.column_query, ground_truth_col=draft.column_ground_truth
    )
    stores.tenant_datasets.write_draft_cache(
        draft.draft_id, samples, source_file=draft.source_file, headers=draft.headers
    )

    pipeline_json = _build_origin_pipeline_json(draft)
    campaign_json = _build_default_campaign_json(draft)
    prompt_default = _build_default_prompt(draft)

    stores.tenant_datasets.commit_draft(
        draft.draft_id,
        slug=draft.slug,
        pipeline_json=pipeline_json,
        campaign_json=campaign_json,
        task_description=draft.task_description,
        prompt_default=prompt_default,
    )
    draft_registry.discard(draft.draft_id, tenant_id=stores.identity.tenant_id)

    return await mint_campaign_command(
        stores=stores,
        dataset_name=draft.slug,
        job_registry=job_registry,
        halt_at_accuracy=halt_at_accuracy,
        spend_budget_usd=spend_budget_usd,
        backend_url=backend_url,
    )


def _build_origin_pipeline_json(draft: DraftCampaign) -> dict[str, Any]:
    """Slice-1 pipeline overlay seeded from the connector's first-tenant default.

    The committed file is the dataset's ``pipeline.json`` overlay; the
    backend's live ``GET /pipeline`` response is the actual schema.
    ``backend_type`` is mandatory for connector resolution
    (``_read_backend_type`` reads it on bootstrap); ``pipelines.default``
    overrides the backend's pipeline order per the merge contract in
    ``application/bootstrap/wiring.py::_apply_dataset_overlay``.

    Per R4 the step list comes from :attr:`Connector.default_pipeline` —
    the launcher carries no hard-coded ``["llm_only"]``. Connectors that
    leave the field empty inherit the backend's own default.
    """
    pipeline: dict[str, Any] = {
        "name": draft.slug,
        "backend_type": draft.connector,
        "backend_name": draft.connector,
    }
    connector = connectors.get(draft.connector)
    if connector.default_pipeline:
        pipeline["pipelines"] = {"default": list(connector.default_pipeline)}
    if draft.pipeline_overlay:
        pipeline["nodes"] = draft.pipeline_overlay
    return pipeline


def _build_default_campaign_json(draft: DraftCampaign) -> dict[str, Any]:
    """Default-campaign sibling — valid :class:`CampaignConfig` wrapped in the
    on-disk ``campaign_config`` outer key per the repo convention
    (see ``datasets/{benchmark}/campaign.json``).

    Per R4, ``exclude_nodes`` and the ``optimization`` knob overrides come
    from the connector (:attr:`Connector.default_exclude_nodes` +
    :attr:`Connector.default_optimization`) — the launcher no longer
    hard-codes ``["llm_ranking"]`` or ``n_variants=3``. Connectors that
    leave the fields empty get the schema defaults.
    """
    optimizer_llm: dict[str, Any] = {"provider": draft.optimizer_provider}
    if draft.optimizer_model:
        optimizer_llm["model"] = draft.optimizer_model
    connector = connectors.get(draft.connector)
    optimization: dict[str, Any] = {"max_rounds": draft.max_rounds}
    optimization.update(dict(connector.default_optimization))
    return {
        "campaign_config": {
            "dataset_name": draft.slug,
            "scoring": f"{draft.scoring_composite}(predicted, ground_truth)",
            "exclude_nodes": list(connector.default_exclude_nodes),
            "optimization": optimization,
            "optimizer_llm": optimizer_llm,
        },
    }


def _build_default_prompt(draft: DraftCampaign) -> dict[str, Any]:
    """Slice-1 starter prompt — wraps the task description, no per-node tuning yet."""
    return {
        "task_description": draft.task_description,
        "instructions": draft.task_description,
    }


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

    dataset_root = resolve_dataset_config_dir(stores, _repo_root(), campaign.dataset_name)
    backend_type = _read_backend_type_from_dataset(dataset_root, campaign.dataset_name)
    await _run_preflight(backend_type, backend_url)

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

    file_config = _load_dataset_campaign_config(
        resolve_dataset_config_dir(stores, _repo_root(), dataset_name)
    )
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
        reason_upper = str(stop_reason).upper() if stop_reason else ""
        # crashed / render_error / diverged are operator-visible failures, not
        # successful completions. ``result.error.message`` is the operator-
        # facing string the runner picked at the throw site — the same string
        # written to ``dashboard.json::error.message`` via the canonical
        # ``ErrorRecord``; reading it here keeps JobRegistry and dashboard in
        # lockstep without coupling to projection state.
        failure_reasons = {"CRASHED", "RENDER_ERROR", "DIVERGED"}
        if reason_upper in failure_reasons:
            status: str = "failed"
            persisted_reason = (
                result.error.message if result.error is not None else str(stop_reason)
            )
        elif reason_upper == "INTERRUPTED":
            status = "stopped"
            persisted_reason = str(stop_reason)
        else:
            status = "completed"
            persisted_reason = str(stop_reason) if stop_reason else None  # type: ignore[assignment]
        job_registry.mark_finished(
            job_id,
            status=status,  # type: ignore[arg-type]
            stop_reason=persisted_reason,
        )
    except asyncio.CancelledError:
        job_registry.mark_finished(job_id, status="stopped", stop_reason="task_cancelled")
        raise
    except Exception as exc:
        # Anything reaching here fired BEFORE / OUTSIDE the runner's own
        # try/except (e.g. ``build_run_observers`` blew up) — no
        # ``ErrorRecord`` was emitted, so the exception's own message is
        # the most informative thing we have. Preserve ``ClassName: message``
        # shape for the audit trail; backend-unreachable cases are caught at
        # the dispatcher boundary (R2) before they get this deep.
        logger.exception("job %s failed", job_id)
        job_registry.mark_finished(
            job_id,
            status="failed",
            stop_reason=f"{type(exc).__name__}: {exc}",
        )


def _repo_root() -> Path:
    """Resolve the repo root from this module's location (parent of ``datasets/``)."""
    return Path(__file__).resolve().parents[3]


def _read_backend_type_from_dataset(dataset_root: Path, dataset_name: str) -> str:
    """Resolve ``backend_type`` from ``{dataset_root}/pipeline.json`` for the preflight.

    Raises :class:`LaunchError` when the field is missing — the launch can't
    proceed without it, and the dispatcher catches LaunchError into a 422.
    """
    raw_path = dataset_root / "pipeline.json"
    if not raw_path.is_file():
        raise LaunchError(f"dataset {dataset_name!r} has no pipeline.json — cannot resolve backend")
    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LaunchError(f"dataset {dataset_name!r} pipeline.json is malformed: {exc}") from exc
    bt = raw.get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise LaunchError(f"dataset {dataset_name!r} pipeline.json is missing 'backend_type'")
    return bt.lower()


def _claim_email(stores: Stores) -> str | None:
    """Best-effort read of the OIDC email claim off ``IdentityContext.claims``."""
    raw = stores.identity.claims.get("email")
    return raw if isinstance(raw, str) else None


def _load_dataset_campaign_config(dataset_root: Path) -> dict[str, Any]:
    """Read ``datasets/{name}/campaign.json``; unwrap the optional outer
    ``campaign_config`` key (the repo on-disk convention). Mirrors the CLI
    loader at ``presentation/cli/session.py:load_campaign_config``."""
    path = dataset_root / "campaign.json"
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    result: dict[str, Any] = data.get("campaign_config", data) or {}
    return result


__all__ = [
    "LaunchError",
    "OriginIncompleteError",
    "QuotaExceededError",
    "mint_campaign_command",
    "mint_campaign_from_draft_command",
    "start_run_command",
]
