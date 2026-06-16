"""``POST /commands/{kind}`` — closed inbound command surface.

The HTTP-side shell: parse the envelope, enforce the trust-boundary
headers (``Idempotency-Key`` always; ``Expected-Version`` when supplied
on cycle-scoped commands — v0 relaxation per the YAML), delegate to
``CommandDispatcher``. Per the m12-api-openapi.yaml closed set: every
wired kind is declared in the YAML *before* a handler lands here.

Wired kinds, by scope:

* Workspace-scoped lifecycle (no ``Expected-Version``, inline-applied):
  ``archive-campaign``, ``delete-campaign``, ``unarchive-campaign``.
* Cycle-scoped sanctioned-POST migrations (``Expected-Version`` validated
  when present, inline-applied): ``fork-cycle``, ``stop-cycle``,
  ``delete-cycle``, ``cleanup-empty-cycles``.
* Workspace-backend (no ``Expected-Version``, inline-applied;
  ``CommandRecord`` lands on the workspace ledger at
  ``projects/{tenant}/.workspace/events.jsonl``): ``register-backend``,
  ``sync-backend-experiments``.
* Runtime-cooperative cycle commands, writing to the target cycle's
  ``.runtime/`` and re-read at the round boundary: ``pause-cycle`` +
  ``resume-cycle`` (``pause.flag``, polled by ``Session.pause_check`` —
  ``stop.flag`` overrides), ``change-spend-budget``
  (``spend_cap.json`` ``{max_usd, max_tokens}``, re-read by the round
  loop's ``BudgetGate``).
* Launcher (workspace-scoped): ``mint-campaign`` mints a fresh
  campaign+cycle and spawns the runner via :class:`JobRegistry` in one
  inline-apply; ``start-run`` (cycle-scoped) launches the runner against
  an existing cycle with ``kind ∈ {new, resume}``.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.datasets.csv_ingest import IngestError
from promptpotter.application.datasets.dataset_replace import (
    NothingToReplaceError,
    version_and_repoint,
)
from promptpotter.application.datasets.origin_readiness import resolution_block
from promptpotter.application.jobs import JobRegistry
from promptpotter.application.jobs.launcher import (
    LaunchError,
    OriginIncompleteError,
    draft_wire_with_locks,
    mint_campaign_from_draft_command,
)
from promptpotter.connectors import BackendUnreachableError
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.presentation.api.deps import StoreDep, get_draft_registry
from promptpotter.presentation.api.middleware import CommandAcceptedBody, CommandDispatcher
from promptpotter.presentation.api.middleware.command_dispatcher import (
    CycleScopedKind,
    LifecycleKind,
    WorkspaceBackendKind,
)
from promptpotter.shared.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    PayloadInvalidError,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)

commands_router = APIRouter(prefix="/commands", tags=["Commands"])

_LIFECYCLE_KINDS: frozenset[str] = frozenset(
    {"archive-campaign", "delete-campaign", "unarchive-campaign"}
)
_CYCLE_SCOPED_KINDS: frozenset[str] = frozenset(
    {
        "fork-cycle",
        "stop-cycle",
        "delete-cycle",
        "cleanup-empty-cycles",
        "pause-cycle",
        "resume-cycle",
        "origin-gate-decision",
        "change-spend-budget",
        "start-run",
    }
)
_WORKSPACE_BACKEND_KINDS: frozenset[str] = frozenset(
    {"register-backend", "sync-backend-experiments", "mint-campaign"}
)
_WIRED_KINDS: frozenset[str] = _LIFECYCLE_KINDS | _CYCLE_SCOPED_KINDS | _WORKSPACE_BACKEND_KINDS


class CommandEnvelope(BaseModel):
    """Inbound envelope per ``m12-api-openapi.yaml#/components/schemas/CommandEnvelope``."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


def _ensure_idempotency_key(header_value: str | None) -> str:
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


_DATASET_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def _require_dataset_name(payload: dict[str, Any]) -> str:
    raw = _require_string(payload, "dataset_name", max_len=64)
    if not _DATASET_NAME_PATTERN.fullmatch(raw):
        raise PayloadInvalidError("payload.dataset_name must match ^[a-z][a-z0-9_-]*$")
    return raw


def _build_workspace_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + project the workspace payload per the YAML schema.

    The returned dict lands on the workspace-ledger ``CommandRecord``;
    keep keys minimal + lossless so the audit trail mirrors the request."""
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
    if kind == "mint-campaign":
        mint_out: dict[str, Any] = {"dataset_name": _require_dataset_name(payload)}
        halt = payload.get("halt_at_accuracy")
        if isinstance(halt, int | float) and 0.0 <= halt <= 1.0:
            mint_out["halt_at_accuracy"] = float(halt)
        spend = payload.get("spend_budget_usd")
        if isinstance(spend, int | float) and spend >= 0.0:
            mint_out["spend_budget_usd"] = float(spend)
        return mint_out
    # sync-backend-experiments
    return {"backend_id": _require_slug(payload, "backend_id", max_len=64)}


class _EditDraftPatch(BaseModel):
    """Sparse mutation payload — only declared fields ride through."""

    model_config = ConfigDict(extra="forbid")

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    scoring_composite: str | None = Field(default=None, min_length=1, max_length=64)
    max_rounds: int | None = Field(default=None, ge=1, le=100)
    raw_task_description: str | None = Field(default=None, max_length=16384)
    pipeline_overlay: dict[str, Any] | None = None
    # The active pipeline step list (e.g. ["llm_only"] vs the full
    # cache_lookup→…→token_matching). The setup-panel mode toggle writes it;
    # commit's `_build_origin_pipeline_json` + `derive_optimizer_locks` read it.
    pipeline_steps: list[str] | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Operator edits to the campaign's origin prompt (PromptTemplate field
    # shape: the six string fields + optional few_shot_examples). Replaces the
    # draft's origin_prompt_fields wholesale — the editor sends the full object.
    origin_prompt_fields: dict[str, Any] | None = None
    # Whether the optimizer is barred from mutating model/provider (the
    # forbidden_axes_strict knob). The pipeline-config control panel toggles it.
    lock_model: bool | None = None
    # Audit marker stamped when an agent simulates the check-in node (authors the
    # origin decomposition by hand instead of spending the LLM call) — see
    # `DraftCampaign.simulated_checkin`. Free dict `{by, model, at}`; lands in the
    # resolution block. Empty/absent on a real check-in.
    simulated_checkin: dict[str, Any] | None = None


class _EditDraftEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^edit-draft-campaign$")
    payload: dict[str, Any] = Field(default_factory=dict)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class _MintFromDraftEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^mint-campaign-from-draft$")
    payload: dict[str, Any] = Field(default_factory=dict)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class _ResolveOriginEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^resolve-origin$")
    payload: dict[str, Any] = Field(default_factory=dict)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


class _ReplaceDatasetEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^replace-dataset$")
    payload: dict[str, Any] = Field(default_factory=dict)
    client_metadata: dict[str, Any] = Field(default_factory=dict)


def _require_draft_id(payload: dict[str, Any]) -> str:
    raw = payload.get("draft_id")
    if not isinstance(raw, str) or not raw or len(raw) > 64 or len(raw) < 8:
        raise PayloadInvalidError("payload.draft_id is required (8-64 chars).")
    return raw


@commands_router.post("/edit-draft-campaign")
async def edit_draft_campaign(
    request: Request,
    store: StoreDep,
    envelope: _EditDraftEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Sparse-patch a `DraftCampaign`. Returns the post-mutation full shape.

    Per ``docs/specs/m12-api-openapi.yaml::editDraftCampaign`` — declared
    here as a typed endpoint (not via the generic dispatcher) because the
    response is a `DraftCampaign`, not a `CommandAcceptedBody`.
    """
    _ensure_idempotency_key(idempotency_key)
    registry = get_draft_registry(request)

    raw_payload = envelope.payload
    draft_id = _require_draft_id(raw_payload)
    patch_raw = raw_payload.get("patch", {})
    if not isinstance(patch_raw, dict):
        raise PayloadInvalidError("payload.patch must be an object.")
    patch = _EditDraftPatch.model_validate(patch_raw)

    draft = registry.get(draft_id, tenant_id=store.identity.tenant_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    changes: dict[str, Any] = {}
    provenance: dict[str, Provenance] = {}
    if patch.slug is not None and patch.slug != draft.slug:
        if store.tenant_datasets.slug_exists(patch.slug):
            suggested = store.tenant_datasets.suggest_free_slug(patch.slug)
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
        (patch.max_rounds, "max_rounds"),
        (patch.pipeline_overlay, "pipeline_overlay"),
        (patch.origin_prompt_fields, "origin_prompt_fields"),
        (patch.lock_model, "lock_model"),
        (patch.simulated_checkin, "simulated_checkin"),
        (patch.pipeline_steps, "pipeline_steps"),
    ):
        if patch_val is not None:
            changes[draft_attr] = patch_val

    # The task framing IS gated — an operator edit CONFIRMS it, which is what
    # opens the origin-readiness gate for a field the resolver left PROPOSED or
    # that started UNSET. The checklist field-id stays `task_description`.
    if patch.raw_task_description is not None:
        changes["raw_task_description"] = patch.raw_task_description
        provenance["task_description"] = Provenance.CONFIRMED

    # Column mapping confirms the input/target headers — each must be a member
    # of the uploaded headers (422 otherwise), and confirming flips the
    # field's provenance so the origin-readiness gate opens.
    for label, col in (
        ("column_query", patch.column_query),
        ("column_ground_truth", patch.column_ground_truth),
    ):
        if col is not None and col not in draft.headers:
            raise PayloadInvalidError(
                f"patch.{label} {col!r} is not one of the uploaded headers {list(draft.headers)}."
            )

    updated = draft.apply_resolution(values=changes, provenance=provenance)
    if patch.column_query is not None or patch.column_ground_truth is not None:
        updated = updated.confirm_columns(
            query_col=patch.column_query, ground_truth_col=patch.column_ground_truth
        )
    registry.update(updated)
    store.tenant_datasets.write_draft_resolution(updated.draft_id, resolution_block(updated))
    return draft_wire_with_locks(updated)


@commands_router.post("/resolve-origin")
async def resolve_origin(
    request: Request,
    store: StoreDep,
    envelope: _ResolveOriginEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Run one origin-resolver turn against a draft. Returns ``{resolution, draft}``.

    Per ``docs/specs/m12-api-openapi.yaml::resolveOrigin``. Synchronous, like
    ``edit-draft-campaign`` — the resolver's findings apply in-line and the
    deterministic checklist re-gates before the response.
    """
    _ensure_idempotency_key(idempotency_key)
    registry = get_draft_registry(request)
    draft_id = _require_draft_id(envelope.payload)
    draft = registry.get(draft_id, tenant_id=store.identity.tenant_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    from promptpotter.application.datasets.origin_resolve import resolve_origin_turn

    try:
        result = await resolve_origin_turn(stores=store, draft=draft, draft_registry=registry)
    except Exception as exc:  # external LLM boundary; surface a clean 502
        logger.exception("resolve-origin turn failed for draft %s", draft_id)
        raise ServiceUnavailableError(
            f"origin resolver turn failed: {exc}", code="resolver_failed"
        ) from exc

    return {"resolution": result.resolution, "draft": draft_wire_with_locks(result.draft)}


@commands_router.post("/mint-campaign-from-draft")
async def mint_campaign_from_draft(
    request: Request,
    store: StoreDep,
    envelope: _MintFromDraftEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Commit a draft + mint the campaign. Synchronous; returns ``{campaign_id, cycle_id, job_id}``.

    Per ``docs/specs/m12-api-openapi.yaml::mintCampaignFromDraft``.
    """
    _ensure_idempotency_key(idempotency_key)
    registry = get_draft_registry(request)
    draft_id = _require_draft_id(envelope.payload)
    draft = registry.get(draft_id, tenant_id=store.identity.tenant_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    job_registry: JobRegistry | None = getattr(request.app.state, "job_registry", None)
    if job_registry is None:
        raise ServiceUnavailableError(
            "job registry not initialised", code="job_registry_unavailable"
        )

    try:
        campaign_id, cycle_id, job = await mint_campaign_from_draft_command(
            stores=store,
            draft=draft,
            draft_registry=registry,
            job_registry=job_registry,
        )
    except OriginIncompleteError:
        # The deterministic origin-readiness checklist still has gaps — the
        # draft is preserved; the operator resolves them (edit-draft-campaign)
        # and retries. The exception already carries code=origin_incomplete +
        # details.gaps (it's a PotterError); persist the resolution then re-raise.
        store.tenant_datasets.write_draft_resolution(draft.draft_id, resolution_block(draft))
        raise
    except IngestError as exc:
        # A confirmed column mapping still hit a per-row data failure at
        # materialization (e.g. a blank input/target cell). Clean 422.
        raise PayloadInvalidError(
            exc.message, code="ingest_failed", details={"reason": exc.reason}
        ) from exc
    except BackendUnreachableError as exc:
        # Preflight ran before commit_draft → draft is preserved; the operator
        # can fix the backend and retry without re-uploading. Augment the
        # exception's own backend_type/url details with the draft id, then re-raise.
        exc.details["draft_id"] = draft.draft_id
        raise
    except LaunchError as exc:
        # Slug collision at commit — surface a free suggestion as a 409.
        suggested = store.tenant_datasets.suggest_free_slug(draft.slug)
        raise ConflictError(
            str(exc), code="slug_collision", details={"suggested_slug": suggested}
        ) from exc
    return {"campaign_id": campaign_id, "cycle_id": cycle_id, "job_id": job.job_id}


@commands_router.post("/replace-dataset")
async def replace_dataset(
    request: Request,
    store: StoreDep,
    envelope: _ReplaceDatasetEnvelope,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Version-and-repoint a colliding dataset so its name frees for new data.

    Per ``docs/specs/m12-api-openapi.yaml::replaceDataset``. Data-safe: never overwrites — the
    old data + every prior campaign's results are preserved under ``{slug}-vN``.
    Synchronous (the migration is a bounded set of renames + JSON rewrites); the
    freed name is re-ingested in a separate ``/datasets/ingest`` call.
    """
    _ensure_idempotency_key(idempotency_key)
    raw_slug = _require_string(envelope.payload, "slug", max_len=64)
    if not _DATASET_NAME_PATTERN.fullmatch(raw_slug):
        raise PayloadInvalidError("payload.slug must match ^[a-z][a-z0-9_-]*$")
    try:
        result = version_and_repoint(stores=store, slug=raw_slug)
    except NothingToReplaceError as exc:
        raise ConflictError(
            str(exc), code="nothing_to_replace", details={"slug": exc.slug}
        ) from exc
    return {
        "slug": result.slug,
        "versioned_to": result.versioned_to,
        "repointed_campaigns": result.repointed_campaigns,
        "restamped_measurements": result.restamped_measurements,
    }


@commands_router.post("/{kind}", response_model=CommandAcceptedBody, status_code=202)
async def post_command(
    request: Request,
    store: StoreDep,
    envelope: CommandEnvelope,
    kind: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]*$", max_length=64)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    expected_version: Annotated[int | None, Header(alias="Expected-Version")] = None,
) -> CommandAcceptedBody:
    """Closed-set command surface — every wired kind validates against the
    declared schema in ``m12-api-openapi.yaml`` and dispatches through
    ``CommandDispatcher``."""
    idemp = _ensure_idempotency_key(idempotency_key)
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
    dispatcher = CommandDispatcher(store, job_registry=job_registry)

    if kind in _WORKSPACE_BACKEND_KINDS:
        workspace_payload = _build_workspace_payload(kind, payload)
        workspace_kind: WorkspaceBackendKind = kind  # type: ignore[assignment]
        return await dispatcher.dispatch_workspace_command(
            kind=workspace_kind,
            payload=workspace_payload,
            idempotency_key=idemp,
            client_metadata=envelope.client_metadata,
        )

    campaign_id = _require_string(payload, "campaign_id", max_len=128)

    if kind in _LIFECYCLE_KINDS:
        reason = _optional_string(payload, "reason", max_len=512)
        lifecycle_kind: LifecycleKind = kind  # type: ignore[assignment]
        return await dispatcher.dispatch_lifecycle(
            kind=lifecycle_kind,
            campaign_id=campaign_id,
            reason=reason,
            idempotency_key=idemp,
            client_metadata=envelope.client_metadata,
        )

    # Cycle-scoped — fork / stop / delete / cleanup-empty / pause / resume /
    # change-spend-budget. The kind-specific payload fields ride `extras`;
    # the dispatcher's `_build_cycle_applier` reads them per kind.
    cycle_id = _require_string(payload, "cycle_id", max_len=128)
    extras: dict[str, Any] = {}
    if kind == "fork-cycle":
        round_raw = payload.get("round", 0)
        if not isinstance(round_raw, int) or round_raw < 0:
            raise PayloadInvalidError("payload.round must be a non-negative integer.")
        extras["round"] = round_raw
        extras["candidate_id"] = _optional_string(payload, "candidate_id", max_len=128)
        # Operator-steered seed (edited searchpoint + reconciled limit overrides)
        # + the editor's identity. The seed is required — every operator fork is
        # `operator_steered`. The dispatcher validates it into a typed `CycleSeed`
        # (wire schema: m12-api-openapi.yaml::OperatorForkOverride).
        extras["seed"] = payload.get("seed")
        extras["steered_by"] = _optional_string(payload, "steered_by", max_len=256)
    elif kind == "change-spend-budget":
        max_usd_raw = payload.get("max_usd")
        max_tokens_raw = payload.get("max_tokens")
        if max_usd_raw is not None:
            if not isinstance(max_usd_raw, int | float) or max_usd_raw < 0:
                raise PayloadInvalidError("payload.max_usd must be a non-negative number.")
            extras["max_usd"] = float(max_usd_raw)
        if max_tokens_raw is not None:
            if (
                not isinstance(max_tokens_raw, int)
                or isinstance(max_tokens_raw, bool)
                or max_tokens_raw < 0
            ):
                raise PayloadInvalidError("payload.max_tokens must be a non-negative integer.")
            extras["max_tokens"] = int(max_tokens_raw)
        if "max_usd" not in extras and "max_tokens" not in extras:
            raise PayloadInvalidError(
                "change-spend-budget requires at least one of max_usd / max_tokens."
            )
    elif kind == "origin-gate-decision":
        decision = payload.get("decision")
        if decision not in ("rescore", "proceed", "abort"):
            raise PayloadInvalidError(
                "payload.decision must be one of 'rescore', 'proceed', 'abort'."
            )
        extras["decision"] = decision
    elif kind == "start-run":
        kind_raw = payload.get("kind")
        if kind_raw not in ("new", "resume"):
            raise PayloadInvalidError("payload.kind must be 'new' or 'resume'.")
        extras["kind"] = kind_raw
        halt = payload.get("halt_at_accuracy")
        if isinstance(halt, int | float) and 0.0 <= halt <= 1.0:
            extras["halt_at_accuracy"] = float(halt)
        spend = payload.get("spend_budget_usd")
        if isinstance(spend, int | float) and spend >= 0.0:
            extras["spend_budget_usd"] = float(spend)

    cycle_kind: CycleScopedKind = kind  # type: ignore[assignment]
    return await dispatcher.dispatch_cycle_command(
        kind=cycle_kind,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        payload_extras=extras,
        idempotency_key=idemp,
        expected_version=expected_version,
        client_metadata=envelope.client_metadata,
    )


__all__ = ["CommandEnvelope", "commands_router"]
