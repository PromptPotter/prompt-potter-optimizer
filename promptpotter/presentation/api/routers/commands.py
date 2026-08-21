"""``POST /commands/{kind}`` — the closed inbound surface; parse, enforce the trust-boundary headers, delegate. A kind is
declared in the OpenAPI spec BEFORE its handler lands, and a response shape never buys its own write path."""

from __future__ import annotations

import logging
from typing import Annotated, Any, cast, get_args

from fastapi import APIRouter, Header, Path, Request
from fastapi.routing import APIRoute
from pydantic import Field, ValidationError

from promptpotter.application.jobs.launcher.checkin import (
    load_checkin_draft,
    save_checkin_draft,
    start_checkin_campaign,
)
from promptpotter.application.jobs.launcher.mint_and_start import OriginIncompleteError
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.connectors import BackendUnreachableError
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import resolve_cycle_path
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.middleware.command_dispatcher import (
    ALL_DISPATCHED_KINDS,
    PAYLOAD_MODEL_FOR_KIND,
    CampaignConfigKind,
    CampaignPayload,
    CheckinScopedKind,
    CommandAcceptedBody,
    CommandDispatcher,
    CommandPayload,
    CyclePayload,
    CycleScopedKind,
    DescendableCyclePayload,
    EditDraftCampaignPayload,
    LifecycleKind,
    LifecyclePayload,
    ReplaceDatasetPayload,
    ResolveOriginPayload,
    StartCheckinPayload,
    WorkspaceScopedKind,
    dispatch_draft_patch,
    dispatch_origin_resolution,
)
from promptpotter.shared.errors import (
    BadRequestError,
    NotFoundError,
    PayloadInvalidError,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)

commands_router = APIRouter(prefix="/commands", tags=["Commands"])

# Derived from the dispatcher's kind Literals (the SoT), never re-authored here — so a new
# kind reaches the router the moment it joins its Literal.
_LIFECYCLE_KINDS: frozenset[str] = frozenset(get_args(LifecycleKind))
_CYCLE_SCOPED_KINDS: frozenset[str] = frozenset(get_args(CycleScopedKind))
_WORKSPACE_SCOPED_KINDS: frozenset[str] = frozenset(get_args(WorkspaceScopedKind))
_CAMPAIGN_CONFIG_KINDS: frozenset[str] = frozenset(get_args(CampaignConfigKind))
# A kind answering a domain object rather than a 202 keeps its own typed route and stays off
# the generic one; `replace-dataset` is the one such kind outside `CheckinScopedKind`.
_TYPED_ROUTE_KINDS: frozenset[str] = frozenset(get_args(CheckinScopedKind)) | {"replace-dataset"}
# SUBTRACTED from the dispatched set rather than re-authored as a union of the four Literals,
# because a union silently omits whatever it forgets to name — so a new kind is wired by
# default and going unwired is the thing you have to write down.
_WIRED_KINDS: frozenset[str] = ALL_DISPATCHED_KINDS - _TYPED_ROUTE_KINDS


class CommandEnvelope(StrictModel):
    """Inbound envelope per ``m12-api-openapi.yaml#/components/schemas/CommandEnvelope``."""

    kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    payload: dict[str, Any] = Field(default_factory=dict)


def ensure_idempotency_key(header_value: str | None) -> str:
    """Trust-boundary check — 400 ``idempotency_key_missing`` when absent or empty."""
    if not header_value or not header_value.strip():
        raise BadRequestError(
            "Idempotency-Key header is required on every command.",
            code="idempotency_key_missing",
        )
    return header_value.strip()


def _validated_payload(kind: str, raw: dict[str, Any]) -> CommandPayload:
    """The WHOLE of payload validation for every dispatched kind, at one call. The model's own
    ``extra="forbid"`` is what refuses an undeclared key: dropping one instead is how a field
    reaches the applier as an absent argument and fails as if it had never been sent."""
    try:
        return PAYLOAD_MODEL_FOR_KIND[kind].model_validate(raw)
    except ValidationError as exc:
        raise PayloadInvalidError(f"payload invalid for {kind!r}: {exc}") from exc


def _require_kind(envelope: CommandEnvelope, expected: str) -> None:
    """Pin a typed route's envelope to its declared ``kind`` const — the same check
    ``post_command`` runs against the path segment."""
    if envelope.kind != expected:
        raise PayloadInvalidError(f"envelope.kind must be {expected!r}, got {envelope.kind!r}.")


@commands_router.post("/edit-draft-campaign")
async def edit_draft_campaign(
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Sparse-patch a `DraftCampaign`. Returns the post-mutation full shape.

    Per ``docs/specs/m12-api-openapi.yaml::editDraftCampaign``. The mutation rides
    `CommandDispatcher` (architecture.md §0: sole writer of `CommandRecord`); only
    the response shape differs from the generic 202 verbs, never the ingress.
    """
    _require_kind(envelope, "edit-draft-campaign")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(
        EditDraftCampaignPayload, _validated_payload("edit-draft-campaign", envelope.payload)
    )
    return await dispatch_draft_patch(
        stores,
        draft_id=payload.draft_id,
        patch=payload.patch,
        idempotency_key=idemp,
    )


@commands_router.post("/resolve-origin")
async def resolve_origin(
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Run one origin-resolver turn against a draft. Returns ``{resolution, draft}``.

    Per ``docs/specs/m12-api-openapi.yaml::resolveOrigin``. Synchronous, like
    ``edit-draft-campaign`` — the resolver's findings apply in-line and the
    deterministic checklist re-gates before the response.
    """
    _require_kind(envelope, "resolve-origin")
    payload = cast(ResolveOriginPayload, _validated_payload("resolve-origin", envelope.payload))
    return await dispatch_origin_resolution(
        stores,
        draft_id=payload.draft_id,
        message=payload.message,
        idempotency_key=ensure_idempotency_key(idempotency_key),
    )


@commands_router.post("/start-checkin")
async def start_checkin(
    request: Request,
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Flip a CHECKIN campaign to ``active`` + spawn the runner. Synchronous;
    returns ``{campaign_id, cycle_id, job_id}``.

    Per ``docs/specs/m12-api-openapi.yaml::startCheckin``. The campaign already
    exists durably (minted on the first ingest action); this gate-checks the
    origin (incomplete → 422, stays ``checkin``), materializes the dataset, mints
    the run cycle, and detaches the loop.
    """
    _require_kind(envelope, "start-checkin")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(StartCheckinPayload, _validated_payload("start-checkin", envelope.payload))
    campaign_id = payload.campaign_id

    draft = load_checkin_draft(stores, campaign_id)
    if draft is None:
        raise NotFoundError(f"check-in {campaign_id!r} not found.", code="command_target_not_found")

    job_registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if job_registry is None:
        raise ServiceUnavailableError(
            "job registry not initialised", code="job_registry_unavailable"
        )

    async def _apply() -> dict[str, Any]:
        try:
            job = await start_checkin_campaign(
                stores=stores,
                job_registry=job_registry,
                campaign_id=campaign_id,
            )
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
        # LaunchError is a PayloadInvalidError — the central PotterError handler maps it to
        # 422 with its own message, so no per-case arm here.
        return {"campaign_id": campaign_id, "cycle_id": job.cycle_id, "job_id": job.job_id}

    dispatcher = CommandDispatcher(stores, job_registry=job_registry)
    outcome = await dispatcher.dispatch_checkin_command(
        kind="start-checkin",
        campaign_id=campaign_id,
        payload=payload.model_dump(mode="json"),
        idempotency_key=idemp,
        applier=_apply,
        # `job_id` has no disk home, so a deduped retry could only fabricate one;
        # the `checkin → active` flip is already the retry guard (second Start →
        # LaunchError → 422).
        dedupe=False,
    )
    return cast("dict[str, Any]", outcome.result)


@commands_router.post("/replace-dataset")
async def replace_dataset(
    request: Request,
    stores: StoresDep,
    envelope: CommandEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Version-and-repoint a colliding dataset so its name frees for new data.

    Per ``docs/specs/m12-api-openapi.yaml::replaceDataset``. Data-safe: never overwrites — the
    old data + every prior campaign's results are preserved under ``{slug}-vN``.
    Synchronous (the migration is a bounded set of renames + JSON rewrites); the
    freed name is re-ingested in a separate ``/datasets/ingest`` call.
    """
    _require_kind(envelope, "replace-dataset")
    idemp = ensure_idempotency_key(idempotency_key)
    payload = cast(ReplaceDatasetPayload, _validated_payload("replace-dataset", envelope.payload))
    dispatcher = CommandDispatcher(stores)
    outcome = await dispatcher.dispatch_workspace_command(
        kind="replace-dataset",
        payload=payload,
        idempotency_key=idemp,
    )
    # Echo the subject, nothing more — `version_and_repoint` records the counts + the
    # versioned slug itself, and no caller reads them off the wire.
    return cast("dict[str, Any]", outcome.result)


@commands_router.post("/{kind}", response_model=CommandAcceptedBody, status_code=202)
async def post_command(
    request: Request,
    stores: StoresDep,
    envelope: CommandEnvelope,
    kind: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    expected_version: Annotated[int | None, Header(alias="Expected-Version")] = None,
) -> CommandAcceptedBody:
    """Closed-set command surface — every wired kind validates against the
    declared schema in ``m12-api-openapi.yaml`` and dispatches through
    ``CommandDispatcher``."""
    idemp = ensure_idempotency_key(idempotency_key)
    if kind != envelope.kind:
        raise PayloadInvalidError(
            f"path kind {kind!r} does not match envelope.kind {envelope.kind!r}"
        )
    if kind not in _WIRED_KINDS:
        raise NotFoundError(
            f"Command kind {kind!r} not wired. See "
            f"docs/specs/m12-api-openapi.yaml for the declared set.",
            code="command_kind_unknown",
        )

    payload = _validated_payload(kind, envelope.payload)
    job_registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    dispatcher = CommandDispatcher(stores, job_registry=job_registry)

    if kind in _WORKSPACE_SCOPED_KINDS:
        workspace_kind: WorkspaceScopedKind = kind  # type: ignore[assignment]
        workspace_outcome = await dispatcher.dispatch_workspace_command(
            kind=workspace_kind,
            payload=payload,
            idempotency_key=idemp,
        )
        return workspace_outcome.accepted

    if kind in _CAMPAIGN_CONFIG_KINDS:
        # In-place manifest edit — campaign-scoped, no cycle.
        config_kind: CampaignConfigKind = kind  # type: ignore[assignment]
        config_outcome = await dispatcher.dispatch_campaign_config(
            kind=config_kind,
            payload=cast(CampaignPayload, payload),
            idempotency_key=idemp,
        )
        return config_outcome.accepted

    if kind in _LIFECYCLE_KINDS:
        lifecycle_kind: LifecycleKind = kind  # type: ignore[assignment]
        lifecycle_outcome = await dispatcher.dispatch_lifecycle(
            kind=lifecycle_kind,
            payload=cast(LifecyclePayload, payload),
            idempotency_key=idemp,
        )
        return lifecycle_outcome.accepted

    # Cycle-scoped. The address may descend into an inner sandbox where the payload TYPE declares
    # one — same grammar as the read side's `?descend=`, and the leaf is a cycle in its own tree.
    cycle_payload = cast(CyclePayload, payload)
    hops: tuple[CycleHop, ...] = (
        CycleHop(campaign_id=cycle_payload.campaign_id, cycle_id=cycle_payload.cycle_id),
    )
    if isinstance(cycle_payload, DescendableCyclePayload):
        hops = (*hops, *decode_descend(cycle_payload.descend))
    stores, leaf = resolve_cycle_path(stores, hops)
    # Re-pointed at the LEAF so the applier and the ledger record name the one cycle addressed.
    cycle_payload = cycle_payload.model_copy(
        update={"campaign_id": leaf.campaign_id, "cycle_id": leaf.cycle_id}
    )
    # Rebuilt on the RESOLVED store: a descent hands back a different workspace root, and the
    # one above was bound to the caller's own.
    dispatcher = CommandDispatcher(stores, job_registry=job_registry)
    cycle_kind: CycleScopedKind = kind  # type: ignore[assignment]
    cycle_outcome = await dispatcher.dispatch_cycle_command(
        kind=cycle_kind,
        payload=cycle_payload,
        idempotency_key=idemp,
        expected_version=expected_version,
    )
    return cycle_outcome.accepted


# Import-time closure of the command surface: every typed route must name a DISPATCHED kind, so
# it inherits `_require_capability_for` and its `CommandRecord`. A route is the only way to add a
# verb here, and a verb that dispatches nothing is authorized by nothing and recorded nowhere —
# which `replace-dataset` was, invisibly, because the cap ladder can only check what dispatches.
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
