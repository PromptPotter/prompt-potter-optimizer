from __future__ import annotations

import logging
from typing import Annotated, Any, cast, get_args

from fastapi import APIRouter, Header, Path, Request
from fastapi.routing import APIRoute
from pydantic import Field, ValidationError

from promptpotter.application.commands.dispatcher import CommandCall, CommandDispatcher
from promptpotter.application.commands.draft_editing import dispatch_draft_patch
from promptpotter.application.commands.launching import dispatch_start_checkin
from promptpotter.application.commands.origin_resolving import (
    ResolveOriginResponse,
    dispatch_origin_resolution,
)
from promptpotter.application.commands.payloads import (
    PAYLOAD_MODEL_FOR_KIND,
    CommandAcceptedBody,
    CommandPayload,
    CompactArchivePayload,
    CyclePayload,
    DatasetReplaced,
    EditDraftCampaignPayload,
    LifecyclePayload,
    ReplaceDatasetPayload,
    ResolveOriginPayload,
    SetCampaignLabelPayload,
    StartCheckinPayload,
    WorkspacePayload,
)
from promptpotter.application.datasets.draft_build import DraftCampaignWire
from promptpotter.application.jobs.launcher.checkin import StartCheckinResponse
from promptpotter.application.maintenance.archive_maintenance import ArchiveReport
from promptpotter.domain.command_kinds import (
    ALL_DISPATCHED_KINDS,
    CampaignConfigKind,
    CheckinScopedKind,
    LifecycleKind,
    WorkspaceScopedKind,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.presentation.api.deps import JobRegistryDep, StoresDep
from promptpotter.shared.errors import (
    BadRequestError,
    NotFoundError,
    PayloadInvalidError,
)

logger = logging.getLogger(__name__)

commands_router = APIRouter(prefix="/commands", tags=["Commands"])

_WORKSPACE_SCOPED_KINDS: frozenset[str] = frozenset(get_args(WorkspaceScopedKind))
_CAMPAIGN_SCOPED_KINDS: frozenset[str] = frozenset(
    get_args(LifecycleKind) + get_args(CampaignConfigKind)
)
# A kind answering a domain object, not a 202, keeps its own typed route and stays off the generic.
_TYPED_ROUTE_KINDS: frozenset[str] = frozenset(get_args(CheckinScopedKind)) | {
    "replace-dataset",
    "compact-archive",
}
# SUBTRACTED, not a union of the Literals: a union silently omits whatever it forgets to name.
_WIRED_KINDS: frozenset[str] = ALL_DISPATCHED_KINDS - _TYPED_ROUTE_KINDS


class CommandEnvelope(StrictModel):
    """The inbound envelope every command is posted in."""

    kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    payload: dict[str, Any] = Field(default_factory=dict)


def ensure_idempotency_key(header_value: str | None) -> str:
    if not header_value or not header_value.strip():
        raise BadRequestError(
            "Idempotency-Key header is required on every command.",
            code="idempotency_key_missing",
        )
    return header_value.strip()


def _validated_payload(kind: str, raw: dict[str, Any]) -> CommandPayload:
    try:
        return PAYLOAD_MODEL_FOR_KIND[kind].model_validate(raw)
    except ValidationError as exc:
        raise PayloadInvalidError(f"payload invalid for {kind!r}: {exc}") from exc


def _require_kind(envelope: CommandEnvelope, expected: str) -> None:
    if envelope.kind != expected:
        raise PayloadInvalidError(f"envelope.kind must be {expected!r}, got {envelope.kind!r}.")


@commands_router.post("/edit-draft-campaign", response_model=DraftCampaignWire)
async def edit_draft_campaign(
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DraftCampaignWire:
    """Sparse-patch a `DraftCampaign` and return its full post-mutation shape."""
    _require_kind(envelope, "edit-draft-campaign")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(
        EditDraftCampaignPayload, _validated_payload("edit-draft-campaign", envelope.payload)
    )
    return await dispatch_draft_patch(stores, CommandCall(payload, idemp))


@commands_router.post("/resolve-origin", response_model=ResolveOriginResponse)
async def resolve_origin(
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ResolveOriginResponse:
    """Run one origin-resolver turn against a draft, synchronously, and return ``{resolution, draft}``."""
    _require_kind(envelope, "resolve-origin")
    payload = cast(ResolveOriginPayload, _validated_payload("resolve-origin", envelope.payload))
    return await dispatch_origin_resolution(
        stores, CommandCall(payload, ensure_idempotency_key(idempotency_key))
    )


@commands_router.post("/start-checkin", response_model=StartCheckinResponse)
async def start_checkin(
    job_registry: JobRegistryDep,
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> StartCheckinResponse:
    """Flip a check-in campaign to ``active``, mint its run cycle and detach the loop.

    An incomplete origin answers 422 and the campaign stays ``checkin``.
    """
    _require_kind(envelope, "start-checkin")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(StartCheckinPayload, _validated_payload("start-checkin", envelope.payload))
    launched = await dispatch_start_checkin(
        stores, CommandCall(payload, idemp), job_registry=job_registry
    )
    job = launched.job
    return StartCheckinResponse(
        campaign_id=job.campaign_id, cycle_id=job.cycle_id, job_id=job.job_id
    )


@commands_router.post("/compact-archive")
async def compact_archive(
    request: Request,
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ArchiveReport:
    """Compact, restore or purge the measurement archive's cold store; a preview and its apply answer in one shape."""
    _require_kind(envelope, "compact-archive")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(CompactArchivePayload, _validated_payload("compact-archive", envelope.payload))
    outcome = await CommandDispatcher(stores).dispatch_compact_archive(CommandCall(payload, idemp))
    return outcome.result


@commands_router.post("/replace-dataset")
async def replace_dataset(
    request: Request,
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DatasetReplaced:
    """Version-and-repoint a colliding dataset so its name frees for new data.

    Nothing is overwritten: the old data and every prior campaign's results stay under ``{slug}-vN``.
    The freed name is re-ingested by a separate ``/datasets/ingest`` call.
    """
    _require_kind(envelope, "replace-dataset")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(ReplaceDatasetPayload, _validated_payload("replace-dataset", envelope.payload))
    outcome = await CommandDispatcher(stores).dispatch_replace_dataset(CommandCall(payload, idemp))
    return outcome.result


@commands_router.post("/{kind}", response_model=CommandAcceptedBody, status_code=202)
async def post_command(
    job_registry: JobRegistryDep,
    stores: StoresDep,
    envelope: CommandEnvelope,
    kind: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    expected_version: Annotated[int | None, Header(alias="Expected-Version")] = None,
) -> CommandAcceptedBody:
    """The closed-set command surface: every wired kind validates against its declared schema, then dispatches."""
    idemp = ensure_idempotency_key(idempotency_key)
    if kind != envelope.kind:
        raise PayloadInvalidError(
            f"path kind {kind!r} does not match envelope.kind {envelope.kind!r}"
        )
    if kind not in _WIRED_KINDS:
        raise NotFoundError(
            f"Command kind {kind!r} not wired. See "
            f"docs/specs/api-openapi.yaml for the declared set.",
            code="command_kind_unknown",
        )

    payload = _validated_payload(kind, envelope.payload)
    dispatcher = CommandDispatcher(stores, job_registry=job_registry)

    if kind in _WORKSPACE_SCOPED_KINDS:
        # `_WIRED_KINDS` already turned the two typed-route workspace kinds away.
        workspace_outcome = await dispatcher.dispatch_workspace_command(
            CommandCall(cast(WorkspacePayload, payload), idemp)
        )
        return workspace_outcome.accepted

    if kind in _CAMPAIGN_SCOPED_KINDS:
        campaign_outcome = await dispatcher.dispatch_campaign_command(
            CommandCall(cast(LifecyclePayload | SetCampaignLabelPayload, payload), idemp)
        )
        return campaign_outcome.accepted

    cycle_outcome = await dispatcher.dispatch_cycle_command(
        CommandCall(cast(CyclePayload, payload), idemp), expected_version=expected_version
    )
    return cycle_outcome.accepted


_ROUTED_KINDS: frozenset[str] = frozenset(
    route.path.removeprefix("/commands/")
    for route in commands_router.routes
    if isinstance(route, APIRoute) and route.path != "/commands/{kind}"
)
if _ROUTED_KINDS - ALL_DISPATCHED_KINDS:
    raise RuntimeError(
        "command route with no dispatched kind — it is gated by no capability and lands on no "
        f"ledger: {sorted(_ROUTED_KINDS - ALL_DISPATCHED_KINDS)}"
    )


__all__ = ["CommandEnvelope", "commands_router"]
