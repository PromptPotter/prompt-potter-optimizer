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
  (``spend_cap.json``, re-read by the ``spend_cap_probe``).
* Launcher (workspace-scoped): ``mint-campaign`` mints a fresh
  campaign+cycle and spawns the runner via :class:`JobRegistry` in one
  inline-apply; ``start-run`` (cycle-scoped) launches the runner against
  an existing cycle with ``kind ∈ {new, resume}``.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from promptpotter.application.jobs import JobRegistry
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.middleware import CommandAcceptedBody, CommandDispatcher
from promptpotter.presentation.api.middleware.command_dispatcher import (
    CycleScopedKind,
    LifecycleKind,
    WorkspaceBackendKind,
)

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
        raise HTTPException(
            400,
            {
                "error": "idempotency_key_missing",
                "message": "Idempotency-Key header is required on every command.",
            },
        )
    return header_value.strip()


def _require_string(payload: dict[str, Any], key: str, *, max_len: int) -> str:
    raw = payload.get(key, "")
    if not isinstance(raw, str) or not raw:
        raise HTTPException(
            422,
            {
                "error": "payload_invalid",
                "message": f"payload.{key} is required and must be a non-empty string.",
            },
        )
    if len(raw) > max_len:
        raise HTTPException(
            422,
            {"error": "payload_invalid", "message": f"payload.{key} exceeds {max_len} chars."},
        )
    return raw


def _optional_string(payload: dict[str, Any], key: str, *, max_len: int) -> str:
    raw = payload.get(key, "")
    if not isinstance(raw, str):
        raise HTTPException(
            422,
            {"error": "payload_invalid", "message": f"payload.{key} must be a string."},
        )
    if len(raw) > max_len:
        raise HTTPException(
            422,
            {"error": "payload_invalid", "message": f"payload.{key} exceeds {max_len} chars."},
        )
    return raw


_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


def _require_slug(payload: dict[str, Any], key: str, *, max_len: int) -> str:
    """Slug-validated required field per the OpenAPI pattern ``^[a-z][a-z0-9-]*$``."""
    raw = _require_string(payload, key, max_len=max_len)
    if not _SLUG_PATTERN.fullmatch(raw):
        raise HTTPException(
            422,
            {
                "error": "payload_invalid",
                "message": f"payload.{key} must match ^[a-z][a-z0-9-]*$",
            },
        )
    return raw


_DATASET_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def _require_dataset_name(payload: dict[str, Any]) -> str:
    raw = _require_string(payload, "dataset_name", max_len=64)
    if not _DATASET_NAME_PATTERN.fullmatch(raw):
        raise HTTPException(
            422,
            {
                "error": "payload_invalid",
                "message": "payload.dataset_name must match ^[a-z][a-z0-9_-]*$",
            },
        )
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
        raise HTTPException(
            422,
            {
                "error": "payload_invalid",
                "message": f"path kind {kind!r} does not match envelope.kind {envelope.kind!r}",
            },
        )
    if kind not in _WIRED_KINDS:
        raise HTTPException(
            404,
            {
                "error": "command_kind_unknown",
                "message": (
                    f"Command kind {kind!r} not wired. See "
                    f"docs/specs/m12-api-openapi.yaml for the declared set."
                ),
            },
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
            raise HTTPException(
                422,
                {
                    "error": "payload_invalid",
                    "message": "payload.round must be a non-negative integer.",
                },
            )
        extras["round"] = round_raw
        extras["candidate_id"] = _optional_string(payload, "candidate_id", max_len=128)
    elif kind == "change-spend-budget":
        max_usd_raw = payload.get("max_usd")
        if not isinstance(max_usd_raw, int | float) or max_usd_raw < 0:
            raise HTTPException(
                422,
                {
                    "error": "payload_invalid",
                    "message": "payload.max_usd must be a non-negative number.",
                },
            )
        extras["max_usd"] = float(max_usd_raw)
    elif kind == "start-run":
        kind_raw = payload.get("kind")
        if kind_raw not in ("new", "resume"):
            raise HTTPException(
                422,
                {
                    "error": "payload_invalid",
                    "message": "payload.kind must be 'new' or 'resume'.",
                },
            )
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
