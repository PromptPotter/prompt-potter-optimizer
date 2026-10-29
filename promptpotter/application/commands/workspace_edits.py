from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from promptpotter.application import library
from promptpotter.application.commands.dispatcher import Applier
from promptpotter.domain.command_kinds import (
    ArchiveCampaignPayload,
    DeleteCampaignPayload,
    UnarchiveCampaignPayload,
)
from promptpotter.infrastructure.store.layout import inner_sandboxes_dir
from promptpotter.shared.clock import utcnow_iso

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched
    from promptpotter.domain.command_kinds import (
        BankSnippetPayload,
        LifecyclePayload,
        SetCampaignLabelPayload,
    )


def campaign_lifecycle(dispatched: Dispatched[LifecyclePayload]) -> Applier[object]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload

    def _apply() -> None:
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


def set_campaign_label(dispatched: Dispatched[SetCampaignLabelPayload]) -> Applier[object]:
    """Identity-neutral: ``label`` is not in ``root_content_hash``, so a rename voids no banked origin."""
    campaigns = dispatched.dispatcher.stores.campaigns
    campaign_id, label = dispatched.payload.campaign_id, dispatched.payload.label

    def _apply() -> None:
        campaigns.update_campaign(campaign_id, label=label)

    return Applier.silent(_apply)


def bank_snippet(dispatched: Dispatched[BankSnippetPayload]) -> Applier[object]:
    stores, payload = dispatched.dispatcher.stores, dispatched.payload
    return Applier.silent(
        lambda: library.bank_snippet(
            stores,
            supersedes=payload.supersedes,
            text=payload.text,
            experiment=payload.experiment,
        )
    )
