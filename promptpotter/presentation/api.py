"""HTTP read API — backend storage + campaign registry + per-cycle live reads.

Three routers in one module so they share the storage / pipeline-discovery
imports and the ``StoreDep`` dependency:

1. **Backend storage** (``backends_router``) — manages backend connections,
   syncs experiments from backends in their native format, and exposes
   pipeline discovery.

2. **Campaign registry** (``campaigns_router``) — lists + views
   optimization campaigns, nested under ``/backends/{backend_id}/campaigns``.

3. **Per-cycle live reads** (also on ``campaigns_router``) — dashboard
   passthrough, log.md, ledger reads + filtered views (decisions,
   forks). The first real consumer of the per-cycle ``CycleEventLog``;
   validates the record types carry what a webapp needs.
"""

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from promptpotter import connectors
from promptpotter.config.settings import settings
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.run_records import ResumeCheckpointKind, ResumeCheckpointRecord
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store import (
    Stores,
    build_stores,
    campaign_dir_for,
    read_active_pointer,
    root_dir_for,
    save_active_pointer,
)
from promptpotter.infrastructure.store.paths import DEFAULT_DATASETS_ROOT, root_cycle_id

StoreDep = Annotated[Stores, Depends(build_stores)]

logger = logging.getLogger(__name__)

backends_router = APIRouter(prefix="/backends", tags=["Backends"])


class RegisterBackendRequest(BaseModel):
    name: str = Field(..., description="Human-readable name")
    backend_type: str = Field(..., description="Backend type, e.g. 'default'")
    base_url: str = Field(..., description="Backend API base URL")
    id: str | None = Field(
        None,
        description="Custom ID (auto-generated from name if omitted)",
    )


class RegisterBackendResponse(BaseModel):
    id: str = Field(description="Backend identifier")
    name: str = Field(description="Human-readable backend name")
    backend_type: str = Field(description="Backend type (e.g. 'default')")
    base_url: str = Field(description="Backend API base URL")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class SyncResponse(BaseModel):
    backend_id: str = Field(description="Backend identifier")
    experiments_synced: int = Field(description="Number of experiments synced from backend")
    synced_at: str = Field(description="ISO 8601 sync timestamp")


class PipelineViewResponse(BaseModel):
    backend_id: str = Field(description="Backend identifier")
    backend_pipeline: dict[str, Any] = Field(description="Full PipelineSchema as dict")
    computed_nodes: list[dict[str, Any]] = Field(description="Computed PipelineNode dicts")
    fetched_at: str = Field(description="ISO 8601 fetch timestamp")
    source: str = Field(description="Pipeline source: 'live', 'cached', or 'default'")


def _get_backend_or_404(backend_id: str, store: Stores) -> BackendConnection:
    backend = store.backends.get(backend_id)
    if not backend:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_id}' not found")
    return backend


@backends_router.post("", response_model=RegisterBackendResponse, status_code=201)
async def register_backend(request: RegisterBackendRequest, store: StoreDep):
    """Register a new backend connection."""
    backend_id = request.id or re.sub(r"[^a-z0-9]+", "-", request.name.lower().strip()).strip("-")
    if store.backends.get(backend_id):
        raise HTTPException(status_code=409, detail=f"Backend '{backend_id}' already exists")

    backend = BackendConnection(
        id=backend_id,
        name=request.name,
        backend_type=request.backend_type,
        base_url=request.base_url.rstrip("/"),
    )
    store.backends.register(backend)

    return RegisterBackendResponse(
        id=backend.id,
        name=backend.name,
        backend_type=backend.backend_type,
        base_url=backend.base_url,
        created_at=backend.created_at,
    )


@backends_router.get("", response_model=list[RegisterBackendResponse])
async def list_backends(store: StoreDep):
    """List all registered backends."""
    return [
        RegisterBackendResponse(
            id=b.id,
            name=b.name,
            backend_type=b.backend_type,
            base_url=b.base_url,
            created_at=b.created_at,
        )
        for b in store.backends.list_all()
    ]


@backends_router.get("/{backend_id}", response_model=RegisterBackendResponse)
async def get_backend(backend_id: str, store: StoreDep):
    """Get backend details."""
    b = _get_backend_or_404(backend_id, store)
    return RegisterBackendResponse(
        id=b.id,
        name=b.name,
        backend_type=b.backend_type,
        base_url=b.base_url,
        created_at=b.created_at,
    )


@backends_router.post("/{backend_id}/sync", response_model=SyncResponse)
async def sync_experiments(backend_id: str, store: StoreDep):
    """Sync experiments from backend API into project store (verbatim)."""
    backend = _get_backend_or_404(backend_id, store)
    connector = connectors.get(backend.backend_type)
    client = BackendClient(
        backend.base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )

    try:
        count = await client.sync_experiments(store, backend_id)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync from {backend.base_url}: {e}",
        ) from e

    now = datetime.now(UTC).isoformat()
    backend.last_synced_at = now
    store.backends.update(backend)

    return SyncResponse(
        backend_id=backend_id,
        experiments_synced=count,
        synced_at=now,
    )


@backends_router.get("/{backend_id}/experiments")
async def list_experiments(backend_id: str, store: StoreDep):
    """List synced experiments (from local store, native format)."""
    _get_backend_or_404(backend_id, store)

    # First try the experiments list file
    data = store.backends.load_sync(backend_id, "experiments.json")
    if data:
        return data

    # Fall back to individual files
    experiments = store.backends.list_synced_experiments(backend_id)
    if not experiments:
        raise HTTPException(
            status_code=404,
            detail="No synced experiments. Run POST /sync first.",
        )
    return {"experiments": experiments}


@backends_router.get("/{backend_id}/experiments/{experiment_id}")
async def get_experiment(backend_id: str, experiment_id: str, store: StoreDep):
    """Get a synced experiment in native backend format."""
    _get_backend_or_404(backend_id, store)
    data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"Experiment '{experiment_id}' not synced. Run POST /sync first.",
        )
    return data


@backends_router.get("/{backend_id}/pipeline", response_model=PipelineViewResponse)
async def get_pipeline(backend_id: str, store: StoreDep):
    """Dynamic pipeline view from the backend."""
    backend = _get_backend_or_404(backend_id, store)
    connector = connectors.get(backend.backend_type)
    client = BackendClient(
        backend.base_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )

    try:
        raw: dict[str, Any] | None = await client.fetch_pipeline()
        source = "live"
    except Exception:
        logger.warning("Backend unreachable at %s; returning empty schema", client.base_url)
        raw = None
        source = "default"

    schema = parse_pipeline_response(raw) if raw is not None else PipelineSchema()

    return PipelineViewResponse(
        backend_id=backend_id,
        backend_pipeline=schema.model_dump(),
        computed_nodes=[s.model_dump() for s in schema.nodes],
        fetched_at=datetime.now(UTC).isoformat(),
        source=source,
    )


# ===========================================================================
# Campaign registry endpoints — REST API for listing + viewing campaigns,
# nested under /backends/{backend_id}/campaigns.
# ===========================================================================

campaigns_router = APIRouter()


class CampaignSummary(BaseModel):
    campaign_id: str = Field(description="Unique campaign identifier")
    name: str = Field(description="Human-readable campaign name")
    status: str = Field(description="Campaign status: active, completed, or stopped")
    n_rounds: int = Field(description="Total number of completed round_data rounds")
    best_accuracy: float = Field(description="Highest accuracy achieved across all rounds")
    origin_accuracy: float = Field(description="Initial accuracy before optimization")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class CampaignListResponse(BaseModel):
    campaigns: list[CampaignSummary] = Field(description="List of campaign summaries")
    total: int = Field(description="Total number of campaigns")


class RoundSummary(BaseModel):
    round_id: str = Field(description="Unique round_data identifier")
    round: int = Field(description="Round number within the campaign")
    label: str = Field(description="Human-readable label (e.g. 'round_3')")
    prompt_fields_id: str = Field(description="OptSearchPoint ID for this round_data's prompt")
    accuracy: float = Field(description="Accuracy achieved in this round_data")
    hits: int = Field(description="Number of correct matches")
    total: int = Field(description="Total number of evaluated samples")
    improved: bool = Field(description="Whether this round_data improved over the previous best")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class CampaignDetailResponse(CampaignSummary):
    backend_id: str = Field(description="Backend this campaign optimizes against")
    config: dict[str, Any] = Field(description="Full campaign configuration used for this run")
    best_round_id: str | None = Field(description="Trial ID of the best-performing round")
    rounds: list[RoundSummary] = Field(description="Ordered list of round_data summaries")
    langfuse_trace_id: str | None = Field(
        default=None,
        description="Langfuse trace ID if observability is enabled",
    )


@campaigns_router.get(
    "/backends/{backend_id}/campaigns",
    response_model=CampaignListResponse,
)
async def list_campaigns(
    store: StoreDep,
    backend_id: str,
):
    """List all campaigns for a backend."""
    campaigns = store.campaigns.list_all(backend_id)
    return CampaignListResponse(
        campaigns=[CampaignSummary(**c) for c in campaigns],
        total=len(campaigns),
    )


@campaigns_router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}",
    response_model=CampaignDetailResponse,
)
async def get_campaign(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
):
    """Get campaign detail with round_data summaries."""
    data = store.campaigns.load(backend_id, campaign_id)
    if data is None:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return CampaignDetailResponse(**data)


@campaigns_router.get(
    "/backends/{backend_id}/campaigns/{campaign_id}/rounds/{round_num}",
    response_model=dict[str, Any],
)
async def get_trial(
    store: StoreDep,
    backend_id: str,
    campaign_id: str,
    round_num: int,
):
    """Get full round_data detail for a specific round."""
    round_data = store.campaigns.load_round_file(backend_id, campaign_id, round_num)
    if round_data is None:
        raise HTTPException(404, f"Trial round {round_num} not found")
    return round_data


# ===========================================================================
# Per-cycle live reads — dashboard, log, ledger
# Webapp-facing endpoints that pass through the on-disk artifacts the
# operator workflows already use, plus filtered ledger views (decisions,
# forks) derived from the typed CycleRecord stream.
# ===========================================================================


def _read_text_or_404(path: Path, label: str) -> str:
    if not path.exists():
        raise HTTPException(404, f"{label} not found at {path.name}")
    return path.read_text(encoding="utf-8")


class CycleRecordEnvelope(BaseModel):
    """One typed ledger record + its offset.

    ``record_type`` discriminates ResumeCheckpointRecord / PhaseRecord / SnapshotRecord;
    ``payload`` carries the model's fields verbatim (Pydantic
    model_dump). Consumers either route by record_type or filter on
    ResumeCheckpointRecord.kind for gating events.
    """

    offset: int = Field(description="Position in events.jsonl, monotonic per cycle")
    record_type: str = Field(description="One of 'decision' | 'phase' | 'snapshot'")
    payload: dict[str, Any] = Field(description="Verbatim record fields")


class LedgerSliceResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the records belong to")
    records: list[CycleRecordEnvelope] = Field(description="Records since the requested offset")
    next_offset: int = Field(description="Pass back as ?since= for incremental polling")


class DecisionEnvelope(BaseModel):
    offset: int = Field(description="Position in the cycle's ledger")
    kind: str = Field(description="ResumeCheckpointKind value (round_winner, fork_cut, ...)")
    round: int | None = Field(default=None, description="Round the decision was made in")
    inputs_ref: dict[str, Any] = Field(description="Inputs the decision was derived from")
    outcome: Any = Field(description="The decision's outcome")
    data: dict[str, Any] = Field(description="Archival sidecar (LLM output, diagnostics)")
    timestamp: str = Field(description="ISO 8601 emit time")


class DecisionsResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the decisions belong to")
    decisions: list[DecisionEnvelope] = Field(
        description="All ResumeCheckpointRecord records in append order"
    )


class ForkRef(BaseModel):
    fork_cycle_id: str = Field(description="The forked sibling cycle's id")
    from_round: int = Field(description="Round at which the parent was cut")
    forked_at: str = Field(description="ISO 8601 fork time (from FORK_CUT.data.forked_at)")


class ForksResponse(BaseModel):
    parent_cycle_id: str = Field(description="The parent cycle this list applies to")
    forks: list[ForkRef] = Field(description="Children minted from this parent")


def _open_cycle_ledger_or_404(cycle_id: str, store: Stores) -> CycleEventLog:
    """Open the per-cycle ledger; 404 if the cycle dir doesn't exist."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")
    return CycleEventLog.open(CycleDir(cycle_dir))


def _record_to_envelope(record: Any, offset: int) -> CycleRecordEnvelope:
    return CycleRecordEnvelope(
        offset=offset,
        record_type=record.record_type,
        payload=record.model_dump(),
    )


class LogMdResponse(BaseModel):
    cycle_id: str = Field(description="Cycle the markdown belongs to")
    markdown: str = Field(description="Verbatim log.md source")


@campaigns_router.get(
    "/campaigns/{cycle_id}/log_md",
    response_model=LogMdResponse,
)
async def get_cycle_log_md(store: StoreDep, cycle_id: str) -> LogMdResponse:
    """Pre-rendered log.md (per-cycle audit) — markdown source in a typed envelope."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    text = _read_text_or_404(cycle_dir / "log.md", "log.md")
    return LogMdResponse(cycle_id=cycle_id, markdown=text)


@campaigns_router.get(
    "/campaigns/{cycle_id}/ledger",
    response_model=LedgerSliceResponse,
)
async def get_cycle_ledger(
    store: StoreDep,
    cycle_id: str,
    since: int = Query(0, ge=0, description="Skip records before this offset"),
    limit: int = Query(1000, ge=1, le=50_000, description="Max records to return"),
):
    """Typed CycleRecord stream from the cycle's events.jsonl, since the given offset.

    Pass back ``next_offset`` as ``?since=`` for incremental polling —
    the webapp's primary live-state read path. Up to ``limit`` records
    per call so the operator can stream a long history without one-shot
    huge responses.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    records: list[CycleRecordEnvelope] = []
    offset = since
    for rec in ledger.iter(since=since):
        records.append(_record_to_envelope(rec, offset))
        offset += 1
        if len(records) >= limit:
            break
    return LedgerSliceResponse(
        cycle_id=cycle_id,
        records=records,
        next_offset=offset,
    )


@campaigns_router.get(
    "/campaigns/{cycle_id}/decisions",
    response_model=DecisionsResponse,
)
async def get_cycle_decisions(store: StoreDep, cycle_id: str):
    """All ResumeCheckpointRecord records from the cycle's ledger, in append order.

    Filtered view over ``GET /ledger`` for the common gating-event use
    case. Includes archival kinds (probe, fork_cut) — consumers filter
    by ``kind`` if they only want REPLAYED ones.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    out: list[DecisionEnvelope] = []
    for offset, rec in enumerate(ledger.iter()):
        if isinstance(rec, ResumeCheckpointRecord):
            out.append(
                DecisionEnvelope(
                    offset=offset,
                    kind=rec.kind.value,
                    round=rec.round,
                    inputs_ref=rec.inputs_ref,
                    outcome=rec.outcome,
                    data=rec.data,
                    timestamp=rec.timestamp,
                )
            )
    return DecisionsResponse(cycle_id=cycle_id, decisions=out)


@campaigns_router.get(
    "/campaigns/{cycle_id}/forks",
    response_model=ForksResponse,
)
async def get_cycle_forks(store: StoreDep, cycle_id: str):
    """Sibling forks minted from this cycle, derived from FORK_CUT records.

    Each fork's metadata comes from a single ``ResumeCheckpointRecord(kind=FORK_CUT)``
    in the parent's ledger — ``outcome`` is the child cycle_id,
    ``inputs_ref.from_round`` is the cut point, ``data.forked_at`` is
    the wall-clock fork time.
    """
    ledger = _open_cycle_ledger_or_404(cycle_id, store)
    forks: list[ForkRef] = []
    for rec in ledger.iter():
        if isinstance(rec, ResumeCheckpointRecord) and rec.kind is ResumeCheckpointKind.FORK_CUT:
            forks.append(
                ForkRef(
                    fork_cycle_id=str(rec.outcome),
                    from_round=int(rec.inputs_ref.get("from_round", -1)),
                    forked_at=str(rec.data.get("forked_at", "")),
                )
            )
    return ForksResponse(parent_cycle_id=cycle_id, forks=forks)


# ===========================================================================
# Active-session pointer + file-tree (M11 webapp preview)
# Read-only surface that lets the static webapp pin to the currently active
# cycle and walk every artifact under the cycle dir + family-root telemetry.
# ===========================================================================

_active_router = APIRouter(tags=["Active"])


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
# a new I/O kind. The M12 daemon will replace both with one typed control
# surface.


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

    MVP scope (pre-M12): the new fork inherits the parent's ledger up to the
    parent's *current* `next_offset` (i.e., "fork from now, endorse the
    parent's present state"). The selected ``round`` + ``candidate_id`` are
    recorded in the fork's ``index.json::fork`` block for the audit trail
    but do not yet drive offset selection — that's M12 work along with the
    substitute-candidates form. The active pointer is retargeted to the new
    fork so a subsequent ``python -m promptpotter optimize`` picks it up.
    """
    import hashlib
    import json
    from datetime import UTC, datetime

    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleEventLog

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
    from datetime import UTC, datetime

    cycle_dir = store.campaigns.campaign_dir(cycle_id)
    if not (cycle_dir / "index.json").is_file():
        raise HTTPException(404, f"cycle not found: {cycle_id}")
    runtime_dir = cycle_dir / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    flag = runtime_dir / "stop.flag"
    flag.write_text(
        f"requested_at={datetime.now(UTC).isoformat()}\n", encoding="utf-8"
    )
    return StopCycleResponse(cycle_id=cycle_id, flag_written=True)


_OPTIMIZER_PIPELINE_PATH = (
    Path(__file__).resolve().parents[1] / "application" / "optimization" / "optimizer_pipeline.json"
)


@_active_router.get("/optimizer/pipeline")
async def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``optimizer_pipeline.json`` — nodes, pipelines, and ``view`` topology
    (containers + node positions + edges) the webapp renders the workflow from."""
    import json

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


class FileEntry(BaseModel):
    path: str = Field(description="Path relative to the scope root, forward slashes")
    scope: Literal["cycle", "family"] = Field(description="Which root the path is under")
    size: int = Field(description="File size in bytes")
    mtime: str = Field(description="ISO 8601 UTC modification time")


class FilesResponse(BaseModel):
    cycle_id: str
    is_fork: bool = Field(description="True when cycle dir != family-root dir")
    entries: list[FileEntry]


class FileContentResponse(BaseModel):
    cycle_id: str
    scope: Literal["cycle", "family"]
    path: str
    size: int
    mtime: str
    content_type: Literal["json", "markdown", "log", "text", "binary"]
    content: str | None = Field(
        default=None,
        description="UTF-8 text content; None when binary or oversized",
    )


# Family-root file-level artifacts (mirror of tests/test_invariants.py::ROOT_TELEMETRY_ARTIFACTS).
_FAMILY_FILE_LEVEL_ARTIFACTS = ("dashboard.json",)
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024  # 2 MiB
_MAX_FILE_ENTRIES = 5000


def _walk_files(root: Path) -> Iterator[Path]:
    """Walk *root* recursively, yielding files. Skip dotfiles except ``.cache/``."""
    if not root.exists():
        return
    for entry in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith(".") and entry.name != ".cache":
            continue
        if entry.is_dir():
            yield from _walk_files(entry)
        elif entry.is_file():
            yield entry


def _iso_mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime, UTC).isoformat()


@campaigns_router.get(
    "/campaigns/{cycle_id}/files",
    response_model=FilesResponse,
)
async def list_cycle_files(store: StoreDep, cycle_id: str) -> FilesResponse:
    """Recursive file tree for the cycle dir + family-root telemetry artifacts."""
    cycle_dir = campaign_dir_for(store.base_dir, cycle_id)
    root_dir = root_dir_for(store.base_dir, cycle_id)
    if not cycle_dir.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")

    is_fork = cycle_dir.resolve() != root_dir.resolve()
    entries: list[FileEntry] = []

    for f in _walk_files(cycle_dir):
        entries.append(
            FileEntry(
                path=f.relative_to(cycle_dir).as_posix(),
                scope="cycle",
                size=f.stat().st_size,
                mtime=_iso_mtime(f),
            )
        )
        if len(entries) > _MAX_FILE_ENTRIES:
            raise HTTPException(413, f"Too many entries in cycle dir (>{_MAX_FILE_ENTRIES})")

    if is_fork:
        for name in _FAMILY_FILE_LEVEL_ARTIFACTS:
            f = root_dir / name
            if f.is_file():
                entries.append(
                    FileEntry(
                        path=name,
                        scope="family",
                        size=f.stat().st_size,
                        mtime=_iso_mtime(f),
                    )
                )

    return FilesResponse(cycle_id=cycle_id, is_fork=is_fork, entries=entries)


def _resolve_safe_file(scope_root: Path, raw_path: str) -> Path:
    """Validate *raw_path* against escape attempts and confine it under *scope_root*."""
    if not raw_path or ".." in raw_path or "\\" in raw_path or raw_path.startswith("/"):
        raise HTTPException(400, "Invalid path")
    scope_root_resolved = scope_root.resolve()
    resolved = (scope_root / raw_path).resolve()
    if not resolved.is_relative_to(scope_root_resolved):
        raise HTTPException(400, "Path escapes scope root")
    if not resolved.is_file():
        raise HTTPException(404, f"File not found: {raw_path}")
    return resolved


_TEXT_SUFFIXES = {".txt", ".jsonl", ""}


def _classify_suffix(suffix: str) -> Literal["json", "markdown", "log", "text"] | None:
    if suffix == ".json":
        return "json"
    if suffix == ".md":
        return "markdown"
    if suffix == ".log":
        return "log"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    return None


@campaigns_router.get(
    "/campaigns/{cycle_id}/file",
    response_model=FileContentResponse,
)
async def get_cycle_file(
    store: StoreDep,
    cycle_id: str,
    scope: Literal["cycle", "family"] = Query(..., description="cycle | family"),
    path: str = Query(..., description="Relative path under the chosen scope root"),
) -> FileContentResponse:
    """Read the contents of one file under the cycle or family-root scope."""
    if scope == "cycle":
        scope_root = campaign_dir_for(store.base_dir, cycle_id)
    else:
        scope_root = root_dir_for(store.base_dir, cycle_id)
    if not scope_root.exists():
        raise HTTPException(404, f"Cycle '{cycle_id}' not found")

    resolved = _resolve_safe_file(scope_root, path)
    size = resolved.stat().st_size
    mtime = _iso_mtime(resolved)
    classification = _classify_suffix(resolved.suffix.lower())

    if size > _MAX_PREVIEW_BYTES:
        return FileContentResponse(
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=None,
        )

    if classification is None:
        try:
            text = resolved.read_text(encoding="utf-8")
            return FileContentResponse(
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="text",
                content=text,
            )
        except UnicodeDecodeError:
            return FileContentResponse(
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="binary",
                content=None,
            )

    if classification == "text":
        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return FileContentResponse(
                cycle_id=cycle_id,
                scope=scope,
                path=path,
                size=size,
                mtime=mtime,
                content_type="binary",
                content=None,
            )
        return FileContentResponse(
            cycle_id=cycle_id,
            scope=scope,
            path=path,
            size=size,
            mtime=mtime,
            content_type="text",
            content=text,
        )

    return FileContentResponse(
        cycle_id=cycle_id,
        scope=scope,
        path=path,
        size=size,
        mtime=mtime,
        content_type=classification,
        content=resolved.read_text(encoding="utf-8"),
    )


# ===========================================================================
# Datasets — campaign-sourced dataset preview for the New Job view
# ===========================================================================

_datasets_router = APIRouter(prefix="/datasets", tags=["Datasets"])

_DATASET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class DatasetItem(BaseModel):
    sample_id: int
    query: str
    ground_truth: str
    task: str | None = None
    n_obs: int = Field(description="Times this sample has been tried")
    surprise: float = Field(
        description=(
            "Miss-probability for an average candidate, derived from Rasch "
            "delta via sigmoid. 0.5 prior for unmeasured samples (no signal "
            "yet — coin flip)."
        ),
    )


class DatasetPreviewResponse(BaseModel):
    name: str
    row_count: int
    train_count: int = Field(description="Items assigned to optimizer training")
    test_count: int = Field(description="Items held out for test evaluation")
    items: list[DatasetItem]


@_datasets_router.get("/{name}/preview", response_model=DatasetPreviewResponse)
async def get_dataset_preview(
    name: str,
    store: StoreDep,
    backend_id: str = Query(default="local"),
    limit: int = Query(default=50, ge=1, le=1000),
) -> DatasetPreviewResponse:
    """Hard-sample leaderboard for ``{name}`` — every dataset row in Rasch
    difficulty order (hardest first), with unmeasured samples appended at the
    bottom in ``sample_id`` order.

    ``train_count`` = samples with at least one measurement in the archive for
    ``backend_id``. ``test_count`` = samples in the dataset cache that have no
    measurements yet (held out for evaluation). The split is implicit in the
    archive — no separate test cache needed.
    """
    import json

    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_hard_samples_artifact,
    )

    if not _DATASET_NAME_RE.match(name):
        raise HTTPException(400, "Invalid dataset name")
    datasets_root = DEFAULT_DATASETS_ROOT.resolve()
    cache_path = (datasets_root / name / "cache.json").resolve()
    if not cache_path.is_relative_to(datasets_root):
        raise HTTPException(400, "Invalid dataset name")
    if not cache_path.is_file():
        raise HTTPException(404, f"Dataset '{name}' not found")
    raw = json.loads(cache_path.read_text(encoding="utf-8"))

    # Sample-id keying varies on disk: canonical datasets use ``id``; BBEH's
    # HF processor emits ``sample_id``. Normalise to int at the read boundary.
    sample_lookup: dict[int, dict[str, Any]] = {}
    for item in raw["items"]:
        sid = int(item["sample_id"] if "sample_id" in item else item["id"])
        sample_lookup[sid] = item

    import math

    artifact = build_archive_hard_samples_artifact(store, backend_id, top_k_samples=None)
    rasch = artifact.get("rasch", {})
    delta_map: dict[int, float] = {int(k): float(v) for k, v in rasch.get("delta", {}).items()}
    n_obs_map: dict[int, int] = {
        int(k): int(v) for k, v in rasch.get("n_obs_per_sample", {}).items()
    }
    measured = {sid for sid in delta_map if sid in sample_lookup}

    # Surprise = miss-probability for an average candidate (theta=0), via
    # sigmoid(delta). Unmeasured samples get the 0.5 prior (no signal yet —
    # genuinely a coin flip for the optimizer).
    def surprise_of(sid: int) -> float:
        if sid in delta_map:
            return 1.0 / (1.0 + math.exp(-delta_map[sid]))
        return 0.5

    full_order = sorted(sample_lookup.keys(), key=lambda s: (-surprise_of(s), s))

    items = [
        DatasetItem(
            sample_id=sid,
            query=sample_lookup[sid]["query"],
            ground_truth=sample_lookup[sid]["ground_truth"],
            task=sample_lookup[sid].get("task"),
            n_obs=n_obs_map.get(sid, 0),
            surprise=surprise_of(sid),
        )
        for sid in full_order[:limit]
    ]

    return DatasetPreviewResponse(
        name=raw["name"],
        row_count=len(sample_lookup),
        train_count=len(measured),
        test_count=len(sample_lookup) - len(measured),
        items=items,
    )
