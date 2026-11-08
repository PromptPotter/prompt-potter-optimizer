from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, assert_never

from promptpotter.application import account, library
from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.commands.draft_editing import reread_draft_wire
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.datasets.ingest import (
    draft_from_dataset,
    draft_from_origin,
    ingest_draft,
)
from promptpotter.application.jobs.coupon import AccountKey, put_account_key, remove_account_key
from promptpotter.domain.command_kinds import (
    ArchiveCampaignPayload,
    DeleteCampaignPayload,
    UnarchiveCampaignPayload,
)
from promptpotter.domain.run_records import DraftOpenedEffect
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import inner_sandboxes_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import LedgerUnreadableError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched
    from promptpotter.domain.command_events import CommandEffect
    from promptpotter.domain.command_kinds import (
        AcceptTermsPayload,
        BankSnippetPayload,
        IngestUploadPayload,
        LifecyclePayload,
        OpenDatasetDraftPayload,
        OpenOriginDraftPayload,
        RemoveApiKeyPayload,
        SetApiKeyPayload,
        SetCampaignLabelPayload,
        SetUserSettingsPayload,
    )
    from promptpotter.infrastructure.store.stores import Stores


async def campaign_lifecycle(dispatched: Dispatched[LifecyclePayload]) -> Applier[object]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    async def _apply() -> None:
        changed_at = utcnow_iso()
        campaigns = stores.campaigns
        campaign_id, reason = payload.campaign_id, payload.reason
        if isinstance(payload, ArchiveCampaignPayload):
            campaigns.archive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        elif isinstance(payload, UnarchiveCampaignPayload):
            campaigns.unarchive_campaign(campaign_id, changed_at=changed_at, reason=reason)
        elif isinstance(payload, DeleteCampaignPayload):
            campaigns.delete_campaign(
                campaign_id,
                keep_results=payload.keep_results,
                changed_at=changed_at,
                reason=reason,
                inner_sandbox_root=inner_sandboxes_dir(stores.shared_root),
            )
        else:
            assert_never(payload)

    return Applier.silent(_apply)


async def set_campaign_label(dispatched: Dispatched[SetCampaignLabelPayload]) -> Applier[object]:
    """Identity-neutral: ``label`` is not in ``root_content_hash``, so a rename voids no banked origin."""
    campaigns = dispatched.dispatcher.stores.campaigns
    campaign_id, label = dispatched.payload.campaign_id, dispatched.payload.label

    async def _apply() -> None:
        campaigns.update_campaign(campaign_id, label=label)

    return Applier.silent(_apply)


def _opening(
    stores: Stores, run: Callable[[], Awaitable[DraftCampaignWire]]
) -> Applier[DraftCampaignWire]:
    """The three doors a check-in draft opens by: a retry under the first act's key answers the
    draft it opened, as that draft reads now."""

    async def _reopened(effect: CommandEffect) -> DraftCampaignWire:
        if not isinstance(effect, DraftOpenedEffect):
            raise LedgerUnreadableError(
                ledger=str(CycleEventLog.workspace_path(stores.base_dir)),
                offset=None,
                reason=f"a draft-open's ack names no draft, its effect is {effect.kind!r}",
                campaign_id=None,
            )
        return await reread_draft_wire(stores, effect.draft_id)

    return Applier(
        run, replay=_reopened, effect_fn=lambda wire: DraftOpenedEffect(draft_id=wire.draft_id)
    )


async def ingest_upload(
    dispatched: Dispatched[IngestUploadPayload],
) -> Applier[DraftCampaignWire]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    async def _apply() -> DraftCampaignWire:
        return await ingest_draft(
            stores=stores, blob=payload.blob, filename=payload.filename, slug=payload.slug
        )

    return _opening(stores, _apply)


async def open_dataset_draft(
    dispatched: Dispatched[OpenDatasetDraftPayload],
) -> Applier[DraftCampaignWire]:
    stores, name = dispatched.dispatcher.stores, dispatched.payload.dataset_name

    async def _apply() -> DraftCampaignWire:
        return await draft_from_dataset(stores=stores, dataset_name=name)

    return _opening(stores, _apply)


async def open_origin_draft(
    dispatched: Dispatched[OpenOriginDraftPayload],
) -> Applier[DraftCampaignWire]:
    stores, origin_id = dispatched.dispatcher.stores, dispatched.payload.origin_id

    async def _apply() -> DraftCampaignWire:
        return await draft_from_origin(stores=stores, origin_id=origin_id)

    return _opening(stores, _apply)


# What an account sets on itself: its preferences, its consent and its own provider keys.
async def set_user_settings(
    dispatched: Dispatched[SetUserSettingsPayload],
) -> Applier[account.UserSettings]:
    stores = dispatched.dispatcher.stores
    sent = account.UserSettings(demo_mode_enabled=dispatched.payload.demo_mode_enabled)

    async def _apply() -> account.UserSettings:
        return account.set_user_settings(stores, sent)

    return Applier(_apply, replay=None)


async def accept_terms(
    dispatched: Dispatched[AcceptTermsPayload],
) -> Applier[account.TermsConsent]:
    stores, version = dispatched.dispatcher.stores, dispatched.payload.version

    async def _apply() -> account.TermsConsent:
        return account.accept_terms(stores, version)

    return Applier(_apply, replay=None)


async def set_api_key(dispatched: Dispatched[SetApiKeyPayload]) -> Applier[AccountKey]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    async def _apply() -> AccountKey:
        return put_account_key(stores, payload.provider, payload.secret)

    return Applier(_apply, replay=None)


async def remove_api_key(dispatched: Dispatched[RemoveApiKeyPayload]) -> Applier[object]:
    stores, provider = dispatched.dispatcher.stores, dispatched.payload.provider

    async def _apply() -> None:
        remove_account_key(stores, provider)

    return Applier.silent(_apply)


async def bank_snippet(dispatched: Dispatched[BankSnippetPayload]) -> Applier[object]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    async def _apply() -> None:
        library.bank_snippet(
            stores,
            supersedes=payload.supersedes,
            text=payload.text,
            experiment=payload.experiment,
        )

    return Applier.silent(_apply)
