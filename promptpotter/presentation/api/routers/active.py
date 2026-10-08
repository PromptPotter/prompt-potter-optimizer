"""The tenant-global read-only surface. The evaluator registry is NOT served here (import-time constant, it reaches
the webapp through the generated TS) and neither is live telemetry — that is the per-cycle dashboard route."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import Field

from promptpotter.application.campaign_config import OptimizationConfig
from promptpotter.application.jobs.capacity import resolve_run_capacity
from promptpotter.application.optimizer_manifest import (
    OptimizerKnobsResponse,
    OptimizerRoster,
    optimizer_knobs,
    optimizer_roster,
    resolve_optimizer,
)
from promptpotter.application.pipeline_resolve import measurement_node
from promptpotter.config.settings import settings
from promptpotter.domain.cycle_listing import CycleListEntry
from promptpotter.domain.pipeline_schema import (
    CapabilityMenu,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    PipelineView,
    reach_map,
)
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.capabilities import resolve_schema_menu
from promptpotter.infrastructure.store.session_pointer import read_active_pointer
from promptpotter.infrastructure.store.stores import descend_store
from promptpotter.presentation.api.deps import (
    IdentityDep,
    JobRegistryDep,
    StoresDep,
    decode_descend,
)
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json

active_router = APIRouter()


class ActiveSessionResponse(StrictModel):
    """The tenant's latest launch — not the set of live runs, which is `run_phase` on `/cycles`."""

    tenant_id: str = Field(
        description="Tenant the pointer belongs to — the caller's own, always known"
    )
    session_id: str | None = Field(description="Active session id; null when no session is active.")
    campaign_id: str | None = Field(
        description="Campaign of the latest launch; null when no session is active."
    )
    cycle_id: str | None = Field(
        description="Cycle of the latest launch; null when no session is active."
    )


@active_router.get("/sessions/active", response_model=ActiveSessionResponse, tags=["Sessions"])
def get_active_session(stores: StoresDep) -> ActiveSessionResponse:
    """The caller-tenant's active-session pointer: its LATEST launch from any entry point, the
    terminal's default ``resume`` target and what an unpinned webapp follows. Several runs can be
    live at once, and each is a ``/cycles`` entry whose ``run_phase`` says so — never this route.

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
def get_cycles(request: Request, stores: StoresDep, descend: str | None = Query(None)) -> Response:
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
    return conditional_json(
        request,
        CyclesResponse(
            tenant_id=leaf.tenant_id,
            active_campaign_id=active_cmp or None,
            active_cycle_id=active_cid or None,
            cycles=leaf.campaigns.enumerate_cycles(),
        ),
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
    ceiling: int = Field(
        description="The operator's `MACHINE_RUN_CAPACITY` — set in the server environment and "
        "writable nowhere else. `capacity` never exceeds it, and neither may an account's limit."
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
        ceiling=settings.MACHINE_RUN_CAPACITY,
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


class OptimizerPipelineResponse(StrictModel):
    """What the OPTIMIZER runs — the manifest's peer of ``GET /campaigns/{id}/pipeline``. The raw
    manifest keys are not served: ``nodes`` was a second, untyped spelling of ``node_config_schema``."""

    view: PipelineView | None = Field(
        description="The graph topology — the same shape a campaign pipeline serves"
    )
    measurement_node: str | None = Field(
        description="The node of `view` that runs the measurement — where a campaign's pipeline "
        "nests under this graph, as `nests.node` names it per campaign. Null where the manifest "
        "declares no measurement node."
    )
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Per-node typed config rows, so the node detail renders the optimizer's own "
        "knobs through the canonical config element rather than a chip and a JSON dump"
    )
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Optimizer-LOCKED is not unpriced: the model is fixed, but which effort rungs "
        "it accepts and what a round costs are the facts every other node's rows need too"
    )
    reach: dict[str, NodeReach] = Field(
        description="Where the search reaches per node, summed off the rows above rather than in "
        "the browser — the same reading a campaign pipeline serves"
    )
    resolved_prompts: dict[str, dict[str, Any]] = Field(
        description="The prompt each node STARTS from, keyed `{node}/{version}` — the floor under "
        "a searchpoint carrying no evolved delta for that node"
    )


@active_router.get(
    "/optimizer-pipeline", tags=["Optimizer"], response_model=OptimizerPipelineResponse
)
def get_optimizer_pipeline(
    stores: StoresDep,
    optimizer: str = Query(
        default=OptimizationConfig.model_fields["optimizer"].default,
        description="The registered optimizer whose manifest to read",
    ),
) -> OptimizerPipelineResponse:
    """One optimizer manifest (the ``pipeline.yaml`` its runtime ships) + its generated
    ``resolved_schemas.json`` sibling — the ``view`` topology plus the per-node
    typed config surface, so the canvas node-detail renders the optimizer's own knobs through the
    same canonical config element the steer panel uses. Read-only: the manifest is operator-owned —
    a hand-edit, never a fork and never a write path from here; a campaign's changes ride its own
    ``optimization.nodes``. model/provider are always optimizer-locked."""
    # The engine's own parse: a second one here had the browser and the engine disagree on menus.
    selected = resolve_optimizer(optimizer, {})
    schema = selected.schema
    prompts = selected.document.get("resolved_prompts") or {}
    # This is the OPTIMIZER's own manifest, so it is the one route that names the axes it moves on
    # itself — and the reach below must sum the SAME rows it serves, or the glyph and the padlock
    # disagree.
    rows = schema.node_config_schema(selected.runtime.own_axes)
    return OptimizerPipelineResponse(
        view=schema.view,
        measurement_node=measurement_node(schema.view),
        node_config_schema=rows,
        node_output_schema=schema.node_output_schemas(),
        reach=reach_map(rows),
        # Per tenant, because the hand-authored override that corrects a wrong catalogue lives in
        # their workspace — which is what makes ``StoresDep`` load-bearing here, not decoration.
        model_capabilities=resolve_schema_menu(schema, workspace=Path(stores.base_dir)),
        resolved_prompts={str(k): dict(v) for k, v in prompts.items()},
    )


@active_router.get("/optimizers", tags=["Optimizer"], response_model=OptimizerRoster)
def get_optimizers() -> OptimizerRoster:
    """The optimizers this install can run — one per registered runtime, each resolved through the
    manifest a run would read — so a picker offers what ``optimization.optimizer`` accepts."""
    return optimizer_roster()


@active_router.get(
    "/optimizers/{name}/knobs", tags=["Optimizer"], response_model=OptimizerKnobsResponse
)
def get_optimizer_knobs(name: str) -> OptimizerKnobsResponse:
    """Every knob the manifest's nodes take — type, closed options, bounds, the value the manifest
    declares — so a settings surface draws one control per knob and writes a campaign's
    ``optimization.nodes.{node}.config.{key}``. 404 when no such optimizer is registered."""
    return optimizer_knobs(name)


__all__ = [
    "ActiveSessionResponse",
    "CyclesResponse",
    "MachineHolder",
    "MachineQueueEntry",
    "MachineStatusResponse",
]
