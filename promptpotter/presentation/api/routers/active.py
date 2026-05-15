"""Active-session pointer + sanctioned mutating control endpoints.

Read-only surface that lets the static webapp pin to the currently active
cycle, plus the two sanctioned mutating endpoints (operator-initiated fork
via ``CycleEventLog.inherit_from``, stop flag via ``.runtime/stop.flag``).
Both ride existing I/O kinds (Persistence + Control-local) — they do not
introduce a new I/O kind.
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
from promptpotter.infrastructure.store.paths import root_cycle_id
from promptpotter.presentation.api.deps import StoreDep

_active_router = APIRouter(tags=["Active"])

_OPTIMIZER_PIPELINE_PATH = (
    Path(__file__).resolve().parents[4] / "datasets" / "_optimizer" / "pipeline.json"
)


class ActiveSessionResponse(BaseModel):
    tenant_id: str = Field(description="Tenant the active session belongs to")
    session_id: str = Field(description="Active session id")
    cycle_id: str = Field(description="Active cycle id (pinned by the webapp)")


@_active_router.get("/active", response_model=ActiveSessionResponse)
async def get_active_session() -> ActiveSessionResponse:
    """Return the active-session pointer; 404 when no session is active."""
    tenant_id, session_id, cycle_id = read_active_pointer()
    if not tenant_id:
        raise HTTPException(404, "No active session")
    return ActiveSessionResponse(
        tenant_id=tenant_id,
        session_id=session_id,
        cycle_id=cycle_id,
    )


class CycleListEntry(BaseModel):
    cycle_id: str
    parent_session_id: str = ""
    dataset_name: str = ""
    backend_id: str = ""
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    is_root: bool
    status: str
    best_accuracy: float | None = None
    n_rounds: int = 0
    created_at: str = ""
    updated_at: str = ""


class CyclesResponse(BaseModel):
    tenant_id: str
    active_cycle_id: str | None = Field(
        description="The cycle the CLI currently considers active "
        "(matches active_session.json), or null when no session is active."
    )
    cycles: list[CycleListEntry]


@_active_router.get("/cycles", response_model=CyclesResponse)
async def get_cycles(store: StoreDep) -> CyclesResponse:
    """Every cycle on disk under the current tenant, plus the active pointer.

    Powers the operator dashboard's cycle picker. Read-only — never mutates
    ``active_session.json``. Co-serves ``active_cycle_id`` so the picker
    renders without a second round-trip."""
    _, _, active_cid = read_active_pointer()
    entries = store.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


# Sanctioned mutating endpoints — see promptpotter/presentation/CLAUDE.md
# for the charter. Both ride existing I/O kinds (Persistence's `inherit_from`
# for forks, Control-local's `stop_check` for stop) — they do not introduce
# a new I/O kind.


class CreateForkRequest(BaseModel):
    round: int = Field(description="Round the operator endorsed (audit-trail only in the MVP)")
    candidate_id: str = Field(
        default="", description="Candidate the operator endorsed (audit-trail only in the MVP)"
    )


class CreateForkResponse(BaseModel):
    fork_cycle_id: str
    cli_command: str = Field(
        description="CLI invocation the operator runs to launch the fork. The active pointer "
        "has been retargeted to this fork, so a bare `optimize` picks it up."
    )
    active_pointer_retargeted: bool = True


@_active_router.post("/cycles/{cycle_id}/forks", response_model=CreateForkResponse)
async def create_fork(
    cycle_id: str, request: CreateForkRequest, store: StoreDep
) -> CreateForkResponse:
    """Operator-initiated fork via the lineage inspector — endorse path.

    The new fork inherits the parent's ledger up to the parent's *current*
    `next_offset` (i.e., "fork from now, endorse the parent's present
    state"). The selected ``round`` + ``candidate_id`` are recorded in the
    fork's ``index.json::fork`` block for the audit trail but do not yet
    drive offset selection. The active pointer is retargeted to the new
    fork so a subsequent ``python -m promptpotter optimize`` picks it up.
    """
    parent_dir = store.campaigns.campaign_dir(cycle_id)
    parent_index_path = parent_dir / "index.json"
    if not parent_index_path.is_file():
        raise HTTPException(404, f"cycle not found: {cycle_id}")
    parent_index = json.loads(parent_index_path.read_text(encoding="utf-8"))

    ts = datetime.now(UTC).isoformat()
    ts_compact = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = hashlib.sha256(f"{cycle_id}|hitl|{ts_compact}".encode()).hexdigest()[:8]
    root_id = root_cycle_id(cycle_id)
    fork_id = f"{root_id}_fork_{suffix}"

    fork_dir = store.campaigns.campaign_dir(fork_id)
    if fork_dir.exists():
        raise HTTPException(409, f"fork dir already exists: {fork_dir}")
    fork_dir.mkdir(parents=True, exist_ok=True)

    parent_session_id = parent_index.get("parent_session_id", "")
    fork_index = {
        "campaign_id": fork_id,
        "type": parent_index.get("type", "optimization_loop"),
        "config": parent_index.get("config", {}),
        "connector_type": parent_index.get("connector_type", ""),
        "backend_id": parent_index.get("backend_id", ""),
        "parent_cycle_id": cycle_id,
        "parent_session_id": parent_session_id,
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
    (fork_dir / "index.json").write_text(json.dumps(fork_index, indent=2), encoding="utf-8")

    parent_log = CycleEventLog.open(CycleDir(parent_dir))
    fork_log = CycleEventLog.open(CycleDir(fork_dir))
    fork_log.inherit_from(parent_log, parent_log.next_offset)

    if parent_session_id:
        save_active_pointer(store.tenant_id, parent_session_id, fork_id)

    return CreateForkResponse(
        fork_cycle_id=fork_id,
        cli_command="python -m promptpotter optimize",
    )


class StopCycleResponse(BaseModel):
    cycle_id: str
    flag_written: bool


@_active_router.post("/cycles/{cycle_id}/stop", response_model=StopCycleResponse)
async def stop_cycle(cycle_id: str, store: StoreDep) -> StopCycleResponse:
    """Write ``.runtime/stop.flag`` under the cycle dir.

    The running loop's ``Session.stop_check`` polls for the flag at the
    next checkpoint and exits cleanly. Idempotent — writing twice is a
    no-op. No ledger write from the API — the optimizer itself records the
    operator-initiated stop in the ledger when it observes the flag.
    """
    cycle_dir = store.campaigns.campaign_dir(cycle_id)
    if not (cycle_dir / "index.json").is_file():
        raise HTTPException(404, f"cycle not found: {cycle_id}")
    runtime_dir = cycle_dir / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    flag = runtime_dir / "stop.flag"
    flag.write_text(f"requested_at={datetime.now(UTC).isoformat()}\n", encoding="utf-8")
    return StopCycleResponse(cycle_id=cycle_id, flag_written=True)


@_active_router.get("/optimizer/pipeline")
async def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``optimizer_pipeline.json`` — nodes, pipelines, and ``view`` topology
    (containers + node positions + edges) the webapp renders the workflow from."""
    return json.loads(_OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))


@_active_router.get("/evaluators_meta")
async def get_evaluators_meta() -> list[dict[str, Any]]:
    """Live registry projection — name, description, scope, direction, node_type.

    Read by the webapp's What-If panel so the evaluator registry is available
    even when a cycle's ``dashboard.json`` doesn't include ``cycle_info``.
    Always returns the import-time registry, not a per-cycle snapshot.
    """
    from promptpotter.application.scoring.evaluators import evaluators_meta

    return evaluators_meta()


__all__ = [
    "ActiveSessionResponse",
    "CreateForkRequest",
    "CreateForkResponse",
    "CycleListEntry",
    "CyclesResponse",
    "StopCycleResponse",
]
