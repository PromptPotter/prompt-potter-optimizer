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
  when present, inline-applied): ``fork-cycle``,
  ``delete-cycle``, ``cleanup-empty-cycles``.
* Workspace-backend (no ``Expected-Version``, inline-applied;
  ``CommandRecord`` lands on the workspace ledger at
  ``projects/{tenant}/.workspace/events.jsonl``): ``register-backend``,
* Runtime-cooperative cycle commands, writing to the target cycle's
  ``.runtime/`` and re-read at the next checkpoint: ``pause-cycle``
  (``pause.flag``, the single operator-interrupt flag polled by
  ``Session.pause_check`` — the worker exits cleanly and resumes via the
  ``start-run``/``resume`` launcher), ``change-spend-budget``
  (``spend_cap.json`` ``{max_usd, max_tokens}``, re-read by the round
  loop's ``BudgetGate``).
* Launcher (workspace-scoped): ``mint-campaign`` mints a fresh
  campaign+cycle and spawns the runner via :class:`JobRegistry` in one
  inline-apply; ``start-run`` (cycle-scoped) launches the runner against
  an existing cycle with ``kind ∈ {new, resume}``.
* Check-in scoped (origin authoring): ``edit-draft-campaign``,
  ``resolve-origin``, ``start-checkin``. Typed routes rather than generic
  ``/{kind}`` arms because each answers a 200 domain object instead of a 202
  ``CommandAcceptedBody`` — but each dispatches through
  ``CommandDispatcher.dispatch_checkin_command`` onto the check-in cycle's
  ledger. A response shape must never again pick the ingress path: these
  three once applied inline, and nothing on disk recorded that an origin had
  been edited, or by whom.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any, cast, get_args

from fastapi import APIRouter, Header, Path, Request
from pydantic import Field

from promptpotter.application.datasets.csv_ingest import IngestError
from promptpotter.application.datasets.dataset_replace import (
    NothingToReplaceError,
    version_and_repoint,
)
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
from promptpotter.application.jobs.launcher.core import OriginIncompleteError
from promptpotter.application.jobs.launcher.draft_build import draft_wire_with_locks
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.connectors import BackendUnreachableError
from promptpotter.domain.origin_provenance import Provenance
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.middleware.command_dispatcher import (
    CampaignConfigKind,
    CommandAcceptedBody,
    CommandDispatcher,
    CycleScopedKind,
    LifecycleKind,
    WorkspaceBackendKind,
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

# Routing groups derived from the dispatcher's kind Literals (the SoT) — never
# re-authored here, so a new kind reaches the router the moment it joins its Literal.
_LIFECYCLE_KINDS: frozenset[str] = frozenset(get_args(LifecycleKind))
_CYCLE_SCOPED_KINDS: frozenset[str] = frozenset(get_args(CycleScopedKind))
_WORKSPACE_BACKEND_KINDS: frozenset[str] = frozenset(get_args(WorkspaceBackendKind))
_CAMPAIGN_CONFIG_KINDS: frozenset[str] = frozenset(get_args(CampaignConfigKind))
_WIRED_KINDS: frozenset[str] = (
    _LIFECYCLE_KINDS | _CYCLE_SCOPED_KINDS | _WORKSPACE_BACKEND_KINDS | _CAMPAIGN_CONFIG_KINDS
)


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


_DATASET_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def _require_dataset_name(payload: dict[str, Any], key: str = "dataset_name") -> str:
    """A dataset name under whichever key the wire gives it — ``dataset_name`` or,
    for ``replace-dataset``, ``slug``. One pattern, one message."""
    raw = _require_string(payload, key, max_len=64)
    if not _DATASET_NAME_PATTERN.fullmatch(raw):
        raise PayloadInvalidError(f"payload.{key} must match ^[a-z][a-z0-9_-]*$")
    return raw


def _optional_bounded_float(
    payload: dict[str, Any], key: str, *, lo: float, hi: float | None = None
) -> float | None:
    """``payload[key]`` as a float within ``[lo, hi]``, or ``None`` when absent.

    Out of range is a 422, never a silent omission: dropping the key let a run start
    with no spend cap and no halt threshold while the client got a 202.
    """
    raw = payload.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise PayloadInvalidError(f"payload.{key} must be a number")
    if raw < lo or (hi is not None and raw > hi):
        bound = f"between {lo} and {hi}" if hi is not None else f"at least {lo}"
        raise PayloadInvalidError(f"payload.{key} must be {bound}")
    return float(raw)


def _run_limits(payload: dict[str, Any]) -> dict[str, float]:
    """The two operator-set run limits, shared by ``mint-campaign`` and ``start-run``."""
    limits: dict[str, float] = {}
    halt = _optional_bounded_float(payload, "halt_at_accuracy", lo=0.0, hi=1.0)
    if halt is not None:
        limits["halt_at_accuracy"] = halt
    spend = _optional_bounded_float(payload, "spend_budget_usd", lo=0.0)
    if spend is not None:
        limits["spend_budget_usd"] = spend
    return limits


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
    # The campaign-config knobs (max_rounds / mechanisms) as one
    # object. Shallow-merged onto the draft's current overrides then validated
    # against OptimizationOverrides — so the editor can send one knob (e.g.
    # {"max_rounds": 8}) or several, and a nested `mechanisms` replaces wholesale.
    optimization_overrides: dict[str, Any] | None = None
    # The origin's target library. Set from the operator's upload or derived from one
    # of the draft's own columns (`routers/datasets.py`); both ride this patch.
    candidate_library: list[str] | None = Field(default=None, min_length=1)
    # The origin's sanctioned model allow-list (ticked in the check-in pipeline setup).
    # Replaces the draft's set wholesale — the checklist sends the full ticked list; an
    # empty list clears it (restrictive default). Not gated (config, like the connector).
    allowed_models: list[str] | None = None


# Every field the origin resolver may propose must be settable by this command —
# that correspondence is what lets a finding be rendered as a clickable
# `edit-draft-campaign` without the model ever naming a command. Fails at import,
# not at the operator's click.
assert set(FINDING_PATCH_KEYS.values()) <= set(_EditDraftPatch.model_fields), (
    f"resolver proposes fields edit-draft-campaign cannot patch: "
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


def _reread_draft(store: Stores, draft_id: str) -> DraftCampaign:
    draft = load_checkin_draft(store, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return draft


def _reread_draft_wire(store: Stores, draft_id: str) -> dict[str, Any]:
    """The post-mutation draft, re-read from ``draft.json`` — the response body for
    a deduped ``Idempotency-Key`` retry, whose first attempt already persisted it."""
    return draft_wire_with_locks(_reread_draft(store, draft_id))


def _origin_effect(store: Stores, draft_id: str, before: dict[str, Any]) -> dict[str, Any]:
    """What the applier moved in the origin, diffed against its pre-apply projection.

    Recorded on the ack because the ``CommandRecord.payload`` states only what was
    *asked* for — a ``resolve-origin`` payload names the draft and nothing else.
    """
    return origin_delta(before, origin_projection(_reread_draft(store, draft_id)))


async def dispatch_draft_patch(
    store: Stores,
    *,
    draft_id: str,
    patch_raw: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Validate a sparse draft patch, apply it through `CommandDispatcher`, return
    the post-mutation draft wire.

    The single write path behind `edit-draft-campaign`. The candidate-library routes
    (`routers/datasets.py`) derive their patch from a multipart upload or one of the
    draft's own columns and then ride this — an origin edit is a `CommandRecord`
    whatever the ingress looked like.
    """
    patch = _EditDraftPatch.model_validate(patch_raw)

    draft = load_checkin_draft(store, draft_id)
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
        (patch.pipeline_overlay, "pipeline_overlay"),
        (patch.origin_prompt_fields, "origin_prompt_fields"),
        (patch.pipeline_steps, "pipeline_steps"),
        (patch.candidate_library, "candidate_library"),
        (patch.allowed_models, "allowed_models"),
    ):
        if patch_val is not None:
            changes[draft_attr] = patch_val

    # Campaign-config knobs: shallow-merge the patched keys onto the draft's
    # current overrides (so one knob can change without resetting the rest), then
    # validate the result against OptimizationOverrides (rejects unknown keys /
    # out-of-range max_rounds / malformed mechanisms) and store the dumped shape.
    if patch.optimization_overrides is not None:
        merged = {**draft.optimization_overrides, **patch.optimization_overrides}
        changes["optimization_overrides"] = OptimizationOverrides.model_validate(merged).model_dump(
            mode="json"
        )

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

    def _apply() -> dict[str, Any]:
        updated = draft.apply_resolution(values=changes, provenance=provenance)
        if patch.column_query is not None or patch.column_ground_truth is not None:
            updated = updated.confirm_columns(
                query_col=patch.column_query, ground_truth_col=patch.column_ground_truth
            )
        save_checkin_draft(store, updated)
        return draft_wire_with_locks(updated)

    before = origin_projection(draft)
    dispatcher = CommandDispatcher(store)
    outcome = await dispatcher.dispatch_checkin_command(
        kind="edit-draft-campaign",
        campaign_id=draft_id,
        payload={"draft_id": draft_id, "patch": patch_raw},
        idempotency_key=idempotency_key,
        applier=_apply,
        on_replay=lambda: _reread_draft_wire(store, draft_id),
        effect_fn=lambda: _origin_effect(store, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


@commands_router.post("/edit-draft-campaign")
async def edit_draft_campaign(
    store: StoreDep,
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
        store,
        draft_id=draft_id,
        patch_raw=patch_raw,
        idempotency_key=idemp,
    )


@commands_router.post("/resolve-origin")
async def resolve_origin(
    request: Request,
    store: StoreDep,
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
    draft = load_checkin_draft(store, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")

    from promptpotter.application.datasets.origin_resolve import resolve_origin_turn

    async def _apply() -> dict[str, Any]:
        try:
            result = await resolve_origin_turn(stores=store, draft=draft, message=message)
        except PotterError:
            raise
        except Exception as exc:
            # A PotterError so the dispatcher's mapping seam emits a `rejected` ack
            # and re-raises as 502, rather than the generic 409 the bare-Exception
            # arm produces.
            logger.exception("resolve-origin turn failed for draft %s", draft_id)
            raise ServiceUnavailableError(
                f"origin resolver turn failed: {exc}", code="resolver_failed"
            ) from exc
        return {"resolution": result.resolution, "draft": draft_wire_with_locks(result.draft)}

    def _on_replay() -> dict[str, Any]:
        # `cache.json::resolution` is byte-identical to the block the live turn
        # returns, so a deduped retry never re-spends the LLM call.
        bank = store.checkin.load_bank(draft_id) or {}
        return {
            "resolution": bank.get("resolution") or {},
            "draft": _reread_draft_wire(store, draft_id),
        }

    before = origin_projection(draft)
    dispatcher = CommandDispatcher(store)
    outcome = await dispatcher.dispatch_checkin_command(
        kind="resolve-origin",
        campaign_id=draft_id,
        payload=envelope.payload,
        idempotency_key=idemp,
        applier=_apply,
        on_replay=_on_replay,
        effect_fn=lambda: _origin_effect(store, draft_id, before),
    )
    return cast("dict[str, Any]", outcome.result)


@commands_router.post("/start-checkin")
async def start_checkin(
    request: Request,
    store: StoreDep,
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

    draft = load_checkin_draft(store, campaign_id)
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
                stores=store,
                job_registry=job_registry,
                campaign_id=campaign_id,
            )
        except OriginIncompleteError:
            # The deterministic origin-readiness checklist still has gaps — the
            # check-in is preserved (lifecycle stays ``checkin``); the operator
            # resolves them (edit-draft-campaign) and retries. The exception already
            # carries code=origin_incomplete + details.gaps; refresh the breadcrumb.
            save_checkin_draft(store, draft)
            raise
        except IngestError as exc:
            # A confirmed column mapping still hit a per-row data failure at
            # materialization (e.g. a blank input/target cell). Clean 422.
            raise PayloadInvalidError(
                exc.message, code="ingest_failed", details={"reason": exc.reason}
            ) from exc
        except BackendUnreachableError as exc:
            # Preflight ran before any irreversible write → the check-in is preserved;
            # the operator can fix the backend and retry without re-authoring. Augment
            # the exception's own backend_type/url details with the campaign id.
            exc.details["campaign_id"] = campaign_id
            raise
        # LaunchError (not-owned / not-in-check-in / rare slug-collision-at-Start) is a
        # PayloadInvalidError → the central PotterError handler maps it to 422 with its
        # own message; no per-case arm here.
        return {"campaign_id": campaign_id, "cycle_id": job.cycle_id, "job_id": job.job_id}

    dispatcher = CommandDispatcher(store, job_registry=job_registry)
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
    store: StoreDep,
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
    ensure_idempotency_key(idempotency_key)
    raw_slug = _require_dataset_name(envelope.payload, "slug")
    try:
        result = version_and_repoint(stores=store, slug=raw_slug)
    except NothingToReplaceError as exc:
        raise ConflictError(
            str(exc), code="nothing_to_replace", details={"slug": exc.slug}
        ) from exc
    # Echo the subject, nothing more: the migration's counts + the versioned slug are
    # recorded by `version_and_repoint` itself (its log line + the on-disk marker), and
    # no caller ever read them off the wire.
    return {"slug": result.slug}


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
    dispatcher = CommandDispatcher(store, job_registry=job_registry)

    if kind in _WORKSPACE_BACKEND_KINDS:
        workspace_payload = _build_workspace_payload(kind, payload)
        workspace_kind: WorkspaceBackendKind = kind  # type: ignore[assignment]
        workspace_outcome = await dispatcher.dispatch_workspace_command(
            kind=workspace_kind,
            payload=workspace_payload,
            idempotency_key=idemp,
        )
        return workspace_outcome.accepted

    campaign_id = _require_string(payload, "campaign_id", max_len=128)

    if kind in _CAMPAIGN_CONFIG_KINDS:
        # In-place campaign config edit (set-allowed-models) — campaign-scoped, no cycle.
        allowed_raw = payload.get("allowed_models")
        if not isinstance(allowed_raw, list) or not all(isinstance(m, str) for m in allowed_raw):
            raise PayloadInvalidError("payload.allowed_models must be a list of strings.")
        config_kind: CampaignConfigKind = kind  # type: ignore[assignment]
        config_outcome = await dispatcher.dispatch_campaign_config(
            kind=config_kind,
            campaign_id=campaign_id,
            allowed_models=allowed_raw,
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
    elif kind in ("change-spend-budget", "origin-gate-decision"):
        # Passed through, NOT validated here: `_build_cycle_applier` validates every
        # cycle-scoped kind's extras, and it is the seam the CLI reaches too. A second
        # spelling here re-derived the same rules and disagreed with them — it rejected
        # a negative max_usd that the dispatcher's `(usd < 0) and (tok < 0)` let through.
        extras.update(
            {k: payload[k] for k in ("max_usd", "max_tokens", "decision") if k in payload}
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


__all__ = ["CommandEnvelope", "commands_router"]
