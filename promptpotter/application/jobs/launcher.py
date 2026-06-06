"""Background-task launcher — mints + spawns a campaign run from one command.

The ``mint-campaign`` apply path runs the one shared mint prologue
(``jobs/mint.py::prepare_fresh_cycle`` — the same seam CLI ``new``
runs inline): build session → load campaign config → ``prepare_fresh_cycle``
→ build observers → ``asyncio.create_task`` for ``run_optimization``.
The 202 returns the moment the campaign exists on disk; the run
proceeds in background tracked by :class:`JobRegistry`.

``_CYCLE_LEDGER`` ContextVar isolation (set by ``build_run_observers``,
cleared by ``drain_all``) lets multiple concurrent campaigns coexist
without leaking ledger state across asyncio tasks.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.bootstrap import init_services
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.bootstrap.wiring import resolve_dataset_config_dir
from promptpotter.application.config import (
    CampaignConfig,
    configure_and_apply_pipeline,
    create_llm_client,
    load_campaign_config,
)
from promptpotter.application.datasets import read_campaign_config_file
from promptpotter.application.datasets.csv_ingest import Table, materialize_samples
from promptpotter.application.datasets.dataset_replace import recover_pending_replacements
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    DraftCampaignRegistry,
    dataset_source_of,
)
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.jobs.mint import prepare_fresh_cycle
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    check_launch_quotas,
    effective_spend_cap_usd,
)
from promptpotter.application.jobs.registry import Job, JobRegistry, JobStatus
from promptpotter.application.optimization.task_context import load_or_build_task_context
from promptpotter.application.runner.entry import run_optimization
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.connectors.protocol import Connector
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, TaskDecomposition
from promptpotter.infrastructure.store import Stores
from promptpotter.shared.errors import PayloadInvalidError

logger = logging.getLogger(__name__)

# The one outcome → JobStatus mapping. Sole bridge from the StopReason outcome
# table to JobRegistry's lifecycle vocabulary; there is no per-reason reconciler.
_JOB_STATUS_BY_OUTCOME: dict[StopOutcome, JobStatus] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "stopped",
    StopOutcome.FAILED: "failed",
}


class LaunchError(PayloadInvalidError):
    """Raised on mint-time failures (missing dataset, malformed config, …) — 422.

    A :class:`~promptpotter.shared.errors.PayloadInvalidError`, so it maps to one
    HTTP response via the central seam; routes that add context (a suggested free
    slug) still catch it explicitly.
    """


class OriginIncompleteError(PayloadInvalidError):
    """Raised when the origin-readiness checklist still has gaps at mint time (422).

    Carries the blocking :class:`FieldGap`s on ``details.gaps`` so the API can
    surface every unresolved field (``origin_incomplete``). The draft is left
    intact — the operator resolves the gaps and retries.
    """

    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete — unresolved fields: {fields}",
            details={"gaps": [gap.to_wire() for gap in gaps]},
        )


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
    # Heal any dataset Replace interrupted mid-migration before resolving a pin —
    # a crashed version-and-repoint can leave a campaign pointing at a name whose
    # data has already moved to `-vN`. Cheap no-op when nothing's pending.
    recover_pending_replacements(stores=stores)
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

    file_config = read_campaign_config_file(dataset_root / "campaign.json")
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = load_campaign_config({**profile, **file_config})

    train_data = session.samples or []
    # The one shared mint prologue — same seam CLI ``new`` runs (inline). See
    # ``application/jobs/mint.py``; the web path keeps only the gates + detached task.
    minted = prepare_fresh_cycle(session, campaign_config, train_data)
    campaign_id, cycle_id = minted.campaign_id, minted.cycle_id

    # Run-start framing: read the committed ``task_context.json`` (written at
    # commit from the check-in's decomposition) — or decompose a benchmark's
    # ``task_description.md`` once on first sight. No second LLM call once the
    # file exists; the web mint path previously ran with EMPTY framing.
    llm_client, model = create_llm_client(campaign_config)
    task_context = await load_or_build_task_context(session.dataset_config_dir, llm_client, model)

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
            task_context=task_context,
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


async def commit_draft_to_dataset(
    *,
    stores: Stores,
    draft: DraftCampaign,
    draft_registry: DraftCampaignRegistry,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> str:
    """Gate + commit a draft to an on-disk tenant dataset; return its slug.

    Materializes the four Origin files (`cache.json`, `pipeline.json`,
    `task_description.md`, `prompts/default.json`) and the sibling
    `campaign.json` per ``docs/specs/roadmap.md § Commit path``.
    Once this returns, ``projects/{tenant}/datasets/{slug}/`` is a first-class
    dataset that ``mint_campaign_command`` (web, detached) or CLI ``new``
    (inline) can mint + run identically. Shared by both entry points — the
    commit logic lives here, not duplicated per surface.

    Fresh-upload drafts only. A draft derived from an existing dataset
    (``source_file = "dataset:{slug}"``) must mint against that canonical dataset,
    never materialize a clone — :func:`mint_campaign_from_draft_command` routes
    it past here; the guard below makes that contract crash-safe.
    """
    if dataset_source_of(draft.source_file) is not None:
        raise LaunchError(
            "commit_draft_to_dataset called on a derived-from-existing draft; "
            "mint against the canonical dataset instead"
        )

    if stores.tenant_datasets.slug_exists(draft.slug):
        raise LaunchError(
            f"slug collision at commit: {draft.slug!r} already exists in this tenant's collection"
        )

    # The closed answer space is a deterministic fact (the target column's label
    # set), not the operator's to complete by hand — fill answer_format here so an
    # otherwise-ready draft whose authored prompt under-enumerated the labels
    # isn't blocked at the gate. No-op when already canonical or open-ended.
    normalized = draft.with_closed_answer_format()
    if normalized is not draft:
        draft = draft_registry.update(normalized)

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
    prompt_default = draft.committed_prompt_fields()
    task_context = _build_task_context(draft)

    stores.tenant_datasets.commit_draft(
        draft.draft_id,
        slug=draft.slug,
        pipeline_json=pipeline_json,
        campaign_json=campaign_json,
        task_description=draft.raw_task_description,
        prompt_default=prompt_default,
        task_context=task_context,
    )
    draft_registry.discard(draft.draft_id, tenant_id=stores.identity.tenant_id)
    return draft.slug


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
    """Commit a draft to disk + mint a campaign + spawn the runner (detached).

    The web ``/commands/mint-campaign-from-draft`` entry. Two paths, forked on
    draft provenance:

    * **Derived from an existing dataset** (demo / benchmark / owned tenant
      dataset; ``source_file = "dataset:{slug}"``) — the dataset already exists on
      disk, so commit nothing. Keep the origin-readiness gate + preflight, then
      mint a campaign DIRECTLY against the canonical ``dataset_name``. No
      ``commit_draft``, no ``{slug}-N`` clone, no slug uniquify — re-running just
      adds a sibling campaign under the one dataset.
    * **Fresh CSV upload** — commit via :func:`commit_draft_to_dataset` (one
      tenant folder), then mint.

    CLI ``new <file>`` shares the commit step but runs the loop inline; CLI
    ``new <name>`` mints an existing dataset directly without ever drafting.
    """
    canonical = dataset_source_of(draft.source_file)
    if canonical is not None:
        # Existing dataset — gate, then mint against it; never materialize a clone.
        readiness = origin_readiness(draft)
        if not readiness.complete:
            raise OriginIncompleteError(readiness.gaps)
        await _run_preflight(draft.connector, backend_url)
        draft_registry.discard(draft.draft_id, tenant_id=stores.identity.tenant_id)
        return await mint_campaign_command(
            stores=stores,
            dataset_name=canonical,
            job_registry=job_registry,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
            backend_url=backend_url,
        )

    slug = await commit_draft_to_dataset(
        stores=stores,
        draft=draft,
        draft_registry=draft_registry,
        backend_url=backend_url,
    )
    return await mint_campaign_command(
        stores=stores,
        dataset_name=slug,
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

    nodes = merge_pipeline_overlay(draft, connector)
    if nodes:
        pipeline["nodes"] = nodes
    return pipeline


def merge_pipeline_overlay(draft: DraftCampaign, connector: Connector) -> dict[str, Any]:
    """Connector node-config seed (e.g. TermNorm's reasoning clamp) underneath,
    operator draft edits on top — the effective ``pipeline.json::nodes`` block.

    Sub-blocks (``config`` / ``optimizer``) shallow-merge per node so an operator
    override narrows the seed rather than replacing the whole node. Shared by the
    committed pipeline.json builder and the wire-side optimizer-locks block so
    the two never drift.
    """
    nodes: dict[str, Any] = copy.deepcopy(dict(connector.default_node_config))
    for node_name, node_overlay in (draft.pipeline_overlay or {}).items():
        dst = nodes.setdefault(node_name, {})
        for key, val in node_overlay.items():
            if isinstance(val, dict) and isinstance(dst.get(key), dict):
                dst[key].update(val)
            else:
                dst[key] = val
    return nodes


def derive_optimizer_locks(draft: DraftCampaign) -> dict[str, Any]:
    """The backend-pipeline permission surface the new-campaign UI renders.

    Makes the otherwise-hidden connector defaults visible *before* commit: the
    default pipeline, the per-node config floor + the ``param_allowed_values``
    the optimizer may permute, and the campaign-wide forbidden axes
    (``model``/``provider`` under ``forbidden_axes_strict``). A draft's
    ``pipeline_overlay`` is empty until commit, so without this the UI couldn't
    show that the optimizer is *locked out* of escalating these — not merely
    that ``low`` is a default. Mirrors the commit-time merge via
    :func:`merge_pipeline_overlay`.
    """
    connector = connectors.get(draft.connector)
    forbidden_strict = draft.lock_model
    node_locks: dict[str, Any] = {}
    for node_name, overlay in merge_pipeline_overlay(draft, connector).items():
        optimizer = overlay.get("optimizer", {})
        node_locks[node_name] = {
            "config": dict(overlay.get("config", {})),
            "param_allowed_values": dict(optimizer.get("param_allowed_values", {})),
        }
    return {
        "pipeline": list(connector.default_pipeline),
        "forbidden_axes": sorted(PARAM_FORBIDDEN_KEYS) if forbidden_strict else [],
        "nodes": node_locks,
    }


def draft_wire_with_locks(draft: DraftCampaign) -> dict[str, Any]:
    """``DraftCampaign.to_wire()`` plus the connector-derived ``optimizer_locks``.

    The single wire shape every draft-returning endpoint emits — keeps
    :meth:`DraftCampaign.to_wire` pure (no connector import) and adds the
    permission block once at the I/O boundary.
    """
    return {**draft.to_wire(), "optimizer_locks": derive_optimizer_locks(draft)}


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
    # The operator's model-lock choice overrides the connector default —
    # mirrors derive_optimizer_locks so the committed campaign matches the panel.
    optimization["forbidden_axes_strict"] = draft.lock_model
    return {
        "campaign_config": {
            "dataset_name": draft.slug,
            "scoring": f"{draft.scoring_composite}(predicted, ground_truth)",
            "exclude_nodes": list(connector.default_exclude_nodes),
            "optimization": optimization,
            "optimizer_llm": optimizer_llm,
        },
    }


def _build_task_context(draft: DraftCampaign) -> dict[str, Any]:
    """The run-start domain framing, written to ``task_context.json``.

    The check-in already decomposed the task into the 7-field ``task_context``
    (carried on :attr:`DraftCampaign.decomposed_task_context`); normalize it through
    :class:`TaskDecomposition` with the verbatim ``raw_description`` so the run
    reads it directly instead of re-decomposing via a second LLM call."""
    return TaskDecomposition.from_dict(
        {**draft.decomposed_task_context, "raw_description": draft.raw_task_description}
    ).to_dict()


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

    # Same recovery guard as the mint path — a resumed cycle must not resolve a
    # pin a crashed Replace left dangling (see ``recover_pending_replacements``).
    recover_pending_replacements(stores=stores)
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

    file_config = read_campaign_config_file(
        resolve_dataset_config_dir(stores, _repo_root(), dataset_name) / "campaign.json"
    )
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = load_campaign_config({**profile, **file_config})

    train_data = session.samples or []
    configure_and_apply_pipeline(session, campaign_config, log=lambda *_a, **_k: None)
    llm_client, model = create_llm_client(campaign_config)
    task_context = await load_or_build_task_context(session.dataset_config_dir, llm_client, model)
    # Bind the session to the EXISTING campaign/cycle before launch. Without this
    # `_ensure_session_minted` (guards on an empty session_id) would mint a fresh
    # random campaign + root cycle and steal the active pointer — stranding an
    # operator-steered fork in its real campaign. The campaign was already loaded
    # above, so auto-mint must never fire from this path. Mirrors CLI `cmd_resume`.
    session.campaign_id = campaign_id
    session.state.cycle_id = cycle_id
    index = stores.campaigns.load(campaign_id, cycle_id) or {}
    session_id = str(index.get("parent_session_id") or "")
    if not session_id:
        raise LaunchError(f"cycle {cycle_id} in {campaign_id} has no parent_session_id")
    session.session_id = session_id

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
            task_context=task_context,
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
    task_context: TaskDecomposition,
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
            task_context=task_context,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
        )
        stop_reason = getattr(result, "stop_reason", None)
        # Job terminal status derives from the single StopReason outcome table —
        # the SAME classification index.json / dashboard.json / the webapp read.
        # No private reconciler: a cycle can no longer read "failed" here and
        # "completed" there (the optimizer_timeout split is gone). For a FAILED
        # outcome, ``result.error.message`` is the operator-facing string the
        # runner picked at the throw site (same as dashboard.json::error.message).
        outcome = stop_reason_outcome(stop_reason) if stop_reason else StopOutcome.SUCCESS
        status: JobStatus = _JOB_STATUS_BY_OUTCOME[outcome]
        if outcome is StopOutcome.FAILED and result.error is not None:
            persisted_reason: str | None = result.error.message
        else:
            persisted_reason = str(stop_reason) if stop_reason else None
        job_registry.mark_finished(
            job_id,
            status=status,
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


__all__ = [
    "LaunchError",
    "OriginIncompleteError",
    "QuotaExceededError",
    "commit_draft_to_dataset",
    "mint_campaign_command",
    "mint_campaign_from_draft_command",
    "start_run_command",
]
