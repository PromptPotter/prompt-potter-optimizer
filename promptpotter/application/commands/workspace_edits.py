from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, assert_never

from promptpotter.application import account, library
from promptpotter.application.account import AccountKey, put_account_key, remove_account_key
from promptpotter.application.commands.dispatcher import Applier, Dispatched, Replaying, replays
from promptpotter.application.commands.draft_editing import reread_draft_wire
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.datasets.ingest import (
    draft_from_dataset,
    draft_from_origin,
    ingest_draft,
)
from promptpotter.application.jobs.launcher.admission import destroying
from promptpotter.config.settings import TERMS_VERSION
from promptpotter.domain.command_kinds import (
    AcceptTermsPayload,
    ArchiveCampaignPayload,
    BankSnippetPayload,
    CommandKind,
    DeleteCampaignPayload,
    IngestUploadPayload,
    LifecyclePayload,
    OpenDatasetDraftPayload,
    OpenOriginDraftPayload,
    RemoveApiKeyPayload,
    SetApiKeyPayload,
    SetCampaignLabelPayload,
    SetUserSettingsPayload,
    UnarchiveCampaignPayload,
)
from promptpotter.domain.records.run_records import DraftOpenedEffect
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import inner_sandboxes_dir
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import FileKind, FileUnreadableError


async def campaign_lifecycle(dispatched: Dispatched[LifecyclePayload]) -> Applier[object]:
    dispatcher, payload = dispatched.dispatcher, dispatched.payload
    stores = dispatcher.stores

    def _apply() -> None:
        if isinstance(payload, UnarchiveCampaignPayload):
            # It destroys nothing, so it takes no destroyer's guard.
            _lifecycle(stores, payload)
            return
        with destroying(stores, dispatcher.job_registry, payload.campaign_id):
            _lifecycle(stores, payload)

    return Applier.blocking(_apply)


def _lifecycle(stores: Stores, payload: LifecyclePayload) -> None:
    campaigns = stores.campaigns
    campaign_id = payload.campaign_id
    if isinstance(payload, ArchiveCampaignPayload):
        campaigns.archive_campaign(campaign_id)
    elif isinstance(payload, UnarchiveCampaignPayload):
        campaigns.unarchive_campaign(campaign_id)
    elif isinstance(payload, DeleteCampaignPayload):
        campaigns.delete_campaign(
            campaign_id,
            keep_results=payload.keep_results,
            inner_sandbox_root=inner_sandboxes_dir(stores.shared_root),
        )
    else:
        assert_never(payload)


async def set_campaign_label(dispatched: Dispatched[SetCampaignLabelPayload]) -> Applier[object]:
    """Identity-neutral: ``label`` is not in ``root_content_hash``, so a rename voids no banked origin."""
    campaigns = dispatched.dispatcher.stores.campaigns
    campaign_id, label = dispatched.payload.campaign_id, dispatched.payload.label

    return Applier.blocking(lambda: campaigns.update_campaign(campaign_id, label=label))


def _opening(run: Callable[[], Awaitable[DraftCampaignWire]]) -> Applier[DraftCampaignWire]:
    """The three doors a check-in draft opens by (`reopened`)."""
    return Applier(run, effect_fn=lambda wire: DraftOpenedEffect(draft_id=wire.draft_id))


@replays(CommandKind.INGEST_UPLOAD, CommandKind.OPEN_DATASET_DRAFT, CommandKind.OPEN_ORIGIN_DRAFT)
async def reopened(replaying: Replaying[Any]) -> DraftCampaignWire:
    """A retry under the first act's key answers the draft it opened, as that draft reads now."""
    effect, stores = replaying.end.effect, replaying.stores
    if not isinstance(effect, DraftOpenedEffect):
        raise FileUnreadableError(
            FileKind.LEDGER,
            path=str(CycleEventLog.workspace_path(stores.base_dir)),
            reason=f"a draft-open's ack names no draft, its effect is {effect.kind!r}",
        )
    return await reread_draft_wire(stores, effect.draft_id)


async def ingest_upload(
    dispatched: Dispatched[IngestUploadPayload],
) -> Applier[DraftCampaignWire]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    async def _apply() -> DraftCampaignWire:
        return await ingest_draft(
            stores=stores, blob=payload.blob, filename=payload.filename, slug=payload.slug
        )

    return _opening(_apply)


async def open_dataset_draft(
    dispatched: Dispatched[OpenDatasetDraftPayload],
) -> Applier[DraftCampaignWire]:
    stores, name = dispatched.dispatcher.stores, dispatched.payload.dataset_name

    async def _apply() -> DraftCampaignWire:
        return await draft_from_dataset(stores=stores, dataset_name=name)

    return _opening(_apply)


async def open_origin_draft(
    dispatched: Dispatched[OpenOriginDraftPayload],
) -> Applier[DraftCampaignWire]:
    stores, origin_id = dispatched.dispatcher.stores, dispatched.payload.origin_id

    async def _apply() -> DraftCampaignWire:
        return await draft_from_origin(stores=stores, origin_id=origin_id)

    return _opening(_apply)


# What an account sets on itself: its preferences, its consent and its own provider keys.
async def set_user_settings(
    dispatched: Dispatched[SetUserSettingsPayload],
) -> Applier[account.UserSettings]:
    stores = dispatched.dispatcher.stores
    sent = account.UserSettings(demo_mode_enabled=dispatched.payload.demo_mode_enabled)

    return Applier.blocking(lambda: account.set_user_settings(stores, sent))


@replays(CommandKind.SET_USER_SETTINGS)
async def settings_set(replaying: Replaying[SetUserSettingsPayload]) -> account.UserSettings:
    # Its key names this payload, so the first act set exactly these.
    return account.UserSettings(demo_mode_enabled=replaying.payload.demo_mode_enabled)


async def accept_terms(
    dispatched: Dispatched[AcceptTermsPayload],
) -> Applier[account.TermsConsent]:
    stores, version = dispatched.dispatcher.stores, dispatched.payload.version

    return Applier.blocking(lambda: account.accept_terms(stores, version))


@replays(CommandKind.ACCEPT_TERMS)
async def terms_accepted(replaying: Replaying[AcceptTermsPayload]) -> account.TermsConsent:
    # One consent line per act: a retry writes none.
    version = replaying.payload.version
    return account.TermsConsent(terms_version=TERMS_VERSION, terms_accepted_version=version)


async def set_api_key(dispatched: Dispatched[SetApiKeyPayload]) -> Applier[AccountKey]:
    """Its payload is `unmatched`: no retry is answered as it, so it replays nothing."""
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    return Applier.blocking(lambda: put_account_key(stores, payload.provider, payload.secret))


async def remove_api_key(dispatched: Dispatched[RemoveApiKeyPayload]) -> Applier[object]:
    stores, provider = dispatched.dispatcher.stores, dispatched.payload.provider

    return Applier.blocking(lambda: remove_account_key(stores, provider))


async def bank_snippet(dispatched: Dispatched[BankSnippetPayload]) -> Applier[object]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    return Applier.blocking(
        lambda: library.bank_snippet(
            stores,
            supersedes=payload.supersedes,
            text=payload.text,
            experiment=payload.experiment,
        )
    )
