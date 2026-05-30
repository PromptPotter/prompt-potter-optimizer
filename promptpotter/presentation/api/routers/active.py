"""Active-session pointer + read-only cycle list.

Carries the read-only surface (``GET /active``, ``GET /cycles``,
``GET /optimizer/pipeline``, ``GET /evaluators_meta``). Cycle mutations
ride the closed-set command highway at ``POST /api/v1/commands/{kind}``
(``docs/specs/m12-api-openapi.yaml`` for the wire contract;
``presentation/api/routers/commands.py`` for the dispatch shell).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter.infrastructure.runtime_flags import is_paused, read_spend_cap
from promptpotter.infrastructure.store import (
    cycle_dir_for,
    read_active_pointer,
    root_cycle_id,
)
from promptpotter.presentation.api.deps import StoreDep

_active_router = APIRouter(tags=["Active"])

_OPTIMIZER_PIPELINE_PATH = (
    Path(__file__).resolve().parents[4] / "datasets" / "_optimizer" / "pipeline.json"
)


class ActiveSessionResponse(BaseModel):
    tenant_id: str = Field(description="Tenant the active session belongs to")
    session_id: str = Field(description="Active session id")
    campaign_id: str = Field(description="Active campaign id (pinned by the webapp)")
    cycle_id: str = Field(description="Active cycle id within the campaign")


@_active_router.get("/active", response_model=ActiveSessionResponse)
async def get_active_session(store: StoreDep) -> ActiveSessionResponse:
    """Return the caller-tenant's active-session pointer; 404 when none exists.

    Pointers are per-tenant on disk; the API never reads another tenant's
    state. Unauthed callers are rejected by ``resolve_identity`` before
    ``StoreDep`` resolves.
    """
    session_id, campaign_id, cycle_id = read_active_pointer(
        store.tenant_id, projects_root=store.projects_root
    )
    if not session_id:
        raise HTTPException(404, "No active session")
    return ActiveSessionResponse(
        tenant_id=store.tenant_id,
        session_id=session_id,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
    )


@_active_router.get("/live")
async def get_live_state(store: StoreDep) -> dict[str, Any]:
    """Live state of the caller-tenant's **active** session — the stable
    surface every new web panel / chat state-read codes against.

    Resolves the active pointer, then returns the session-family root's
    live telemetry (the same shape the per-cycle dashboard route serves).
    Keying on the active pointer means a consumer needs no campaign/cycle
    ids and no file path; it is insulated from how that telemetry is
    produced or persisted, so the eventual state-sync Phase 2/3 internals
    swap (derive-from-ledger) changes nothing here.

    404 when no session is active. While a fresh campaign's origin is still
    running (no telemetry flushed yet) returns a ``warming_up`` payload at
    200 so the panel can render an "initialising" placeholder rather than
    treat the session as offline.

    Beyond the dashboard telemetry, the payload carries two runtime facts the
    dashboard projection does not track (they are control-flow flags, not
    ledger events): ``is_paused`` (``.runtime/pause.flag`` present on the
    active cycle) and ``current_spend_cap_usd`` (the live cap from
    ``.runtime/spend_cap.json``, or ``null`` when uncapped). The run controls
    code against these rather than guessing pause-state from telemetry
    freshness — a paused runner emits no events, so liveness alone is blind.
    """
    session_id, campaign_id, cycle_id = read_active_pointer(
        store.tenant_id, projects_root=store.projects_root
    )
    if not session_id:
        raise HTTPException(404, "No active session")
    # Runtime flags live on the *active* cycle dir (where the runner writes
    # them + polls them), which may be a fork — distinct from the session
    # root that owns the shared dashboard.json. Shared readers in
    # ``infrastructure.runtime_flags`` so this matches the per-cycle /runstate.
    runtime_dir = cycle_dir_for(store.base_dir, campaign_id, cycle_id) / ".runtime"
    paused = is_paused(runtime_dir)
    current_spend_cap_usd = read_spend_cap(runtime_dir)

    session_root = root_cycle_id(cycle_id)
    path = cycle_dir_for(store.base_dir, campaign_id, session_root) / "dashboard.json"
    if not path.is_file():
        return {
            "warming_up": True,
            "session_id": session_id,
            "campaign_id": campaign_id,
            "cycle_id": session_root,
            "phase_hint": "origin",
            "is_paused": paused,
            "current_spend_cap_usd": current_spend_cap_usd,
        }
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    state["is_paused"] = paused
    state["current_spend_cap_usd"] = current_spend_cap_usd
    return state


class CycleListEntry(BaseModel):
    campaign_id: str = Field(description="Campaign the cycle belongs to")
    cycle_id: str
    parent_session_id: str = ""
    parent_cycle_id: str | None = Field(
        default=None,
        description="Immediate parent for siblings (forks/sweeps/diag); null for roots. Sidebar uses this to nest siblings.",
    )
    dataset_name: str = ""
    backend_id: str = ""
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    # Operator-facing unit kind — see _unit_kind() in campaign_store/cycles.py.
    unit_kind: Literal["session", "divergent_resume", "user_fork", "l3_fork"]
    is_root: bool
    status: str
    best_accuracy: float | None = None
    n_rounds: int = 0
    created_at: str = ""
    updated_at: str = ""


class CyclesResponse(BaseModel):
    tenant_id: str
    active_campaign_id: str | None = Field(
        description="Active campaign per active_session.json; null when no session is active."
    )
    active_cycle_id: str | None = Field(
        description="Active cycle per active_session.json; null when no session is active."
    )
    cycles: list[CycleListEntry]


@_active_router.get("/cycles", response_model=CyclesResponse)
async def get_cycles(store: StoreDep) -> CyclesResponse:
    """Every cycle on disk for the tenant + active pointer (one round-trip for the picker)."""
    _, active_cmp, active_cid = read_active_pointer(
        store.tenant_id, projects_root=store.projects_root
    )
    entries = store.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_campaign_id=active_cmp or None,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


@_active_router.get("/optimizer/pipeline")
async def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``optimizer_pipeline.json`` — nodes + pipelines + ``view`` topology for the webapp workflow."""
    pipeline: dict[str, Any] = json.loads(_OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))
    return pipeline


@_active_router.get("/evaluators_meta")
async def get_evaluators_meta() -> list[dict[str, Any]]:
    """Import-time evaluator registry (name/description/scope/direction/node_type) — feeds the What-If panel even when ``dashboard.json`` lacks ``cycle_info``."""
    from promptpotter.application.scoring.evaluators import evaluators_meta

    return evaluators_meta()


@_active_router.get("/llm/providers")
async def get_llm_providers() -> dict[str, Any]:
    """Available optimizer-LLM providers + a starter model list per provider.

    "Available" means the relevant API key is set in ``.env`` — the
    chat-first ingest UI uses this to surface a provider picker the
    operator can actually use, instead of letting them pick a provider
    that will fail at first call.

    The model list per provider is a curated starter set, not the full
    catalogue — provider model catalogues are huge and change weekly;
    operators who want a model not in the starter list can type it in
    directly.
    """
    from promptpotter.config.settings import settings

    providers = [
        {
            "name": "groq",
            "display_name": "Groq",
            "available": bool(settings.GROQ_API_KEY),
            "env_var": "GROQ_API_KEY",
            "models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "moonshotai/kimi-k2-instruct"],
            "note": "Free tier capped at 8000 TPM — L1 meta-prompt is ~20k tokens; "
            "use a paid Groq tier or pick OpenRouter/OpenAI for headroom.",
        },
        {
            "name": "openai",
            "display_name": "OpenAI",
            "available": bool(settings.OPENAI_API_KEY),
            "env_var": "OPENAI_API_KEY",
            "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini"],
            "note": "",
        },
        {
            "name": "openrouter",
            "display_name": "OpenRouter",
            "available": bool(settings.OPENROUTER_API_KEY),
            "env_var": "OPENROUTER_API_KEY",
            "models": [
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "anthropic/claude-sonnet-4.5",
                "mistralai/mistral-small-3.2-24b-instruct",
            ],
            "note": "Recommended for first-time tenants — broad model catalogue, high TPM headroom.",
        },
        {
            "name": "anthropic",
            "display_name": "Anthropic",
            "available": bool(settings.ANTHROPIC_API_KEY),
            "env_var": "ANTHROPIC_API_KEY",
            "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"],
            "note": "",
        },
    ]
    return {"providers": providers}


__all__ = [
    "ActiveSessionResponse",
    "CycleListEntry",
    "CyclesResponse",
]
