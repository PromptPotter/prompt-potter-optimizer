"""Background-task launcher — mints + spawns a campaign run; the 202 returns the moment the
campaign exists on disk. The ``_CYCLE_LEDGER`` ContextVar keeps concurrent campaigns apart."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.config import (
    CampaignConfig,
    apply_inherited_overlay,
    configure_and_apply_pipeline,
    load_campaign_config,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    read_campaign_config_file,
)
from promptpotter.application.datasets.csv_ingest import Table, materialize_samples
from promptpotter.application.datasets.dataset_replace import recover_pending_replacements
from promptpotter.application.datasets.draft_campaign import DraftCampaign
from promptpotter.application.datasets.origin_readiness import FieldGap, origin_readiness
from promptpotter.application.initialization.session import Session
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.draft_build import (
    _build_default_campaign_json,
    _build_origin_pipeline_json,
    _build_task_context,
    split_overlay,
)
from promptpotter.application.jobs.mint import fresh_campaign_id, prepare_fresh_cycle
from promptpotter.application.jobs.quota import (
    QuotaExceededError,
    check_launch_quotas,
    effective_spend_cap_usd,
)
from promptpotter.application.jobs.registry import Job, JobRegistry, JobStatus, ReserveResult
from promptpotter.application.optimization.task_context import (
    checkin_call_context,
    load_or_build_task_context,
)
from promptpotter.application.runner.entry import RunMode, run_optimization
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import MachineBusyError, PayloadInvalidError
from promptpotter.shared.identity import claim_email

logger = logging.getLogger(__name__)


def launch_interrupted(exc: BaseException) -> bool:
    """True when the launch stopped because someone ASKED — the pause flag's synthetic
    ``KeyboardInterrupt``, a Ctrl+C's ``CancelledError``, a host cancel. None is visible to ``except Exception``."""
    return isinstance(exc, KeyboardInterrupt | asyncio.CancelledError)


def _record_launch_stop(
    *,
    stores: Stores,
    hop: CycleHop,
    session_id: str,
    exc: BaseException,
) -> None:
    """Stamp a launch that ended before its projection pipeline bound. A crash gets ``finished_at``,
    an interrupt gets the paused declaration and none. Best-effort — must never mask *exc*."""
    from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView

    interrupted = launch_interrupted(exc)
    try:
        cycle_dir = CycleDir(stores.campaigns.cycle_dir(hop))
        LiveDashboardView.write_launch_stop(
            cycle_dir,
            hop=hop,
            session_id=session_id,
            exc=exc,
            interrupted=interrupted,
        )
        if not interrupted:
            stores.campaigns.mark_finished(
                hop,
                status="failed",
                stop_reason=f"{type(exc).__name__}: {exc}",
                finished_at=utcnow_iso(),
                crash_traceback=traceback.format_exc(),
            )
    except Exception:
        logger.exception("failed to record launch stop for %s/%s", hop.campaign_id, hop.cycle_id)


# The one outcome → JobStatus mapping. Sole bridge from the StopReason outcome
# table to JobRegistry's lifecycle vocabulary; there is no per-reason reconciler.
_JOB_STATUS_BY_OUTCOME: dict[StopOutcome, JobStatus] = {
    StopOutcome.SUCCESS: "completed",
    StopOutcome.HALTED: "stopped",
    StopOutcome.FAILED: "failed",
    # A pause exits the worker but the cycle stays resumable — the job's process
    # is "stopped" (a fresh start-run mints a new job to continue).
    StopOutcome.PAUSED: "stopped",
}


class LaunchError(PayloadInvalidError):
    """Mint-time failure (missing dataset, malformed config) — a ``PayloadInvalidError``, so the
    central seam maps it to 422 and only routes ADDING context catch it explicitly."""


class OriginIncompleteError(PayloadInvalidError):
    """The origin-readiness checklist still has gaps at mint time (422). Carries the blocking
    :class:`FieldGap`s on ``details.gaps``; the draft is left intact for the operator to resolve."""

    code = "origin_incomplete"

    def __init__(self, gaps: tuple[FieldGap, ...]) -> None:
        self.gaps = gaps
        fields = ", ".join(gap.field for gap in gaps) or "<none>"
        super().__init__(
            f"origin incomplete — unresolved fields: {fields}",
            details={"gaps": [gap.to_wire() for gap in gaps]},
        )


def _assert_origin_ready(draft: DraftCampaign) -> None:
    """The one gate both mint paths run BEFORE anything irreversible — the checklist, not the
    operator, decides, so a false-ready never reaches mint."""
    readiness = origin_readiness(draft)
    if not readiness.complete:
        raise OriginIncompleteError(readiness.gaps)


async def _run_preflight(backend_type: str, backend_url: str) -> None:
    """Resolve the connector and run its reachability probe. A connector opts out by leaving
    ``Connector.preflight = None``; a raised ``BackendUnreachableError`` becomes a 503."""
    connector = connectors.get(backend_type)
    if connector.preflight is None:
        return
    await connector.preflight(backend_url)


def _admit(reservation: ReserveResult) -> Job:
    """Unwrap an admission, or raise 409 ``machine_busy`` from the slot holder. Both launchers gate
    through it, so the busy 409 reads identically whichever command was attempted."""
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


def build_cycle_config(
    session: Session,
    dataset_root: Path,
    *,
    pipeline_overlay: dict[str, Any] | None = None,
) -> CampaignConfig:
    """Load the campaign config for a launch, folding a reused-dataset overlay onto a per-campaign
    SNAPSHOT — one definition for all three launch paths, leaving the shared dataset immutable."""
    file_config = read_campaign_config_file(dataset_campaign_path(dataset_root))
    profile = session.store.backends.load_connector_profile(session.backend_id) or {}
    campaign_config = load_campaign_config({**profile, **file_config})
    if pipeline_overlay:
        overrides, narrowing = split_overlay(pipeline_overlay)
        campaign_config = campaign_config.model_copy(
            update={
                "pipeline_overrides": {**campaign_config.pipeline_overrides, **overrides},
                "optimizer_narrowing": {**campaign_config.optimizer_narrowing, **narrowing},
            }
        )
    return campaign_config


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
    """Mint a fresh campaign + cycle, then spawn the runner detached; the caller's 202 goes out the
    moment this returns. ``pipeline_overlay`` is ``None`` for a fresh upload, which commits its own."""
    # Heal any dataset Replace interrupted mid-migration before resolving a pin —
    # a crashed version-and-repoint can leave a campaign pointing at a name whose
    # data has already moved to `-vN`. Cheap no-op when nothing's pending.
    recover_pending_replacements(stores=stores)
    try:
        dataset_root = readable_dataset_dir(stores, dataset_name)
    except DatasetAccessError:
        raise LaunchError(f"dataset not found: {dataset_name!r}") from None

    backend_type = _read_backend_type_from_dataset(dataset_root, dataset_name)

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
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
    # crashed preflight/init never wedges the machine at capacity. `campaign_id` /
    # `cycle_id` bind only once the mint resolves; init them so the failure handler
    # can tell "crashed before a cycle existed" (nothing to mark) from "crashed
    # after mint" (mark the cycle terminal).
    campaign_id = cycle_id = ""
    try:
        # Pre-202 phase timing — the synchronous init is what the operator
        # waits on (round-0 scoring is already backgrounded). One INFO line per
        # phase so the dominant cost is visible on disk, not guessed at.
        _t0 = time.perf_counter()
        await _run_preflight(backend_type, backend_url)
        _t_preflight = time.perf_counter()
        # Daily-cap composition globs + reads every cycle ledger — offload so the
        # scan never blocks the single event loop on the launch path.
        spend_budget_usd = await asyncio.to_thread(
            effective_spend_cap_usd,
            requested_cap_usd=spend_budget_usd,
            user=user,
            stores=stores,
        )
        _t_spendcap = time.perf_counter()

        session = await init_services(
            backend_url=backend_url,
            dataset_name=dataset_name,
            identity=stores.identity,
        )
        _t_init = time.perf_counter()
        logger.info(
            "mint-timing[%s]: preflight=%.2fs spend_cap=%.2fs init_services=%.2fs",
            dataset_name,
            _t_preflight - _t0,
            _t_spendcap - _t_preflight,
            _t_init - _t_spendcap,
        )

        # Reused-dataset setup edits ride the overlay onto a per-campaign snapshot;
        # prepare_fresh_cycle freezes the result into the Campaign manifest.
        campaign_config = build_cycle_config(
            session, dataset_root, pipeline_overlay=pipeline_overlay
        )

        train_data = session.samples
        # The one shared mint prologue — same seam CLI ``new`` runs (inline). See
        # ``application/jobs/mint.py``; the web path keeps only the gates + detached task.
        minted = prepare_fresh_cycle(
            session,
            campaign_config,
            train_data,
            campaign_id=fresh_campaign_id(session, campaign_config),
            origin_override=origin_override,
        )
        campaign_id, cycle_id = minted.campaign_id, minted.cycle_id

        # Run-start framing: read the committed ``task_context.yaml`` (written at
        # commit from the check-in's decomposition) — or decompose a benchmark's
        # ``task_description.md`` once on first sight. No second LLM call once the
        # file exists, and never mint with empty framing.
        _t_framing0 = time.perf_counter()
        task_context = await load_or_build_task_context(
            stores,
            session.dataset_name,
            campaign_id=campaign_id,
            context=checkin_call_context(
                stores, CycleHop(campaign_id=campaign_id, cycle_id=cycle_id)
            ),
        )
        logger.info(
            "mint-timing[%s]: task_context=%.2fs (total pre-202=%.2fs)",
            dataset_name,
            time.perf_counter() - _t_framing0,
            time.perf_counter() - _t0,
        )
    except BaseException as exc:
        stopped = launch_interrupted(exc)
        job_registry.mark_finished(
            job.job_id,
            status="stopped" if stopped else "failed",
            stop_reason="launch_interrupted" if stopped else "launch_aborted",
        )
        if campaign_id and cycle_id:
            _record_launch_stop(
                stores=stores,
                hop=CycleHop(campaign_id=campaign_id, cycle_id=cycle_id),
                session_id="",
                exc=exc,
            )
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


def materialize_and_write_origin(
    stores: Stores, draft: DraftCampaign, *, bank_items: list[dict[str, Any]]
) -> None:
    """Materialize raw bank rows → Samples and write the committed dataset Origin files — the one
    commit body at check-in Start, shared by the CLI inline path and the web detach path."""
    table = Table(headers=draft.headers, rows=tuple(bank_items))
    samples = materialize_samples(
        table, query_col=draft.column_query, ground_truth_col=draft.column_ground_truth
    )
    stores.tenant_datasets.write_committed_dataset(
        draft.slug,
        samples=samples,
        source_file=draft.source_file,
        headers=draft.headers,
        pipeline_json=_build_origin_pipeline_json(draft),
        campaign_json=_build_default_campaign_json(draft),
        task_description=draft.raw_task_description,
        prompt_default=draft.committed_prompt_fields(),
        task_context=_build_task_context(draft),
    )
    persist_origin_candidate_library(stores, draft.slug, draft)


def persist_origin_candidate_library(stores: Stores, slug: str, draft: DraftCampaign) -> None:
    """Scoped to tenant datasets: a reopened repo benchmark is not ours to mutate, and its committed
    library already round-tripped through the draft. A draft with no library is a no-op."""
    if draft.candidate_library and stores.tenant_datasets.slug_exists(slug):
        stores.tenant_datasets.write_candidate_library(slug, draft.candidate_library)


async def start_run_command(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    hop: CycleHop,
    kind: str,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    stop_after_rounds: int | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> Job:
    """Spawn a runner against an existing cycle; ``kind`` ∈ ``{"new", "resume"}`` mirrors the two CLI
    verbs. ``stop_after_rounds`` bounds the run in place (``step-round``)."""
    if kind not in ("new", "resume"):
        raise LaunchError(f"start-run kind must be 'new' or 'resume', got {kind!r}")

    # Same recovery guard as the mint path — a resumed cycle must not resolve a
    # pin a crashed Replace left dangling (see ``recover_pending_replacements``).
    recover_pending_replacements(stores=stores)
    campaign = stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise LaunchError(f"campaign not found or not owned: {hop.campaign_id}")

    try:
        dataset_root = readable_dataset_dir(stores, campaign.dataset_name)
    except DatasetAccessError:
        raise LaunchError(f"dataset not found: {campaign.dataset_name!r}") from None
    backend_type = _read_backend_type_from_dataset(dataset_root, campaign.dataset_name)
    dataset_name = campaign.dataset_name

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )
    # Per-user gates first, then the atomic global slot (ids are known up front,
    # so the reservation carries them directly). See ``mint_campaign_command``.
    check_launch_quotas(user=user, job_registry=job_registry, rate_limited=False)
    job = _admit(
        job_registry.reserve(
            user_id=str(stores.identity.user_id),
            dataset_name=dataset_name,
            hop=hop,
        )
    )

    try:
        # Pre-202 phase timing — same instrumentation as ``mint_campaign_command``;
        # this seam shares the preflight + spend-cap + init prologue.
        _t0 = time.perf_counter()
        await _run_preflight(backend_type, backend_url)
        _t_preflight = time.perf_counter()
        # Daily-cap composition globs + reads every cycle ledger — offload so the
        # scan never blocks the single event loop on the launch path.
        spend_budget_usd = await asyncio.to_thread(
            effective_spend_cap_usd,
            requested_cap_usd=spend_budget_usd,
            user=user,
            stores=stores,
        )
        _t_spendcap = time.perf_counter()

        session = await init_services(
            backend_url=backend_url,
            dataset_name=dataset_name,
            identity=stores.identity,
        )
        _t_init = time.perf_counter()
        logger.info(
            "start-timing[%s]: preflight=%.2fs spend_cap=%.2fs init_services=%.2fs",
            dataset_name,
            _t_preflight - _t0,
            _t_spendcap - _t_preflight,
            _t_init - _t_spendcap,
        )

        # Resume/fork rebuild config from the LIVE dataset file (so declaration
        # edits stay drift-detected), then re-apply the per-campaign overlay the
        # dataset file never holds — origin-floor values + param locks — from the
        # frozen `Campaign.config` snapshot. A steered-fork seed's lock edits
        # override per node. Without this, locks silently reopen on every resume.
        campaign_config = apply_inherited_overlay(
            build_cycle_config(session, dataset_root),
            campaign.config,
            stores.campaigns.read_cycle_seed(hop),
        )

        train_data = session.samples
        configure_and_apply_pipeline(session, campaign_config, log=lambda *_a, **_k: None)
        _t_framing0 = time.perf_counter()
        task_context = await load_or_build_task_context(
            stores,
            session.dataset_name,
            campaign_id=hop.campaign_id,
            context=checkin_call_context(stores, hop),
        )
        logger.info(
            "start-timing[%s]: task_context=%.2fs (total pre-202=%.2fs)",
            dataset_name,
            time.perf_counter() - _t_framing0,
            time.perf_counter() - _t0,
        )
        # Bind the session to the EXISTING campaign/cycle before launch. Without
        # this `_ensure_session_minted` (guards on an empty session_id) would mint
        # a fresh random campaign + root cycle and steal the active pointer —
        # stranding an operator-steered fork in its real campaign. The campaign was
        # already loaded above, so auto-mint must never fire from this path.
        # Mirrors CLI `cmd_resume`.
        session.campaign_id = hop.campaign_id
        session.state.cycle_id = hop.cycle_id
        index = stores.campaigns.load(hop) or {}
        session_id = str(index.get("parent_session_id") or "")
        if not session_id:
            raise LaunchError(f"cycle {hop.cycle_id} in {hop.campaign_id} has no parent_session_id")
        session.session_id = session_id
    except BaseException as exc:
        stopped = launch_interrupted(exc)
        job_registry.mark_finished(
            job.job_id,
            status="stopped" if stopped else "failed",
            stop_reason="launch_interrupted" if stopped else "launch_aborted",
        )
        _record_launch_stop(
            stores=stores,
            hop=hop,
            session_id="",
            exc=exc,
        )
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
            stop_after_rounds=stop_after_rounds,
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
    stop_after_rounds: int | None = None,
) -> None:
    from promptpotter.application.run_observers import build_run_observers
    from promptpotter.infrastructure.llm.telemetry import set_cycle_ledger

    # asyncio.create_task copies the CURRENT context, where the dispatcher has the
    # command ledger bound to _CYCLE_LEDGER. Clear it so any emit before
    # build_run_observers binds the real cycle ledger no-ops instead of misfiling
    # onto the command/workspace ledger. No reset — this task's context is its own
    # and dies with it.
    set_cycle_ledger(None)

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
            task_context=task_context,
            mode=RunMode(halt_at_accuracy=halt_at_accuracy, stop_after_rounds=stop_after_rounds),
            spend_budget_usd=spend_budget_usd,
        )
        stop_reason = result.stop_reason
        # Job terminal status derives from the single StopReason outcome table — the SAME
        # classification index.json / dashboard.json / the webapp read. No private
        # reconciler, or a cycle reads "failed" here and "completed" there. For a FAILED
        # outcome ``result.error.message`` is the runner's throw-site string.
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
    except (asyncio.CancelledError, KeyboardInterrupt):
        # Both reach here: the runner re-raises a cancellation past its own finalize, and the
        # pause flag's synthetic KeyboardInterrupt escapes round-0 origin scoring, which runs
        # inside `_prepare_run` — outside the round loop's own arm.
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
        # Reaching here means the runner never finalized this cycle (the failure
        # fired before / outside its own try/except — e.g. build_run_observers),
        # so nothing wrote the cycle terminal. Stamp it so the fork doesn't sit
        # frozen at `init` in the file tree and the webapp.
        _record_launch_stop(
            stores=session.store,
            hop=session.hop,
            session_id=session.session_id,
            exc=exc,
        )


def _read_backend_type_from_dataset(dataset_root: Path, dataset_name: str) -> str:
    """Resolve ``backend_type`` from the dataset's ``pipeline.yaml`` for the preflight. Raises
    :class:`LaunchError` when absent — the launch cannot proceed without it."""
    raw_path = dataset_pipeline_path(dataset_root)
    try:
        raw = read_yaml_optional(raw_path)
    except json.JSONDecodeError as exc:
        raise LaunchError(f"dataset {dataset_name!r} pipeline.yaml is malformed: {exc}") from exc
    if raw is None:
        raise LaunchError(f"dataset {dataset_name!r} has no pipeline.yaml — cannot resolve backend")
    bt = raw.get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise LaunchError(f"dataset {dataset_name!r} pipeline.yaml is missing 'backend_type'")
    return bt.lower()


__all__ = [
    "LaunchError",
    "OriginIncompleteError",
    "QuotaExceededError",
    "build_cycle_config",
    "materialize_and_write_origin",
    "mint_campaign_command",
    "persist_origin_candidate_library",
    "start_run_command",
]
