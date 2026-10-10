from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, assert_never

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.commands.payloads import (
    ArchiveCampaignPayload,
    DatasetReplaced,
    DeleteCampaignPayload,
    UnarchiveCampaignPayload,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.infrastructure.store import dataset_replace
from promptpotter.infrastructure.store.layout import inner_sandboxes_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import CommandDispatcher
    from promptpotter.application.commands.payloads import (
        LifecyclePayload,
        RegisterBackendPayload,
        ReplaceDatasetPayload,
        SetCampaignLabelPayload,
    )

__all__ = ["campaign_lifecycle", "register_backend", "replace_dataset", "set_campaign_label"]


def campaign_lifecycle(dispatcher: CommandDispatcher, payload: LifecyclePayload) -> Applier[object]:
    stores = dispatcher.stores

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


def set_campaign_label(
    dispatcher: CommandDispatcher, payload: SetCampaignLabelPayload
) -> Applier[object]:
    """Identity-neutral: ``label`` is not in ``root_content_hash``, so a rename voids no banked origin."""
    campaign_id, label = payload.campaign_id, payload.label

    def _apply() -> dict[str, Any]:
        dispatcher.stores.campaigns.update_campaign(campaign_id, label=label)
        return {"campaign_id": campaign_id, "label": label}

    return Applier.silent(_apply)


def _slugify_backend_id(name: str) -> str:
    """Mirrors the auto-derivation ``RegisterBackendPayload.id`` documents."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")


def register_backend(
    dispatcher: CommandDispatcher, payload: RegisterBackendPayload
) -> Applier[object]:
    backends = dispatcher.stores.backends

    def _apply() -> None:
        backend_id = payload.id or _slugify_backend_id(payload.name)
        if backends.get(backend_id) is not None:
            raise ConflictError(
                f"Backend '{backend_id}' already exists", details={"backend_id": backend_id}
            )
        backends.register(
            BackendConnection(
                id=backend_id,
                name=payload.name,
                backend_type=payload.backend_type,
                base_url=payload.base_url.rstrip("/"),
            )
        )

    return Applier.silent(_apply)


def replace_dataset(
    dispatcher: CommandDispatcher, payload: ReplaceDatasetPayload
) -> Applier[DatasetReplaced]:
    slug = payload.slug

    def _apply() -> DatasetReplaced:
        try:
            result = dataset_replace.replace_dataset(stores=dispatcher.stores, slug=slug)
        except dataset_replace.NothingToReplaceError as exc:
            raise ConflictError(
                str(exc), code="nothing_to_replace", details={"slug": exc.slug}
            ) from exc
        return DatasetReplaced(slug=result.slug)

    # A deduped retry must not re-run the migration: it would version the slug a second time.
    return Applier(_apply, replay=lambda: DatasetReplaced(slug=slug))
