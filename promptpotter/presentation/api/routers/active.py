"""Active-session pointer + sanctioned mutating control endpoints.

Read-only surface that lets the static webapp pin to the currently active
campaign + cycle, plus the sanctioned mutating endpoints (operator-initiated
fork via ``CycleEventLog.inherit_from``, stop flag via ``.runtime/stop.flag``,
and stub-cycle cleanup via ``DELETE``). All three ride existing I/O kinds
(Persistence + Control-local + Persistence-cleanup) — no new I/O kind.

Cycles are campaign-scoped: a ``cycle_id`` is unique only within its
campaign, so every cycle-level route carries ``(campaign_id, cycle_id)``.
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
        description=(
            "Immediate parent cycle id for siblings (forks, sweeps, diag); "
            "null for roots. Used by the sidebar to nest siblings under "
            "their parent within the campaign."
        ),
    )
    dataset_name: str = ""
    backend_id: str = ""
    sibling_kind: Literal["root", "fork", "diag", "sweep"]
    # Operator-facing unit kind — the time-horizon taxonomy the sidebar
    # badges by. Derived server-side from (sibling_kind, fork trigger):
    # session = the root run; divergent_resume = a fork-on-divergence
    # branch; user_fork = any operator-initiated branch (HITL / diag /
    # sweep); l3_fork = reserved for L3 auto-forking (not emitted yet).
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
        description="The campaign the CLI currently considers active "
        "(matches active_session.json), or null when no session is active."
    )
    active_cycle_id: str | None = Field(
        description="The cycle the CLI currently considers active "
        "(matches active_session.json), or null when no session is active."
    )
    cycles: list[CycleListEntry]


@_active_router.get("/cycles", response_model=CyclesResponse)
async def get_cycles(store: StoreDep) -> CyclesResponse:
    """Every cycle on disk under the current tenant, plus the active pointer.

    Powers the operator dashboard's cycle picker. Read-only — never mutates
    ``active_session.json``. Co-serves the active ``(campaign, cycle)`` pair
    so the picker renders without a second round-trip."""
    _, _, active_cmp, active_cid = read_active_pointer()
    entries = store.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=store.tenant_id,
        active_campaign_id=active_cmp or None,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


# Sanctioned mutating endpoints — see promptpotter/presentation/CLAUDE.md
# for the charter. All ride existing I/O kinds (Persistence's `inherit_from`
# for forks, Control-local's `stop_check` for stop, Persistence cleanup for
# stub deletion) — they do not introduce a new I/O kind.


class CreateForkRequest(BaseModel):
    round: int = Field(description="Round the operator endorsed (audit-trail only in the MVP)")
    candidate_id: str = Field(
        default="", description="Candidate the operator endorsed (audit-trail only in the MVP)"
    )


class CreateForkResponse(BaseModel):
    campaign_id: str
    fork_cycle_id: str
    cli_command: str = Field(
        description="CLI invocation the operator runs to launch the fork. The active pointer "
        "has been retargeted to this fork, so a bare `resume` picks it up."
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

    The new fork inherits the parent's ledger up to the parent's *current*
    ``next_offset`` (i.e., "fork from now, endorse the parent's present
    state"). The selected ``round`` + ``candidate_id`` are recorded in the
    fork's ``index.json::fork`` block for the audit trail but do not yet
    drive offset selection. The fork is minted inside the *same* campaign.
    The active pointer is retargeted to the new fork so a subsequent
    ``python -m promptpotter resume`` picks it up.
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
    """Write ``.runtime/stop.flag`` under the cycle dir.

    The running loop's ``Session.stop_check`` polls for the flag at the
    next checkpoint and exits cleanly. Idempotent — writing twice is a
    no-op. No ledger write from the API — the optimizer itself records the
    operator-initiated stop in the ledger when it observes the flag.
    """
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
    reason: str = Field(
        default="",
        description="Empty on success; populated only when the delete was a no-op",
    )


class CleanupEmptyResponse(BaseModel):
    campaign_id: str
    root_cycle_id: str
    deleted_cycle_ids: list[str] = Field(
        description="Cycles whose dirs were removed, in deletion order (leaves first)."
    )
    skipped: list[dict[str, str]] = Field(
        description="Cycles considered but skipped, with a 'cycle_id' + 'reason' key each."
    )


# Stub-deletion guard lives on CampaignStore.try_delete_stub_cycle so
# the orchestration cleanup (entry.py + sweep_runner.py) and the API
# share one source of truth. The store method does NOT read the active
# pointer — both callers wrap it with their own pointer policy
# (API refuses when active matches; orchestration retargets first).


@_active_router.delete(
    "/campaigns/{campaign_id}/cycles/{cycle_id}",
    response_model=DeleteCycleResponse,
)
async def delete_cycle(campaign_id: str, cycle_id: str, store: StoreDep) -> DeleteCycleResponse:
    """Delete a single stub cycle dir from disk.

    Refuses unless cycle exists, ``n_rounds == 0``, has no on-disk
    children, isn't the active cycle, and isn't a campaign root. The
    n_rounds + descendants + root guards come from
    ``CampaignStore.try_delete_stub_cycle``; the active-pointer guard
    is enforced here so the API never silently retargets.
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
    """Batch-delete every empty-stub sibling under the root of *cycle_id*.

    Walks the campaign's cycles once, identifies all cycles whose own work
    is empty (``n_rounds == 0``) and have no descendants, and deletes them.
    Iterates leaves-first via two passes so a stub whose parent is also
    a stub still gets picked up on the second sweep.
    """
    root_id = root_cycle_id(cycle_id)
    _, _, active_cmp, active_cid = read_active_pointer()
    deleted_ids: list[str] = []
    skipped: list[dict[str, str]] = []
    # Two passes so we drain trees from the leaves up — a stub parent
    # that has a stub child can only be deleted after the child's gone.
    # Two passes is enough for two-generation stub chains, which is all
    # the on-disk data exhibits today. Extend to a loop-until-stable if
    # deeper stub trees become common.
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
                # Active-pointer guard — refuse here so the API's policy
                # matches the single-cycle DELETE endpoint above.
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
    """Bundled ``optimizer_pipeline.json`` — nodes, pipelines, and ``view`` topology
    (containers + node positions + edges) the webapp renders the workflow from."""
    pipeline: dict[str, Any] = json.loads(_OPTIMIZER_PIPELINE_PATH.read_text(encoding="utf-8"))
    return pipeline


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
    "CleanupEmptyResponse",
    "CreateForkRequest",
    "CreateForkResponse",
    "CycleListEntry",
    "CyclesResponse",
    "DeleteCycleResponse",
    "StopCycleResponse",
]
