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
from promptpotter.application.jobs.launcher.draft_build import (
    _build_default_campaign_json,
    _build_origin_pipeline_json,
    _build_task_context,
    split_overlay,
)
from promptpotter.application.jobs.mint import prepare_fresh_cycle
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    check_launch_quotas,
    effective_spend_cap_usd,
)
from promptpotter.application.jobs.registry import Job, JobRegistry, JobStatus, ReserveResult
from promptpotter.application.optimization.task_context import load_or_build_task_context
from promptpotter.application.runner.entry import run_optimization
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.paths import REPO_ROOT
from promptpotter.shared.errors import MachineBusyError, PayloadInvalidError

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


def _admit(reservation: ReserveResult) -> Job:
    """Unwrap an admission, or raise 409 ``machine_busy`` from the slot holder.

    The single map from :class:`ReserveResult` to the launch surface — both
    launchers gate through it, so the busy 409 reads identically whichever
    command was attempted.
    """
    if reservation.job is not None:
        return reservation.job
    holder = reservation.holder
    assert holder is not None  # reserve() sets exactly one side
    raise MachineBusyError(
        holder_user=holder.user_id,
        campaign_id=holder.campaign_id,
        cycle_id=holder.cycle_id,
        started_at=holder.started_at,
    )


async def mint_campaign_command(
    *,
    stores: Stores,
    dataset_name: str,
    job_registry: JobRegistry,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    origin_override: dict[str, Any] | None = None,
    pipeline_overlay: dict[str, Any] | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> tuple[str, str, Job]:
    """Mint a fresh campaign + cycle, then spawn the runner in the background.

    Returns ``(campaign_id, cycle_id, job)``. The asyncio task is detached;
    the caller's 202 response goes out the moment this returns. Background
    progress shows up via the canonical ledger + `dashboard.json` stream.

    ``origin_override`` (campaign-from-origin) seeds C0 from a chosen prior
    origin's prompt fields instead of the dataset's authored origin.

    ``pipeline_overlay`` carries a reused-dataset draft's operator setup edits
    (lock/allow + origin-floor values). It is split (``split_overlay``) onto the
    per-campaign config snapshot — narrowing the dataset's declared search space
    + overriding origin-floor values for THIS campaign only, leaving the shared
    dataset immutable. A fresh upload commits its edits into its own
    ``pipeline.json`` instead, so this stays ``None`` for that path.
    """
    # Heal any dataset Replace interrupted mid-migration before resolving a pin —
    # a crashed version-and-repoint can leave a campaign pointing at a name whose
    # data has already moved to `-vN`. Cheap no-op when nothing's pending.
    recover_pending_replacements(stores=stores)
    dataset_root = resolve_dataset_config_dir(stores, REPO_ROOT, dataset_name)
    if not dataset_root.is_dir():
        raise LaunchError(f"dataset not found: {dataset_name!r} (no {dataset_root}/)")

    backend_type = _read_backend_type_from_dataset(dataset_root, dataset_name)

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=_claim_email(stores),
    )
    # Per-user gates first (count the caller's PRIOR runs), then the atomic
    # global slot. Reserve writes a pending reservation before any ``await``, so
    # a second near-simultaneous launch sees it and is rejected — the launch
    # race is closed. The reservation's real ids land once the mint resolves.
    check_launch_quotas(user=user, job_registry=job_registry, rate_limited=True)
    job = _admit(
        job_registry.reserve(user_id=str(stores.identity.user_id), dataset_name=dataset_name)
    )

    # Everything below holds a slot — release it on any pre-launch failure so a
    # crashed preflight/init never wedges the machine at capacity.
    try:
        await _run_preflight(backend_type, backend_url)
        spend_budget_usd = effective_spend_cap_usd(
            requested_cap_usd=spend_budget_usd,
            user=user,
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
        # Reused-dataset setup edits land on the per-campaign snapshot (not the
        # shared dataset): config blocks → pipeline_overrides, optimizer blocks →
        # optimizer_narrowing, merged over the dataset's defaults (operator wins
        # per node). prepare_fresh_cycle freezes this into the Campaign manifest.
        if pipeline_overlay:
            overrides, narrowing = split_overlay(pipeline_overlay)
            campaign_config = campaign_config.model_copy(
                update={
                    "pipeline_overrides": {**campaign_config.pipeline_overrides, **overrides},
                    "optimizer_narrowing": {**campaign_config.optimizer_narrowing, **narrowing},
                }
            )

        train_data = session.samples or []
        # The one shared mint prologue — same seam CLI ``new`` runs (inline). See
        # ``application/jobs/mint.py``; the web path keeps only the gates + detached task.
        minted = prepare_fresh_cycle(
            session, campaign_config, train_data, origin_override=origin_override
        )
        campaign_id, cycle_id = minted.campaign_id, minted.cycle_id
        job_registry.update_target(job.job_id, campaign_id=campaign_id, cycle_id=cycle_id)

        # Run-start framing: read the committed ``task_context.json`` (written at
        # commit from the check-in's decomposition) — or decompose a benchmark's
        # ``task_description.md`` once on first sight. No second LLM call once the
        # file exists; the web mint path previously ran with EMPTY framing.
        task_context = await load_or_build_task_context(session.dataset_config_dir)
    except BaseException:
        job_registry.mark_finished(job.job_id, status="failed", stop_reason="launch_aborted")
        raise

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
    persist_origin_candidate_library(stores, draft.slug, draft)
    draft_registry.discard(draft.draft_id, tenant_id=stores.identity.tenant_id)
    return draft.slug


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftCampaign) -> None:
    """Write the draft's candidate library into the dataset origin — the ONE
    origin-write seam for the library, called on every mint route.

    The library is part of the origin spec, so it persists whenever a draft
    establishes/re-establishes an origin: a fresh-upload commit and a
    reused-dataset mint (which skips ``commit_draft``) both land here. Scoped to
    tenant datasets — a reopened repo benchmark isn't ours to mutate, and on
    reopen the committed library already round-tripped through the draft, so
    re-writing it would be a no-op anyway. A draft with no library is a no-op
    (nothing was dropped)."""
    if draft.candidate_library and stores.tenant_datasets.slug_exists(slug):
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)


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
        # The derived path skips commit_draft_to_dataset (the dataset already
        # exists), so persist a built/dropped candidate library through the SAME
        # origin-write seam — otherwise a library the operator supplied on reopen
        # would be lost. The run reads it from the resolved tenant-first config dir.
        persist_origin_candidate_library(stores, canonical, draft)
        # Discard ONLY after a successful mint — a failed mint (e.g. 409
        # machine_busy) must leave the draft intact so the operator can retry
        # without re-opening the origin (otherwise the retry 404s on a gone draft).
        result = await mint_campaign_command(
            stores=stores,
            dataset_name=canonical,
            job_registry=job_registry,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
            # Persist the reused-dataset draft's lock/allow + origin-floor edits on
            # the per-campaign snapshot (the shared dataset stays immutable).
            pipeline_overlay=draft.pipeline_overlay,
            backend_url=backend_url,
        )
        draft_registry.discard(draft.draft_id, tenant_id=stores.identity.tenant_id)
        return result

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

    dataset_root = resolve_dataset_config_dir(stores, REPO_ROOT, campaign.dataset_name)
    backend_type = _read_backend_type_from_dataset(dataset_root, campaign.dataset_name)
    dataset_name = campaign.dataset_name

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=_claim_email(stores),
    )
    # Per-user gates first, then the atomic global slot (ids are known up front,
    # so the reservation carries them directly). See ``mint_campaign_command``.
    check_launch_quotas(user=user, job_registry=job_registry, rate_limited=False)
    job = _admit(
        job_registry.reserve(
            user_id=str(stores.identity.user_id),
            dataset_name=dataset_name,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
        )
    )

    try:
        await _run_preflight(backend_type, backend_url)
        spend_budget_usd = effective_spend_cap_usd(
            requested_cap_usd=spend_budget_usd,
            user=user,
            stores=stores,
        )

        session = await init_services(
            backend_url=backend_url,
            dataset_name=dataset_name,
            identity=stores.identity,
        )

        file_config = read_campaign_config_file(
            resolve_dataset_config_dir(stores, REPO_ROOT, dataset_name) / "campaign.json"
        )
        profile = session.store.backends.load_connector_profile(session.backend_id) or {}
        campaign_config = load_campaign_config({**profile, **file_config})

        train_data = session.samples or []
        configure_and_apply_pipeline(session, campaign_config, log=lambda *_a, **_k: None)
        task_context = await load_or_build_task_context(session.dataset_config_dir)
        # Bind the session to the EXISTING campaign/cycle before launch. Without
        # this `_ensure_session_minted` (guards on an empty session_id) would mint
        # a fresh random campaign + root cycle and steal the active pointer —
        # stranding an operator-steered fork in its real campaign. The campaign was
        # already loaded above, so auto-mint must never fire from this path.
        # Mirrors CLI `cmd_resume`.
        session.campaign_id = campaign_id
        session.state.cycle_id = cycle_id
        index = stores.campaigns.load(campaign_id, cycle_id) or {}
        session_id = str(index.get("parent_session_id") or "")
        if not session_id:
            raise LaunchError(f"cycle {cycle_id} in {campaign_id} has no parent_session_id")
        session.session_id = session_id
    except BaseException:
        job_registry.mark_finished(job.job_id, status="failed", stop_reason="launch_aborted")
        raise

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
        stop_reason = result.stop_reason
        # Job terminal status derives from the single StopReason outcome table —
        # the SAME classification index.json / dashboard.json / the webapp read.
        # No private reconciler: a cycle can no longer read "failed" here and
        # "completed" there (the optimizer_timeout split is gone). For a FAILED
        # outcome, ``result.error.message`` is the operator-facing string the
        # runner picked at the throw site (same as dashboard.json::error.message).
        outcome = stop_reason_outcome(stop_reason)
        status: JobStatus = _JOB_STATUS_BY_OUTCOME[outcome]
        if outcome is StopOutcome.FAILED and result.error is not None:
            persisted_reason: str | None = result.error.message
        else:
            persisted_reason = stop_reason
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
    "persist_origin_candidate_library",
    "start_run_command",
]
