"""The tenant-global read-only surface. The evaluator registry is NOT served here (import-time constant, it reaches
the webapp through the generated TS) and neither is live telemetry — that is the per-cycle dashboard route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import Field

from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    optimizer_manifest,
    optimizer_resolved_schemas,
)
from promptpotter.application.optimization.dispatch.schemas import L2_NODE_AXES
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.run_records import MintKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import descend_store
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoresDep,
    decode_descend,
)

active_router = APIRouter()


class ActiveSessionResponse(StrictModel):
    tenant_id: str = Field(
        description="Tenant the pointer belongs to — the caller's own, always known"
    )
    session_id: str | None = Field(description="Active session id; null when no session is active.")
    campaign_id: str | None = Field(
        description="Active campaign id (pinned by the webapp); null when no session is active."
    )
    cycle_id: str | None = Field(
        description="Active cycle id within the campaign; null when no session is active."
    )


@active_router.get("/sessions/active", response_model=ActiveSessionResponse, tags=["Sessions"])
def get_active_session(stores: StoresDep) -> ActiveSessionResponse:
    """The caller-tenant's active-session pointer, null-valued while nothing runs.

    **"No active session" is a STEADY STATE, not a missing resource** — nothing has
    been launched yet, or the workspace was cleared — so it answers 200 with null ids
    exactly as :class:`CyclesResponse` does, and this route has no 404. The webapp
    polls it every 2 s from boot, and a 404 here is wrong twice over: it minted a
    warning per tick for the whole idle life of the server, and 404 is the client's
    ``gone`` classification, which means "stop, this address is dead". Pointers are
    per-tenant on disk; unauthed callers are rejected by ``resolve_identity`` before
    ``StoresDep`` resolves.
    """
    session_id, campaign_id, cycle_id = read_active_pointer(stores.base_dir)
    return ActiveSessionResponse(
        tenant_id=stores.tenant_id,
        session_id=session_id or None,
        campaign_id=campaign_id or None,
        cycle_id=cycle_id or None,
    )


class SpawnedBy(StrictModel):
    """The outer work-item an L4 inner cycle was spawned to measure.

    Stamped at inner-cycle mint (``runner/inner/spawn.py``) — an inner campaign's own
    ids carry no outer provenance (random ``campaign_id``, ``cycle_id`` hashed from its
    OWN origin), so without this the fan-out can only be numbered by launch order.
    """

    outer_cycle_id: str = Field(description="The outer cycle that owns this inner sandbox")
    outer_campaign_id: str = Field(
        description="The outer CAMPAIGN that owns this inner sandbox. Required alongside the cycle because a `cycle_id` is content-addressed on its origin and so is shared by every campaign minted from that origin — the pair is the identity, either half alone is not, and a null here is why two pooled sandboxes on disk could not be attributed after the fact.",
    )
    round: int | None = Field(
        default=None,
        description="Outer round; 0 is the origin (C0). Null when the spawn came from outside any round (the noise-floor diagnostic).",
    )
    candidate_idx: int | None = Field(
        default=None, description="Position in the outer round's population; null for the origin."
    )
    candidate_id: str | None = Field(
        default=None,
        description="The outer candidate's `OptSearchPoint.lineage.id` — stable across rounds; null for the origin.",
    )
    candidate_label: str | None = Field(
        default=None,
        description="Canonical label (`C0` for the origin, else `C{round}.{idx+1}`) — the same string the round file and console use.",
    )
    task: str = Field(
        description="The panel cell this run measured — the outer query, e.g. `justlogic-d234/seed-0` (`inner_tasks.yaml::tasks[].id`). The candidate fields do NOT identify a run: every task runs for every candidate, so one candidate's spawns are as many as the panel has cells and are told apart only by this.",
    )


class CycleListEntry(StrictModel):
    campaign_id: str = Field(description="Campaign the cycle belongs to")
    cycle_id: str
    parent_session_id: str = ""
    parent_cycle_id: str | None = Field(
        default=None,
        description="Immediate parent for siblings (forks/sweeps/diag); null for roots. Sidebar uses this to nest siblings.",
    )
    dataset_name: str = ""
    backend_id: str = ""
    # What minted this cycle — the badge `MINT_KIND_FOR_TRIGGER` declares per fork trigger,
    # projected by `campaign_store/store.py::_mint_kind` off the id's own separator. The raw
    # separator is NOT served beside it: the browser parses the id itself (`lib/ids.ts`).
    mint_kind: MintKind
    is_root: bool
    # Precise terminal reason (StopReason value) once finished, else "active"
    # ("unreadable" for a malformed index). The display label + outcome derive
    # from the one STOP_REASON_INFO table; do not re-map per surface.
    status: str
    superseded_by: str | None = Field(
        default=None,
        description="The cycle_id that took this cycle's line, set on the LEFT-BEHIND side of a supersede cut. This is the successor pointer — follow it to find which cycle answers for the campaign; it is a fact of its own precisely so it survives on a parent that had already stopped for its own reason, which `status` cannot express. Null on a root, an offshoot, and any cycle still holding the line.",
    )
    run_phase: RunPhase = Field(
        default=RunPhase.DETACHED,
        description="The single run-state value (RunPhase). Computed once by derive_run_phase from lifecycle + control flags + freshness; every picker dot and badge reads this, none re-derive it. 'checkin' wins first (the campaign hasn't run); 'terminal' pairs with `status` for the reason label.",
    )
    best_accuracy: float | None = None
    origin_accuracy: float | None = Field(
        default=None,
        description="Round 0's accuracy — the origin's measurement, derived from rounds[] (no stored copy). Null until round 0 lands.",
    )
    n_rounds: int = 0
    created_at: str = ""
    updated_at: str = ""
    human_intervened: bool = Field(
        default=False,
        description="True once an operator manually intervened (e.g. skip-searchpoint); the cycle is babysat and no longer purely reproducible. Drives the 'babysat' badge; orthogonal to run_phase.",
    )
    spawned_by: SpawnedBy | None = Field(
        default=None,
        description=(
            "Which outer work-item asked for this cycle, when it is an L4 inner "
            "measurement; null for an ordinary campaign, which is the only reason it is "
            "null. Lets the sidebar name an inner run by the candidate that produced it "
            "instead of by launch order."
        ),
    )


class CyclesResponse(StrictModel):
    tenant_id: str
    active_campaign_id: str | None = Field(
        description="Active campaign per active_session.json; null when no session is active."
    )
    active_cycle_id: str | None = Field(
        description="Active cycle per active_session.json; null when no session is active."
    )
    cycles: list[CycleListEntry]


@active_router.get("/cycles", response_model=CyclesResponse, tags=["Cycles"])
def get_cycles(stores: StoresDep, descend: str | None = Query(None)) -> CyclesResponse:
    """Every cycle in one store + that store's active pointer — one round-trip per forest.

    ``descend`` names the chain of cycles to descend INTO (``~``-joined
    ``campaign::cycle``, mirroring the webapp's ``CyclePath``); absent/empty is the
    tenant's own tree. Each hop enters that cycle's ``.inner/`` sandbox, so ONE
    route serves the top-level forest, an L4 cycle's inner fan-out, or an L5+
    descendant — a sandbox is structurally a normal projects tree, so the read is
    byte-identical at every depth (:func:`descend_store`).

    The pointer is the store's own: at the top level the active session, inside a
    sandbox the inner loop running right now. It is reported RAW — liveness is
    ``CycleListEntry.run_phase``'s job, and a consumer asking "is the pointed cycle
    live?" reads that off ``cycles[]`` rather than having this route re-derive it.
    """
    leaf = descend_store(stores, decode_descend(descend))
    _, active_cmp, active_cid = read_active_pointer(leaf.base_dir)
    entries = leaf.campaigns.enumerate_cycles()
    return CyclesResponse(
        tenant_id=leaf.tenant_id,
        active_campaign_id=active_cmp or None,
        active_cycle_id=active_cid or None,
        cycles=[CycleListEntry(**e) for e in entries],
    )


class MachineHolder(StrictModel):
    user: str = Field(description="user_id of the operator whose run owns the slot")
    campaign_id: str
    cycle_id: str
    started_at: str | None = Field(
        default=None, description="ISO start time of the holding run; null if still pending"
    )


class MachineQueueEntry(StrictModel):
    job_id: str
    dataset_name: str
    created_at: str
    position: int = Field(
        description="1-based place in the machine-wide drain order at the moment of this read. "
        "Least-served-first, so it moves as other accounts start and finish — it is where this "
        "launch stands now, not a countdown."
    )


class MachineStatusResponse(StrictModel):
    capacity: int = Field(
        description="Campaigns the machine admits right now. Resolved per read against the same "
        "rule a launch is admitted on, and lowered from the operator's ceiling while the shared "
        "provider throttle is saturated."
    )
    running: int = Field(description="Campaigns currently live on the machine.")
    queued: int = Field(
        description="Launches waiting for a slot, machine-wide — an occupancy figure like "
        "`running`, not a list. Who they belong to is deliberately not served; `queue` carries "
        "the caller's own."
    )
    busy: bool = Field(
        description="True iff `running >= capacity` — no slot free for anyone, the caller "
        "included. A launch is then QUEUED rather than refused, so this reads as 'you will wait', "
        "not 'you cannot start'."
    )
    holder: MachineHolder | None = Field(
        default=None,
        description="The oldest live run, whoever owns it; null when nothing is running.",
    )
    queue: list[MachineQueueEntry] = Field(
        default_factory=list,
        description="The CALLER's own waiting launches, oldest first — everything a client needs "
        "to say 'queued, position 3' and to offer a cancel. Other tenants' entries are counted in "
        "`queued` and never listed.",
    )


@active_router.get("/machine-status", response_model=MachineStatusResponse, tags=["Sessions"])
def get_machine_status(identity: IdentityDep, jobs: JobRegistryDep) -> MachineStatusResponse:
    """Machine occupancy — how many campaigns may run here, and how many do.

    What the webapp polls to say the machine is full *before* the operator presses. ``busy`` is
    ``running >= capacity`` against the same :func:`resolve_run_capacity`
    :meth:`JobRegistry.request_slot` admits on, so banner and gate cannot disagree — and it means
    "you will wait", not "you cannot start". Cross-user holder info is intentionally exposed (the
    seed of an admin presence view).

    Occupancy is not relative to who asks, so the caller's own run counts. Excluding it makes
    banner and gate disagree exactly where it matters: on an auth-off box every request is the same
    operator, so the banner reads free while that operator's own launch is queued.

    The QUEUE is served two ways on purpose. ``queued`` is occupancy — how deep the line is,
    which anyone may see because it is a fact about the machine. ``queue`` is the caller's own
    entries with their places in it, which is what a client needs to say "queued, position 3" and
    to offer a cancel; other tenants' waiting launches are counted and never named.

    ``identity`` decides which entries those are, and it is what 401s an unauthenticated read —
    so it was already load-bearing here before the queue, since dropping it would publish
    cross-user holder info.
    """
    # Sync on purpose, like every other read here: `list_running` reads every job file and, on a
    # zombie, writes one and globs the projects tree. On a 5 s always-on poll that belongs in the
    # threadpool, never on the one event loop every other route shares.
    running = jobs.list_running()
    live = len(running)
    capacity = resolve_run_capacity(live)
    oldest = jobs.holder()
    # ONE ordering, the drain's own, so a position the browser shows is the position the queue
    # will honour. Numbering the caller's slice separately would read 1, 2, 3 to everyone.
    order = jobs.queue_order()
    mine = str(identity.user_id)
    return MachineStatusResponse(
        capacity=capacity,
        running=live,
        queued=len(order),
        busy=live >= capacity,
        holder=None
        if oldest is None
        else MachineHolder(
            user=oldest.user_id,
            campaign_id=oldest.campaign_id,
            cycle_id=oldest.cycle_id,
            started_at=oldest.started_at,
        ),
        queue=[
            MachineQueueEntry(
                job_id=job.job_id,
                dataset_name=job.dataset_name,
                created_at=job.created_at,
                position=index,
            )
            for index, job in enumerate(order, 1)
            if job.user_id == mine
        ],
    )


@active_router.get("/optimizer-pipeline", tags=["Optimizer"])
def get_optimizer_pipeline() -> dict[str, Any]:
    """Bundled ``promptpotter/assets/optimizer/pipeline.yaml`` + its generated
    ``resolved_schemas.json`` sibling — nodes + pipelines + ``view``
    topology, plus the per-node typed config surface (``node_config_schema`` /
    ``node_output_schema``) so the canvas node-detail renders the optimizer's own
    knobs (model / provider / reasoning_effort / temperature / …) through the same
    canonical config element the steer panel uses, not a hand-rolled chip + JSON
    dump. Read-only: the install-global ``_optimizer`` pipeline is operator-owned — a
    hand-edit, never a fork and never a write path from here (``evidence``
    names a winner and writes nothing); model/provider are always optimizer-locked."""
    # The manifest is already parsed + cached one layer down; re-reading the file here
    # was a second opinion on the same bytes. Copied because the response is mutated below.
    pipeline: dict[str, Any] = dict(optimizer_manifest())
    pipeline["resolved_schemas"] = optimizer_resolved_schemas()
    # The manifest's own `pipelines` block, unflattened: `PipelineSchema.config_nodes` is
    # what reaches the nodes no round runs, for this door and the other two alike, so the
    # block stays free to shape the graph.
    schema = parse_pipeline_response(pipeline)
    pipeline["view"] = schema.view.model_dump(by_alias=True) if schema.view else None
    # This is the OPTIMIZER's own manifest, so it is the one route that names L2's axes.
    pipeline["node_config_schema"] = {
        node: [p.model_dump() for p in params]
        for node, params in schema.node_config_schema(L2_NODE_AXES).items()
    }
    pipeline["node_output_schema"] = {
        node: (out.model_dump() if out is not None else None)
        for node, out in schema.node_output_schemas().items()
    }
    return pipeline


__all__ = [
    "ActiveSessionResponse",
    "CycleListEntry",
    "CyclesResponse",
    "MachineHolder",
    "MachineQueueEntry",
    "MachineStatusResponse",
]
