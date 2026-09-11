from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.commands.dispatcher import CommandDispatcher
from promptpotter.application.commands.payloads import (
    EditDraftCampaignPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
)
from promptpotter.application.datasets.draft_campaign import load_checkin_draft
from promptpotter.application.datasets.draft_patch import (
    EditDraftPatch,
    apply_draft_patch,
    plan_draft_patch,
)
from promptpotter.application.datasets.origin_readiness import origin_delta, origin_projection
from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
from promptpotter.application.jobs.launcher.checkin import (
    load_checkin_for_start,
    save_checkin_draft,
)
from promptpotter.application.jobs.launcher.draft_build import draft_wire
from promptpotter.application.jobs.launcher.mint_and_start import OriginIncompleteError
from promptpotter.domain.connector import BackendUnreachableError
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError, PotterError, ServiceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.domain.cycle_paths import CycleHop

logger = logging.getLogger(__name__)

__all__ = ["dispatch_draft_patch", "dispatch_origin_resolution", "dispatch_start_checkin"]


def _reread_draft(stores: Stores, draft_id: str) -> Any:

    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return draft


def reread_draft_wire(stores: Stores, draft_id: str) -> dict[str, Any]:
    """The post-mutation draft, re-read from ``draft.json`` — the response body for a deduped
    ``Idempotency-Key`` retry, whose first attempt already persisted it."""

    return draft_wire(_reread_draft(stores, draft_id), stores.base_dir)


def origin_effect(stores: Stores, draft_id: str, before: dict[str, Any]) -> dict[str, Any]:
    """What the applier MOVED in the origin, diffed against its pre-apply projection. Recorded on
    the ack because the command payload states only what was ASKED for."""

    return origin_delta(before, origin_projection(_reread_draft(stores, draft_id)))


async def dispatch_draft_patch(
    stores: Stores,
    *,
    draft_id: str,
    patch: EditDraftPatch,
    idempotency_key: str,
) -> dict[str, Any]:
    """The single write path behind ``edit-draft-campaign``. The candidate-library ingresses and
    the CLI's ``--set`` derive their patch and then ride this, so an origin edit is a
    ``CommandRecord`` whatever the ingress looked like."""

    draft = _reread_draft(stores, draft_id)
    plan = plan_draft_patch(stores, draft, patch)

    def _apply() -> dict[str, Any]:
        updated = apply_draft_patch(draft, plan)
        save_checkin_draft(stores, updated)
        return draft_wire(updated, stores.base_dir)

    before = origin_projection(draft)
    outcome = await CommandDispatcher(stores).dispatch_checkin_command(
        kind="edit-draft-campaign",
        campaign_id=draft_id,
        payload=EditDraftCampaignPayload(draft_id=draft_id, patch=patch).model_dump(mode="json"),
        idempotency_key=idempotency_key,
        applier=_apply,
        on_replay=lambda: reread_draft_wire(stores, draft_id),
        effect_fn=lambda: origin_effect(stores, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


async def dispatch_origin_resolution(
    stores: Stores,
    *,
    draft_id: str,
    message: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """One origin-resolver turn, recorded. Calling ``resolve_origin_turn`` bare puts the turn on no
    ledger AND re-spends the LLM call that ``on_replay`` serves from ``cache.json``."""

    draft = _reread_draft(stores, draft_id)

    async def _apply() -> dict[str, Any]:
        try:
            result = await resolve_origin_turn(stores=stores, draft=draft, message=message)
        except PotterError:
            raise
        except Exception as exc:
            # A PotterError, so the dispatcher's mapping seam emits a `rejected` ack and
            # re-raises as 502 rather than the generic 409 the bare-Exception arm produces.
            logger.exception("resolve-origin turn failed for draft %s", draft_id)
            raise ServiceUnavailableError(
                f"origin resolver turn failed: {exc}", code="resolver_failed"
            ) from exc
        return {"resolution": result.resolution, "draft": draft_wire(result.draft, stores.base_dir)}

    def _on_replay() -> dict[str, Any]:
        # `cache.json::resolution` is byte-identical to the live turn's block, so a deduped
        # retry never re-spends the LLM call.
        bank = stores.checkin.load_bank(draft_id) or {}
        return {
            "resolution": bank.get("resolution") or {},
            "draft": reread_draft_wire(stores, draft_id),
        }

    before = origin_projection(draft)
    outcome = await CommandDispatcher(stores).dispatch_checkin_command(
        kind="resolve-origin",
        campaign_id=draft_id,
        payload=ResolveOriginPayload(draft_id=draft_id, message=message).model_dump(mode="json"),
        idempotency_key=idempotency_key,
        applier=_apply,
        on_replay=_on_replay,
        effect_fn=lambda: origin_effect(stores, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


async def dispatch_start_checkin[T](
    stores: Stores,
    *,
    campaign_id: str,
    idempotency_key: str,
    start: Callable[[CycleHop, DraftCampaign], Awaitable[T]],
) -> T:
    draft = _reread_draft(stores, campaign_id)

    async def _apply() -> T:
        try:
            hop, gated = load_checkin_for_start(stores, campaign_id)
            return await start(hop, gated)
        except OriginIncompleteError:
            # Lifecycle stays ``checkin`` so the operator can resolve the gaps and retry; the
            # exception already carries code=origin_incomplete + details.gaps.
            save_checkin_draft(stores, draft)
            raise
        except BackendUnreachableError as exc:
            # Preflight ran before any irreversible write, so the check-in survives and the
            # operator retries without re-authoring.
            exc.details["campaign_id"] = campaign_id
            raise

    outcome = await CommandDispatcher(stores).dispatch_checkin_command(
        kind="start-checkin",
        campaign_id=campaign_id,
        payload=StartCheckinPayload(campaign_id=campaign_id).model_dump(mode="json"),
        idempotency_key=idempotency_key,
        applier=_apply,
        # The flip from `checkin` to `active` is the retry guard: a second Start is a `LaunchError`.
        dedupe=False,
    )
    return cast("T", outcome.result)
