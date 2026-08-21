"""The two check-in transitions: drop/pick mints a real disk-backed CHECKIN campaign (sidebar,
active pointer, restart-survivable); Start commits it. The tails differ ONLY in run-invocation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.datasets.dataset_replace import recover_pending_replacements
from promptpotter.application.datasets.draft_campaign import DraftCampaign, dataset_source_of
from promptpotter.application.datasets.origin_readiness import resolution_block
from promptpotter.application.initialization.session import (
    finalize_checkin_to_active,
    mint_checkin_skeleton,
)
from promptpotter.application.initialization.wiring import init_services
from promptpotter.application.jobs.launcher.mint_and_start import (
    LaunchError,
    _admit,
    _assert_origin_ready,
    _record_launch_stop,
    _release_slot,
    _run_in_background,
    _run_preflight,
    build_cycle_config,
    materialize_and_write_origin,
    persist_origin_candidate_library,
)
from promptpotter.application.jobs.mint import resolve_cycle_plan
from promptpotter.application.jobs.quota import admit_launch, check_launch_quotas
from promptpotter.config.settings import DEFAULT_BACKEND_URL
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.run_records import CycleSeed
from promptpotter.infrastructure.runtime_flags import is_checkin
from promptpotter.infrastructure.store.dataset_access import readable_dataset_dir
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.identity import (
    CAMPAIGN_CREATE_CAP,
    claim_email,
    require_capability,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.jobs.registry import JobRegistry

logger = logging.getLogger(__name__)


def create_checkin_campaign(
    stores: Stores,
    *,
    draft: DraftCampaign,
    bank_items: list[dict[str, Any]],
    source_file: str = "",
    headers: tuple[str, ...] = (),
) -> tuple[str, str, DraftCampaign]:
    """Transition (a) — mint the CHECKIN skeleton, re-key ``draft`` to the new ``campaign_id``, persist
    it. The bank lands FIRST: ``write_resolution`` patches ``cache.json`` and no-ops without one.

    **The capability gate is HERE, at the mint, not on the routes above it.** A check-in campaign is
    durable disk the moment it exists, and four ingresses reach this one function — `/datasets/ingest`,
    `/datasets/{name}/draft`, `/origins/{id}/draft` and CLI `new <file>`. Gating each of them spells
    the same rule four times and leaves the fifth unguarded; `mint-campaign` already answers to
    `campaign.create` at the dispatcher, so a second door onto the same act answers to it too."""
    require_capability(stores.identity, CAMPAIGN_CREATE_CAP, subject="check-in mint")
    _session_id, campaign_id, cycle_id = mint_checkin_skeleton(stores, slug=draft.slug)
    stores.checkin.write_bank(
        campaign_id, bank_items, source_file=source_file or draft.source_file, headers=headers
    )
    return campaign_id, cycle_id, save_checkin_draft(stores, draft.patch(draft_id=campaign_id))


def load_checkin_draft(stores: Stores, campaign_id: str) -> DraftCampaign | None:
    """Rehydrate the durable check-in draft, or ``None``. The campaign dir IS the identity, so
    ``draft_id`` / ``tenant_id`` come from the store's tenant scope — a cross-tenant id isn't found."""
    data = stores.checkin.read_draft(campaign_id)
    if data is None:
        return None
    return DraftCampaign.from_disk(data, draft_id=campaign_id, tenant_id=stores.identity.tenant_id)


def save_checkin_draft(
    stores: Stores, draft: DraftCampaign, *, resolution: dict[str, Any] | None = None
) -> DraftCampaign:
    """The single write-back seam every draft-mutating handler rides. It writes BOTH ``draft.json`` and
    the derived ``cache.json::resolution``, so no caller can refresh one and leave the other stale."""
    stores.checkin.write_draft(draft.draft_id, draft.to_disk())
    stores.checkin.write_resolution(draft.draft_id, resolution or resolution_block(draft))
    return draft


@dataclass(frozen=True, slots=True)
class PreparedCheckinRun:
    session: Session
    campaign_config: CampaignConfig
    train_data: list[Any]
    cycle_id: str
    session_id: str


def load_checkin_for_start(stores: Stores, campaign_id: str) -> tuple[CycleHop, DraftCampaign]:
    """Load + gate a check-in for Start. An incomplete origin raises ``OriginIncompleteError`` → 422
    with the open gaps, leaving the campaign in check-in."""
    recover_pending_replacements(stores=stores)
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None or campaign.owner_user_id != str(stores.identity.user_id):
        raise LaunchError(f"campaign not found or not owned: {campaign_id}")
    if not is_checkin(stores.campaigns.cycle_dir(campaign.root_hop)):
        raise LaunchError(f"campaign {campaign_id} is not in check-in — its origin is committed")
    draft = load_checkin_draft(stores, campaign_id)
    if draft is None:
        raise LaunchError(f"campaign {campaign_id} has no check-in working state to start")
    _assert_origin_ready(draft)
    return campaign.root_hop, draft


async def prepare_checkin_run(
    stores: Stores,
    *,
    hop: CycleHop,
    draft: DraftCampaign,
    make_session: Callable[[str], Awaitable[Session]],
) -> PreparedCheckinRun:
    """The single irreversible Start body: commit the origin, build the session, flip to ``active``.
    Run-invocation-agnostic — preflight, the machine slot and the run itself are the caller's."""
    canonical = dataset_source_of(draft.source_file)
    if canonical is None:
        if stores.tenant_datasets.slug_exists(draft.slug):
            raise LaunchError(
                f"slug collision at start: {draft.slug!r} already exists in this tenant's collection"
            )
        bank = stores.checkin.load_bank(hop.campaign_id)
        if bank is None:
            raise LaunchError(f"campaign {hop.campaign_id} has no sample bank to materialize")
        materialize_and_write_origin(stores, draft, bank_items=list(bank.get("items", [])))
        dataset_name = draft.slug
        pipeline_overlay: dict[str, Any] = {}
        origin_override = None
    else:
        persist_origin_candidate_library(stores, canonical, draft)
        dataset_name = canonical
        pipeline_overlay = draft.pipeline_overlay
        # Whenever THIS turn authored an origin, not only when it reused one. Gated on
        # `reused_origin_id` the override was dropped in the case that needs it: a fresh origin
        # over an existing slug never reaches `materialize_and_write_origin` (that writes the
        # dataset, which later campaigns share), so the run silently measured whatever
        # `prompts/default.yaml` the FIRST campaign on that slug committed — a prompt nobody in
        # this check-in saw, under the first campaign's identity, so the cache replayed it too.
        # `committed_prompt_fields` rather than the raw dict, or the label enumeration is lost.
        authored = any(str(value).strip() for value in draft.origin_prompt_fields.values())
        origin_override = draft.committed_prompt_fields() if authored else None

    session = await make_session(dataset_name)

    dataset_root = readable_dataset_dir(stores, dataset_name)
    campaign_config = build_cycle_config(session, dataset_root, pipeline_overlay=pipeline_overlay)

    train_data = session.samples
    plan = resolve_cycle_plan(session, campaign_config, train_data, origin_override=origin_override)

    index = stores.campaigns.load(hop) or {}
    session_id = str(index.get("parent_session_id") or "")
    if not session_id:
        raise LaunchError(
            f"checkin cycle {hop.cycle_id} in {hop.campaign_id} has no parent_session_id"
        )

    finalize_checkin_to_active(
        session,
        campaign_config,
        hop=hop,
        session_id=session_id,
        cycle_plan=plan,
        dataset_size=len(train_data),
    )
    if origin_override:
        stores.campaigns.write_cycle_seed(
            hop,
            CycleSeed(origin_prompt_fields=origin_override, origin_source="campaign_origin"),
        )

    return PreparedCheckinRun(
        session=session,
        campaign_config=campaign_config,
        train_data=train_data,
        cycle_id=hop.cycle_id,
        session_id=session_id,
    )


async def start_checkin_campaign(
    *,
    stores: Stores,
    job_registry: JobRegistry,
    campaign_id: str,
    halt_at_accuracy: float | None = None,
    spend_budget_usd: float | None = None,
    token_budget: int | None = None,
    backend_url: str = DEFAULT_BACKEND_URL,
) -> Any:
    """Transition (b), web tail — reserve the machine slot, then spawn the runner as a detached task.
    The CLI ``new <file>`` shares :func:`prepare_checkin_run` but runs the loop inline."""
    hop, draft = load_checkin_for_start(stores, campaign_id)

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
            hop=hop,
        )
    )

    # ADMISSION — nothing here touches the check-in cycle, so a refusal answers for the machine
    # slot alone and leaves the campaign re-startable once the account has room again.
    try:
        await _run_preflight(draft.connector, backend_url)
        spend_budget_usd, token_budget = await asyncio.to_thread(
            admit_launch,
            requested_cap_usd=spend_budget_usd,
            requested_cap_tokens=token_budget,
            user=user,
            stores=stores,
            job_registry=job_registry,
        )
        job_registry.set_caps(job.job_id, cap_usd=spend_budget_usd, cap_tokens=token_budget)
    except BaseException as exc:
        _release_slot(job_registry, job.job_id, exc, admitted=False)
        raise

    async def make_session(dataset_name: str) -> Session:
        return await init_services(
            backend_url=backend_url, dataset_name=dataset_name, identity=stores.identity
        )

    try:
        prepared = await prepare_checkin_run(
            stores,
            hop=hop,
            draft=draft,
            make_session=make_session,
        )
    except BaseException as exc:
        # `prepare_checkin_run` flips checkin → active before its last await, so an interrupt
        # there leaves an `active` campaign with no producer. The CYCLE needs stamping too, or
        # it derives `detached` and `load_checkin_for_start` can no longer re-start it.
        _release_slot(job_registry, job.job_id, exc)
        _record_launch_stop(
            stores=stores,
            hop=hop,
            session_id="",
            exc=exc,
        )
        raise

    task = asyncio.create_task(
        _run_in_background(
            session=prepared.session,
            campaign_config=prepared.campaign_config,
            train_data=prepared.train_data,
            job_registry=job_registry,
            job_id=job.job_id,
            halt_at_accuracy=halt_at_accuracy,
            spend_budget_usd=spend_budget_usd,
            token_budget=token_budget,
        ),
        name=f"job-{job.job_id}",
    )
    job_registry.attach_task(job.job_id, task)
    logger.info("start-checkin: started %s/%s (job %s)", hop.campaign_id, hop.cycle_id, job.job_id)
    return job


__all__ = [
    "PreparedCheckinRun",
    "create_checkin_campaign",
    "load_checkin_draft",
    "prepare_checkin_run",
    "save_checkin_draft",
    "start_checkin_campaign",
]
