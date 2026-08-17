"""``POST /commands/{kind}`` — the closed inbound surface; parse, enforce the trust-boundary headers, delegate. A kind is
declared in the OpenAPI spec BEFORE its handler lands, and a response shape never buys its own write path."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, cast, get_args

from fastapi import APIRouter, Header, Path, Request
from fastapi.routing import APIRoute
from pydantic import Field

from promptpotter.application.datasets.draft_campaign import DraftCampaign, OptimizationOverrides
from promptpotter.application.datasets.origin_readiness import (
    origin_delta,
    origin_projection,
)
from promptpotter.application.datasets.origin_resolve import FINDING_PATCH_KEYS
from promptpotter.application.jobs.launcher.checkin import (
    load_checkin_draft,
    save_checkin_draft,
    start_checkin_campaign,
)
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.application.jobs.launcher.mint_and_start import OriginIncompleteError
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.connectors import BackendUnreachableError
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import validate_dataset_name
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.middleware.command_dispatcher import (
    ALL_DISPATCHED_KINDS,
    CampaignConfigKind,
    CheckinScopedKind,
    CommandAcceptedBody,
    CommandDispatcher,
    CycleScopedKind,
    LifecycleKind,
    WorkspaceScopedKind,
    optional_bounded_number,
)
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
    PotterError,
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


def _require_string(payload: dict[str, Any], key: str, *, max_len: int) -> str:
    raw = payload.get(key, "")
    if not isinstance(raw, str) or not raw:
        raise PayloadInvalidError(f"payload.{key} is required and must be a non-empty string.")
    if len(raw) > max_len:
        raise PayloadInvalidError(f"payload.{key} exceeds {max_len} chars.")
    return raw


def _optional_string(payload: dict[str, Any], key: str, *, max_len: int) -> str:
    raw = payload.get(key, "")
    if not isinstance(raw, str):
        raise PayloadInvalidError(f"payload.{key} must be a string.")
    if len(raw) > max_len:
        raise PayloadInvalidError(f"payload.{key} exceeds {max_len} chars.")
    return raw


_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


def _require_slug(payload: dict[str, Any], key: str, *, max_len: int) -> str:
    """Slug-validated required field per the OpenAPI pattern ``^[a-z][a-z0-9-]*$``."""
    raw = _require_string(payload, key, max_len=max_len)
    if not _SLUG_PATTERN.fullmatch(raw):
        raise PayloadInvalidError(f"payload.{key} must match ^[a-z][a-z0-9-]*$")
    return raw


def _require_dataset_name(payload: dict[str, Any], key: str = "dataset_name") -> str:
    """Extract a dataset name under whichever key the wire gives it. Deciding what a name IS belongs to ``validate_dataset_name``:
    a pattern of its own here is a second rule that can disagree with the slug ingest mints off a filename."""
    raw = _require_string(payload, key, max_len=64)
    try:
        return validate_dataset_name(raw)
    except ValueError as exc:
        raise PayloadInvalidError(f"payload.{key}: {exc}") from exc


def _run_limits(payload: dict[str, Any]) -> dict[str, float]:
    """The two operator-set run limits, shared by ``mint-campaign`` and ``start-run``."""
    limits: dict[str, float] = {}
    halt = optional_bounded_number(
        payload.get("halt_at_accuracy"), field="halt_at_accuracy", lo=0.0, hi=1.0
    )
    if halt is not None:
        limits["halt_at_accuracy"] = halt
    spend = optional_bounded_number(
        payload.get("spend_budget_usd"), field="spend_budget_usd", lo=0.0
    )
    if spend is not None:
        limits["spend_budget_usd"] = spend
    return limits


def _build_workspace_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + project the workspace payload. The result lands on the ledger record, so keep the keys minimal and
    lossless — the audit trail mirrors the request."""
    if kind == "register-backend":
        name = _require_string(payload, "name", max_len=128)
        backend_type = _require_string(payload, "backend_type", max_len=64)
        base_url = _require_string(payload, "base_url", max_len=2048)
        out: dict[str, Any] = {
            "name": name,
            "backend_type": backend_type,
            "base_url": base_url,
        }
        if payload.get("id"):
            out["id"] = _require_slug(payload, "id", max_len=64)
        return out
    # mint-campaign
    mint_out: dict[str, Any] = {"dataset_name": _require_dataset_name(payload)}
    mint_out.update(_run_limits(payload))
    return mint_out


class _EditDraftPatch(StrictModel):
    """Sparse mutation payload — only declared fields ride through."""

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    scoring_composite: str | None = Field(default=None, min_length=1, max_length=64)
    raw_task_description: str | None = Field(default=None, min_length=1, max_length=16384)
    pipeline_overlay: dict[str, Any] | None = None
    # Written by the setup-panel mode toggle; read by commit's
    # `_build_origin_pipeline_json` + `derive_optimizer_locks`.
    pipeline_steps: list[str] | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Replaces the draft's fields wholesale — the editor sends the full PromptTemplate object.
    origin_prompt_fields: dict[str, Any] | None = None
    # Shallow-merged onto the draft's current overrides then validated against
    # OptimizationOverrides, so the editor can send one knob or several; a nested
    # `mechanisms` replaces wholesale.
    optimization_overrides: dict[str, Any] | None = None
    # From the operator's upload or derived from one of the draft's own columns
    # (`routers/datasets/ingest.py`); both ride this patch.
    candidate_library: list[str] | None = Field(default=None, min_length=1)
    # Replaces the draft's set wholesale — the checklist sends the full ticked list, and an
    # empty list clears it (restrictive default). Not gated, like the connector.
    allowed_models: list[str] | None = None


# Every field the origin resolver may propose must be settable here — that correspondence is
# what renders a finding as a clickable `edit-draft-campaign` without the model ever naming a
# command. Fails at import, not at the operator's click.
if not set(FINDING_PATCH_KEYS.values()) <= set(_EditDraftPatch.model_fields):
    raise RuntimeError(
        "resolver proposes fields edit-draft-campaign cannot patch: "
        f"{set(FINDING_PATCH_KEYS.values()) - set(_EditDraftPatch.model_fields)}"
    )


def _require_kind(envelope: CommandEnvelope, expected: str) -> None:
    """Pin a typed route's envelope to its declared ``kind`` const — the same check
    ``post_command`` runs against the path segment."""
    if envelope.kind != expected:
        raise PayloadInvalidError(f"envelope.kind must be {expected!r}, got {envelope.kind!r}.")


def _require_checkin_id(payload: dict[str, Any], key: str) -> str:
    """The check-in campaign id, whichever name the wire gives it: ``draft_id`` and
    ``campaign_id`` are the same id (re-keyed at ``create_checkin_campaign``)."""
    raw = _require_string(payload, key, max_len=128)
    if len(raw) < 8:
        raise PayloadInvalidError(f"payload.{key} is required (8-128 chars).")
    return raw


def _reread_draft(stores: Stores, draft_id: str) -> DraftCampaign:
    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return draft


def _reread_draft_wire(stores: Stores, draft_id: str) -> dict[str, Any]:
    """The post-mutation draft, re-read from ``draft.json`` — the response body for
    a deduped ``Idempotency-Key`` retry, whose first attempt already persisted it."""
    return draft_wire_with_locks(_reread_draft(stores, draft_id))


def _origin_effect(stores: Stores, draft_id: str, before: dict[str, Any]) -> dict[str, Any]:
    """What the applier MOVED in the origin, diffed against its pre-apply projection. Recorded on the ack because the
    command payload states only what was ASKED for."""
    return origin_delta(before, origin_projection(_reread_draft(stores, draft_id)))


async def dispatch_draft_patch(
    stores: Stores,
    *,
    draft_id: str,
    patch_raw: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """The single write path behind ``edit-draft-campaign``. The candidate-library routes derive their patch and then ride this,
    so an origin edit is a ``CommandRecord`` whatever the ingress looked like."""
    patch = _EditDraftPatch.model_validate(patch_raw)

    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    changes: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    if patch.slug is not None and patch.slug != draft.slug:
        if stores.tenant_datasets.slug_exists(patch.slug):
            suggested = stores.tenant_datasets.suggest_free_slug(patch.slug)
            raise ConflictError(
                f"Slug '{patch.slug}' already exists in your collection.",
                code="slug_collision",
                details={"suggested_slug": suggested},
            )
        changes["slug"] = patch.slug
    # Config + the authored prompt are not gated — just set the value.
    for patch_val, draft_attr in (
        (patch.connector, "connector"),
        (patch.scoring_composite, "scoring_composite"),
        (patch.pipeline_overlay, "pipeline_overlay"),
        (patch.origin_prompt_fields, "origin_prompt_fields"),
        (patch.pipeline_steps, "pipeline_steps"),
        (patch.candidate_library, "candidate_library"),
        (patch.allowed_models, "allowed_models"),
    ):
        if patch_val is not None:
            changes[draft_attr] = patch_val

    # Shallow-merge so one knob can change without resetting the rest, then validate the
    # result (rejects unknown keys / out-of-range max_rounds / malformed mechanisms).
    if patch.optimization_overrides is not None:
        merged = {**draft.optimization_overrides, **patch.optimization_overrides}
        changes["optimization_overrides"] = OptimizationOverrides.model_validate(merged).model_dump(
            mode="json"
        )

    # The task framing IS gated — an operator edit CONFIRMS it, which is what opens the
    # origin-readiness gate for a field left PROPOSED or UNSET.
    if patch.raw_task_description is not None:
        changes["raw_task_description"] = patch.raw_task_description
        provenance["task_description"] = Provenance.CONFIRMED

    # Each column must be a member of the uploaded headers (422 otherwise); confirming flips
    # its provenance so the origin-readiness gate opens.
    for label, col in (
        ("column_query", patch.column_query),
        ("column_ground_truth", patch.column_ground_truth),
    ):
        if col is not None and col not in draft.headers:
            raise PayloadInvalidError(
                f"patch.{label} {col!r} is not one of the uploaded headers {list(draft.headers)}."
            )

    def _apply() -> dict[str, Any]:
        updated = draft.apply_resolution(values=changes, provenance=provenance)
        if patch.column_query is not None or patch.column_ground_truth is not None:
            updated = updated.confirm_columns(
                query_col=patch.column_query, ground_truth_col=patch.column_ground_truth
            )
        save_checkin_draft(stores, updated)
        return draft_wire_with_locks(updated)

    before = origin_projection(draft)
    dispatcher = CommandDispatcher(stores)
    outcome = await dispatcher.dispatch_checkin_command(
        kind="edit-draft-campaign",
        campaign_id=draft_id,
        payload={"draft_id": draft_id, "patch": patch_raw},
        idempotency_key=idempotency_key,
        applier=_apply,
        on_replay=lambda: _reread_draft_wire(stores, draft_id),
        effect_fn=lambda: _origin_effect(stores, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


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
    draft_id = _require_checkin_id(envelope.payload, "draft_id")
    patch_raw = envelope.payload.get("patch", {})
    if not isinstance(patch_raw, dict):
        raise PayloadInvalidError("payload.patch must be an object.")
    return await dispatch_draft_patch(
        stores,
        draft_id=draft_id,
        patch_raw=patch_raw,
        idempotency_key=idemp,
    )


@commands_router.post("/resolve-origin")
async def resolve_origin(
    request: Request,
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
    idemp = ensure_idempotency_key(idempotency_key)
    draft_id = _require_checkin_id(envelope.payload, "draft_id")
    message = _optional_string(envelope.payload, "message", max_len=4000)
    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    from promptpotter.application.datasets.origin_resolve import resolve_origin_turn

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
        return {"resolution": result.resolution, "draft": draft_wire_with_locks(result.draft)}

    def _on_replay() -> dict[str, Any]:
        # `cache.json::resolution` is byte-identical to the live turn's block, so a deduped
        # retry never re-spends the LLM call.
        bank = stores.checkin.load_bank(draft_id) or {}
        return {
            "resolution": bank.get("resolution") or {},
            "draft": _reread_draft_wire(stores, draft_id),
        }

    before = origin_projection(draft)
    dispatcher = CommandDispatcher(stores)
    outcome = await dispatcher.dispatch_checkin_command(
        kind="resolve-origin",
        campaign_id=draft_id,
        payload=envelope.payload,
        idempotency_key=idemp,
        applier=_apply,
        on_replay=_on_replay,
        effect_fn=lambda: _origin_effect(stores, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


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
    campaign_id = _require_checkin_id(envelope.payload, "campaign_id")

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
        payload=envelope.payload,
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
    raw_slug = _require_dataset_name(envelope.payload, "slug")
    dispatcher = CommandDispatcher(stores)
    outcome = await dispatcher.dispatch_workspace_command(
        kind="replace-dataset",
        payload={"slug": raw_slug},
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

    payload = envelope.payload
    job_registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    dispatcher = CommandDispatcher(stores, job_registry=job_registry)

    if kind in _WORKSPACE_SCOPED_KINDS:
        workspace_payload = _build_workspace_payload(kind, payload)
        workspace_kind: WorkspaceScopedKind = kind  # type: ignore[assignment]
        workspace_outcome = await dispatcher.dispatch_workspace_command(
            kind=workspace_kind,
            payload=workspace_payload,
            idempotency_key=idemp,
        )
        return workspace_outcome.accepted

    campaign_id = _require_string(payload, "campaign_id", max_len=128)

    if kind in _CAMPAIGN_CONFIG_KINDS:
        # In-place manifest edit — campaign-scoped, no cycle.
        config_payload: dict[str, Any]
        if kind == "set-allowed-models":
            allowed_raw = payload.get("allowed_models")
            if not isinstance(allowed_raw, list) or not all(
                isinstance(m, str) for m in allowed_raw
            ):
                raise PayloadInvalidError("payload.allowed_models must be a list of strings.")
            config_payload = {"allowed_models": allowed_raw}
        else:
            # Optional, not required: clearing the label restores the dataset-name
            # fallback the display chain already documents, so "" is a real value.
            config_payload = {"label": _optional_string(payload, "label", max_len=200).strip()}
        config_kind: CampaignConfigKind = kind  # type: ignore[assignment]
        config_outcome = await dispatcher.dispatch_campaign_config(
            kind=config_kind,
            campaign_id=campaign_id,
            payload=config_payload,
            idempotency_key=idemp,
        )
        return config_outcome.accepted

    if kind in _LIFECYCLE_KINDS:
        reason = _optional_string(payload, "reason", max_len=512)
        lifecycle_kind: LifecycleKind = kind  # type: ignore[assignment]
        # `keep_results` only meaningful for delete-campaign; harmless on the others.
        keep_results = bool(payload.get("keep_results", False))
        lifecycle_outcome = await dispatcher.dispatch_lifecycle(
            kind=lifecycle_kind,
            campaign_id=campaign_id,
            reason=reason,
            idempotency_key=idemp,
            keep_results=keep_results,
        )
        return lifecycle_outcome.accepted

    # Cycle-scoped. The kind-specific payload fields ride `extras`; the dispatcher's
    # `_build_cycle_applier` reads them per kind.
    cycle_id = _require_string(payload, "cycle_id", max_len=128)
    extras: dict[str, Any] = {}
    if kind == "fork-cycle":
        round_raw = payload.get("round", 0)
        if not isinstance(round_raw, int) or round_raw < 0:
            raise PayloadInvalidError("payload.round must be a non-negative integer.")
        extras["round"] = round_raw
        extras["candidate_id"] = _optional_string(payload, "candidate_id", max_len=128)
        # Required — every operator fork is `operator_steered`. The dispatcher validates it
        # into a typed `CycleSeed` (wire schema: m12-api-openapi.yaml::OperatorForkOverride).
        extras["seed"] = payload.get("seed")
        extras["steered_by"] = _optional_string(payload, "steered_by", max_len=256)
    elif kind in ("change-spend-budget", "origin-gate-decision", "set-sample-lookahead"):
        # Passed through, NOT validated here: `_build_cycle_applier` validates every
        # cycle-scoped kind's extras and is the seam the CLI reaches too. A second spelling
        # here re-derives the same rules and disagrees with them.
        extras.update(
            {
                k: payload[k]
                for k in ("max_usd", "max_tokens", "decision", "enabled")
                if k in payload
            }
        )
    elif kind == "start-run":
        kind_raw = payload.get("kind")
        if kind_raw not in ("new", "resume"):
            raise PayloadInvalidError("payload.kind must be 'new' or 'resume'.")
        extras["kind"] = kind_raw
        extras.update(_run_limits(payload))
    elif kind == "step-cycle":
        rounds_raw = payload.get("rounds", 1)
        if not isinstance(rounds_raw, int) or not (1 <= rounds_raw <= 100):
            raise PayloadInvalidError("payload.rounds must be an integer in [1, 100].")
        extras["rounds"] = rounds_raw

    cycle_kind: CycleScopedKind = kind  # type: ignore[assignment]
    cycle_outcome = await dispatcher.dispatch_cycle_command(
        kind=cycle_kind,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        payload_extras=extras,
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
