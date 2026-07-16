"""The two check-in transitions — drop/pick → CHECKIN skeleton, Start → active+run.

Splits the old atomic ``mint_campaign_from_draft_command`` at the seam between
"establish identity + persist working state" (transition a, free, no slot) and
"irreversibly commit + run" (transition b, the gate + commit + loop). A check-in
is a real disk-backed campaign in the ``checkin`` lifecycle, so it shows in the
sidebar, follows the active pointer, and survives a restart — the working state
lives under the campaign dir (``checkin/draft.json`` + ``checkin/cache.json``)
and is rehydrated at Start.

The irreversible Start body — commit the origin, build the session, resolve the
cycle plan, flip ``checkin`` → ``active`` — is :func:`prepare_checkin_run`, shared
by BOTH run tails: the web ``start_checkin_campaign`` (reserves a machine slot then
detaches via :func:`_run_in_background`) and the CLI ``new <file>`` (runs the loop
inline with ``LiveDisplay``). The ONLY legitimate difference between the surfaces is
run-invocation; everything up to the run is one path. The commit + finalize steps
reuse the launcher core (:func:`materialize_and_write_origin`,
:func:`resolve_cycle_plan`, :func:`finalize_checkin_to_active`).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.session import (
    finalize_checkin_to_active,
    mint_checkin_skeleton,
)
from promptpotter.application.bootstrap.wiring import init_services, resolve_dataset_config_dir
from promptpotter.application.datasets.dataset_replace import recover_pending_replacements
from promptpotter.application.datasets.draft_campaign import DraftCampaign, dataset_source_of
from promptpotter.application.datasets.origin_readiness import resolution_block
from promptpotter.application.jobs.launcher.core import (
    LaunchError,
    _admit,
    _assert_origin_ready,
    _run_in_background,
    _run_preflight,
    build_cycle_config,
    materialize_and_write_origin,
    persist_origin_candidate_library,
)
from promptpotter.application.jobs.mint import resolve_cycle_plan
from promptpotter.application.jobs.quota import check_launch_quotas, effective_spend_cap_usd
from promptpotter.application.optimization.task_context import (
    checkin_call_context,
    load_or_build_task_context,
)
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.layout import REPO_ROOT
from promptpotter.shared.identity import claim_email

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.search_point import TaskDecomposition

logger = logging.getLogger(__name__)


def create_checkin_campaign(
    stores: Stores,
    *,
    draft: DraftCampaign,
    bank_items: list[dict[str, Any]],
    source_file: str = "",
    headers: tuple[str, ...] = (),
) -> tuple[str, str, DraftCampaign]:
    """Transition (a) — mint a CHECKIN campaign skeleton from the first ingest action.

    Mints campaign + provisional cycle + session + active pointer (no slot, no run),
    re-keys ``draft`` to the new ``campaign_id``, and persists the durable working
    state (``checkin/draft.json``) + the sample bank (``checkin/cache.json``). The
    campaign appears in the sidebar immediately and is resumable. Returns
    ``(campaign_id, cycle_id, keyed_draft)`` — the keyed draft (``draft_id`` now the
    ``campaign_id``) is the wire/return value the ingest handlers hand back.

    The bank lands before the draft: :meth:`write_resolution` patches ``cache.json``
    and no-ops when no bank exists, so minting through the seam in the other order
    would leave a fresh check-in with no ``resolution`` breadcrumb on disk."""
    _session_id, campaign_id, cycle_id = mint_checkin_skeleton(stores, slug=draft.slug)
    stores.checkin.write_bank(
        campaign_id, bank_items, source_file=source_file or draft.source_file, headers=headers
    )
    return campaign_id, cycle_id, save_checkin_draft(stores, draft.patch(draft_id=campaign_id))


def load_checkin_draft(stores: Stores, campaign_id: str) -> DraftCampaign | None:
    """Rehydrate the durable check-in draft for ``campaign_id``, or ``None``.

    The campaign dir is the identity, so ``draft_id`` / ``tenant_id`` are
    re-injected from the store's tenant scope — a cross-tenant id isn't found."""
    data = stores.checkin.read_draft(campaign_id)
    if data is None:
        return None
    return DraftCampaign.from_disk(data, draft_id=campaign_id, tenant_id=stores.identity.tenant_id)


def save_checkin_draft(
    stores: Stores, draft: DraftCampaign, *, resolution: dict[str, Any] | None = None
) -> DraftCampaign:
    """Persist a mutated check-in draft back to disk (``draft_id`` IS the campaign id).

    The single write-back seam every draft-mutating handler rides. It writes BOTH
    ``draft.json`` and the ``cache.json::resolution`` breadcrumb, because the latter
    is derived from the former: a caller that refreshed one and not the other left
    the operator reading stale gaps out of the file tree. Pass ``resolution`` only to
    enrich the default block (the resolver appends its raw output + degradation).
    """
    stores.checkin.write_draft(draft.draft_id, draft.to_disk())
    stores.checkin.write_resolution(draft.draft_id, resolution or resolution_block(draft))
    return draft


@dataclass(frozen=True, slots=True)
class PreparedCheckinRun:
    """The run bundle :func:`prepare_checkin_run` hands back — everything the loop
    needs, after the check-in has been committed + flipped to ``active``. Both run
    tails (web detach + CLI inline) drive the loop from this."""

    session: Session
    campaign_config: CampaignConfig
    train_data: list[Any]
    task_context: TaskDecomposition
    cycle_id: str
    session_id: str


def load_checkin_for_start(stores: Stores, campaign_id: str) -> tuple[str, DraftCampaign]:
    """Load + gate a check-in for Start — the shared front of both run tails.

    Heals pending Replaces, asserts the campaign is owned + still in ``checkin``,
    rehydrates the draft, and runs the deterministic origin-readiness gate (raises
    :class:`OriginIncompleteError` → 422 with the open gaps, leaving the campaign in
    check-in). Returns ``(cycle_id, draft)``."""
    recover_pending_replacements(stores=stores)
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise LaunchError(f"campaign not found or not owned: {campaign_id}")
    if campaign.lifecycle_status != "checkin":
        raise LaunchError(
            f"campaign {campaign_id} is not in check-in (lifecycle={campaign.lifecycle_status})"
        )
    draft = load_checkin_draft(stores, campaign_id)
    if draft is None:
        raise LaunchError(f"campaign {campaign_id} has no check-in working state to start")
    _assert_origin_ready(draft)
    return campaign.root_cycle_id, draft


async def prepare_checkin_run(
    stores: Stores,
    *,
    campaign_id: str,
    cycle_id: str,
    draft: DraftCampaign,
    make_session: Callable[[str], Awaitable[Session]],
) -> PreparedCheckinRun:
    """Commit the origin + build the session + flip ``checkin`` → ``active``.

    The single irreversible Start body shared by the web detach + CLI inline tails.
    Fresh upload → materialize the dataset now; derived-from-existing → the dataset
    already exists, persist only its candidate library through the one origin-write
    seam. ``make_session`` is the surface's session factory (web ``init_services`` /
    CLI ``init_services_cli``) taking the resolved ``dataset_name``. Preflight + the
    machine slot (web) / status check (CLI) + the run itself are the caller's — this
    seam is run-invocation-agnostic."""
    canonical = dataset_source_of(draft.source_file)
    if canonical is None:
        if stores.tenant_datasets.slug_exists(draft.slug):
            raise LaunchError(
                f"slug collision at start: {draft.slug!r} already exists in this tenant's collection"
            )
        bank = stores.checkin.load_bank(campaign_id)
        if bank is None:
            raise LaunchError(f"campaign {campaign_id} has no sample bank to materialize")
        materialize_and_write_origin(stores, draft, bank_items=list(bank.get("items", [])))
        dataset_name = draft.slug
        pipeline_overlay: dict[str, Any] = {}
        origin_override = None
    else:
        persist_origin_candidate_library(stores, canonical, draft)
        dataset_name = canonical
        pipeline_overlay = draft.pipeline_overlay
        origin_override = draft.origin_prompt_fields if draft.reused_origin_id else None

    session = await make_session(dataset_name)

    dataset_root = resolve_dataset_config_dir(stores, REPO_ROOT, dataset_name)
    campaign_config = build_cycle_config(session, dataset_root, pipeline_overlay=pipeline_overlay)

    train_data = session.samples or []
    plan = resolve_cycle_plan(session, campaign_config, train_data, origin_override=origin_override)

    index = stores.campaigns.load(campaign_id, cycle_id) or {}
    session_id = str(index.get("parent_session_id") or "")
    if not session_id:
        raise LaunchError(f"checkin cycle {cycle_id} in {campaign_id} has no parent_session_id")

    finalize_checkin_to_active(
        session,
        campaign_config,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        session_id=session_id,
        cycle_plan=plan,
        dataset_size=len(train_data),
    )
    if origin_override:
        stores.campaigns.write_cycle_seed(
            campaign_id,
            cycle_id,
            CycleSeed(origin_prompt_fields=origin_override, origin_source="campaign_origin"),
        )

    task_context = await load_or_build_task_context(
        session.dataset_config_dir,
        campaign_id=campaign_id,
        context=checkin_call_context(stores, campaign_id, cycle_id),
    )
    return PreparedCheckinRun(
        session=session,
        campaign_config=campaign_config,
        train_data=train_data,
        task_context=task_context,
        cycle_id=cycle_id,
        session_id=session_id,
    )


async def start_checkin_campaign(
    *,
    stores: Stores,
    job_registry: Any,
    campaign_id: str,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> Any:
    """Transition (b), web tail — flip a CHECKIN campaign to ``active`` and DETACH.

    Gate the origin (incomplete → 422, stays ``checkin``), reserve the machine slot,
    run the shared :func:`prepare_checkin_run`, then spawn the runner as a detached
    task. The webapp's "Start campaign" (``POST /commands/start-checkin``) calls
    this; the CLI ``new <file>`` shares :func:`prepare_checkin_run` but runs inline."""
    cycle_id, draft = load_checkin_for_start(stores, campaign_id)

    user = stores.users.get_or_create(
        user_id=str(stores.identity.user_id),
        tenant_id=str(stores.identity.tenant_id),
        email=claim_email(stores.identity),
    )
    check_launch_quotas(user=user, job_registry=job_registry, rate_limited=True)
    job = _admit(
        job_registry.reserve(
            user_id=str(stores.identity.user_id),
            dataset_name=draft.slug,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
        )
    )

    try:
        await _run_preflight(draft.connector, backend_url)
        spend_budget_usd = await asyncio.to_thread(
            effective_spend_cap_usd,
            requested_cap_usd=spend_budget_usd,
            user=user,
            stores=stores,
        )

        async def make_session(dataset_name: str) -> Session:
            return await init_services(
                backend_url=backend_url, dataset_name=dataset_name, identity=stores.identity
            )

        prepared = await prepare_checkin_run(
            stores,
            campaign_id=campaign_id,
            cycle_id=cycle_id,
            draft=draft,
            make_session=make_session,
        )
    except BaseException:
        job_registry.mark_finished(job.job_id, status="failed", stop_reason="launch_aborted")
        raise

    task = asyncio.create_task(
        _run_in_background(
            session=prepared.session,
            campaign_config=prepared.campaign_config,
            train_data=prepared.train_data,
            job_registry=job_registry,
            job_id=job.job_id,
            task_context=prepared.task_context,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
        ),
        name=f"job-{job.job_id}",
    )
    job_registry.attach_task(job.job_id, task)
    logger.info("start-checkin: started %s/%s (job %s)", campaign_id, cycle_id, job.job_id)
    return job


__all__ = [
    "PreparedCheckinRun",
    "create_checkin_campaign",
    "load_checkin_draft",
    "load_checkin_for_start",
    "prepare_checkin_run",
    "save_checkin_draft",
    "start_checkin_campaign",
]
