"""Active-session pointer + sanctioned mutating control endpoints.

Read-only surface for the webapp's active campaign/cycle pin + the
sanctioned mutators (operator fork via ``CycleEventLog.inherit_from``, stop
flag via ``.runtime/stop.flag``, stub-cycle DELETE). All ride existing I/O
kinds — no new kind. Cycle id is campaign-scoped, so routes carry ``(campaign_id, cycle_id)``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import read_active_pointer, save_active_pointer
from promptpotter.infrastructure.store.base import write_json
from promptpotter.infrastructure.store.paths import root_cycle_id
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
async def get_active_session() -> ActiveSessionResponse:
    """Return the active-session pointer; 404 when no session is active."""
    tenant_id, session_id, campaign_id, cycle_id = read_active_pointer()
    if not tenant_id:
        raise HTTPException(404, "No active session")
    return ActiveSessionResponse(
        tenant_id=tenant_id,
        session_id=session_id,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
    )


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
    _, _, active_cmp, active_cid = read_active_pointer()
    entries = store.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_campaign_id=active_cmp or None,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


# Sanctioned mutating endpoints (charter: presentation/CLAUDE.md) — all ride
# existing I/O kinds, no new kind introduced.


class CreateForkRequest(BaseModel):
    round: int = Field(description="Round the operator endorsed (audit-trail only in the MVP)")
    candidate_id: str = Field(
        default="", description="Candidate the operator endorsed (audit-trail only in the MVP)"
    )


class CreateForkResponse(BaseModel):
    campaign_id: str
    fork_cycle_id: str
    cli_command: str = Field(
        description="Launch the fork; active pointer has been retargeted so bare `resume` picks it up."
    )
    active_pointer_retargeted: bool = True


@_active_router.post(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/forks",
    response_model=CreateForkResponse,
)
async def create_fork(
    campaign_id: str,
    cycle_id: str,
    request: CreateForkRequest,
    store: StoreDep,
) -> CreateForkResponse:
    """Operator-initiated fork via the lineage inspector — endorse path.

    Fork inherits ledger up to parent's current ``next_offset`` ("fork from
    now, endorse the parent's present state"). ``round``+``candidate_id`` go
    to ``index.json::fork`` for audit (no offset selection yet). Same
    campaign; active pointer retargeted so ``resume`` picks it up.
    """
    parent_dir = store.campaigns.cycle_dir(campaign_id, cycle_id)
    parent_index_path = parent_dir / "index.json"
    if not parent_index_path.is_file():
        raise HTTPException(404, f"cycle not found: {campaign_id}/{cycle_id}")
    parent_index = json.loads(parent_index_path.read_text(encoding="utf-8"))

    ts = datetime.now(UTC).isoformat()
    ts_compact = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{cycle_id}|hitl|{ts_compact}".encode()).hexdigest()[:8]
    root_id = root_cycle_id(cycle_id)
    fork_id = f"{root_id}_fork_{suffix}"

    fork_dir = store.campaigns.cycle_dir(campaign_id, fork_id)
    if fork_dir.exists():
        raise HTTPException(409, f"fork dir already exists: {fork_dir}")
    fork_dir.mkdir(parents=True, exist_ok=True)

    parent_session_id = parent_index.get("parent_session_id", "")
    fork_index = {
        "type": parent_index.get("type", "optimization_loop"),
        "connector_type": parent_index.get("connector_type", ""),
        "backend_id": parent_index.get("backend_id", ""),
        "parent_cycle_id": cycle_id,
        "parent_session_id": parent_session_id,
        "sibling_kind": "fork",
        "status": "interrupted",
        "n_rounds": 0,
        "best_accuracy": None,
        "origin_accuracy": parent_index.get("best_accuracy"),
        "created_at": ts,
        "updated_at": ts,
        "header": parent_index.get("header", {}),
        "fork": {
            "trigger": "operator_hitl",
            "from_round": request.round,
            "from_candidate": request.candidate_id,
            "forked_at": ts,
        },
    }
    write_json(fork_dir / "index.json", fork_index)

    parent_log = CycleEventLog.open(CycleDir(parent_dir))
    fork_log = CycleEventLog.open(CycleDir(fork_dir))
    fork_log.inherit_from(parent_log, parent_log.next_offset)

    if parent_session_id:
        save_active_pointer(store.tenant_id, parent_session_id, campaign_id, fork_id)

    return CreateForkResponse(
        campaign_id=campaign_id,
        fork_cycle_id=fork_id,
        cli_command="python -m promptpotter resume",
    )


class StopCycleResponse(BaseModel):
    campaign_id: str
    cycle_id: str
    flag_written: bool


@_active_router.post(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/stop",
    response_model=StopCycleResponse,
)
async def stop_cycle(campaign_id: str, cycle_id: str, store: StoreDep) -> StopCycleResponse:
    """Write ``.runtime/stop.flag``; ``Session.stop_check`` polls at the next checkpoint and exits cleanly. Idempotent. The optimizer logs to the ledger itself when it observes the flag."""
    cycle_dir = store.campaigns.cycle_dir(campaign_id, cycle_id)
    if not (cycle_dir / "index.json").is_file():
        raise HTTPException(404, f"cycle not found: {campaign_id}/{cycle_id}")
    runtime_dir = cycle_dir / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    flag = runtime_dir / "stop.flag"
    flag.write_text(f"requested_at={datetime.now(UTC).isoformat()}\n", encoding="utf-8")
    return StopCycleResponse(campaign_id=campaign_id, cycle_id=cycle_id, flag_written=True)


class DeleteCycleResponse(BaseModel):
    campaign_id: str
    cycle_id: str
    deleted: bool
    reason: str = Field(default="", description="Populated only when the delete was a no-op.")


class CleanupEmptyResponse(BaseModel):
    campaign_id: str
    root_cycle_id: str
    deleted_cycle_ids: list[str] = Field(
        description="Removed dirs in deletion order (leaves first)."
    )
    skipped: list[dict[str, str]] = Field(
        description="Considered but skipped — ``{cycle_id, reason}`` each."
    )


# Stub-deletion guard on CampaignStore.try_delete_stub_cycle is SoT for both
# orchestration cleanup + API; active-pointer policy lives in each caller.


@_active_router.delete(
    "/campaigns/{campaign_id}/cycles/{cycle_id}",
    response_model=DeleteCycleResponse,
)
async def delete_cycle(campaign_id: str, cycle_id: str, store: StoreDep) -> DeleteCycleResponse:
    """Delete a stub cycle dir; guards (exists, n_rounds==0, no descendants, not root)
    in ``CampaignStore.try_delete_stub_cycle``; active-pointer guard here so the API never silently retargets.
    """
    _, _, active_cmp, active_cid = read_active_pointer()
    if active_cmp == campaign_id and active_cid == cycle_id:
        raise HTTPException(409, f"refusing to delete {cycle_id}: active cycle — switch first")
    deleted, reason = store.campaigns.try_delete_stub_cycle(campaign_id, cycle_id)
    if not deleted:
        status = 404 if reason == "not on disk" else 409
        raise HTTPException(status, f"refusing to delete {cycle_id}: {reason}")
    return DeleteCycleResponse(campaign_id=campaign_id, cycle_id=cycle_id, deleted=True)


@_active_router.post(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/cleanup-empty",
    response_model=CleanupEmptyResponse,
)
async def cleanup_empty_stubs(
    campaign_id: str, cycle_id: str, store: StoreDep
) -> CleanupEmptyResponse:
    """Batch-delete every empty-stub sibling under ``root_cycle_id(cycle_id)``; leaves-first via two passes (enough for the two-generation stub chains on disk today)."""
    root_id = root_cycle_id(cycle_id)
    _, _, active_cmp, active_cid = read_active_pointer()
    deleted_ids: list[str] = []
    skipped: list[dict[str, str]] = []
    for _pass in range(2):
        progress = False
        entries = store.campaigns.enumerate_cycles()
        family_ids = [
            e["cycle_id"]
            for e in entries
            if e["campaign_id"] == campaign_id
            and e["cycle_id"] != root_id
            and e["parent_cycle_id"] == root_id
        ]
        for cid in family_ids:
            if cid in deleted_ids:
                continue
            if campaign_id == active_cmp and cid == active_cid:
                # Match the single-cycle DELETE active-pointer guard.
                if _pass == 1:
                    skipped.append({"cycle_id": cid, "reason": "active cycle — switch first"})
                continue
            deleted, reason = store.campaigns.try_delete_stub_cycle(campaign_id, cid)
            if deleted:
                deleted_ids.append(cid)
                progress = True
            elif _pass == 1:
                skipped.append({"cycle_id": cid, "reason": reason})
        if not progress:
            break
    return CleanupEmptyResponse(
        campaign_id=campaign_id,
        root_cycle_id=root_id,
        deleted_cycle_ids=deleted_ids,
        skipped=skipped,
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


__all__ = [
    "ActiveSessionResponse",
    "CleanupEmptyResponse",
    "CreateForkRequest",
    "CreateForkResponse",
    "CycleListEntry",
    "CyclesResponse",
    "DeleteCycleResponse",
    "StopCycleResponse",
]
