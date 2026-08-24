"""The tenant-global read-only surface. The evaluator registry is NOT served here (import-time constant, it reaches
the webapp through the generated TS) and neither is live telemetry — that is the per-cycle dashboard route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import Field

from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    optimizer_manifest,
    optimizer_resolved_schemas,
)
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


class MachineStatusResponse(StrictModel):
    busy: bool = Field(
        description="True iff a *different* user holds a running job (the server runs one campaign at a time)."
    )
    holder: MachineHolder | None = Field(
        default=None, description="Who holds the slot; null when free for this caller."
    )


@active_router.get("/machine-status", response_model=MachineStatusResponse, tags=["Sessions"])
async def get_machine_status(identity: IdentityDep, jobs: JobRegistryDep) -> MachineStatusResponse:
    """Whether another user is currently running a campaign on this machine.

    The server is single-process and runs campaigns in sequence, so a launch
    is rejected (409 ``machine_busy``) while anyone else's run is live. This
    read is the poll that lets the webapp surface that state as a critical-alert
    banner *before* the operator tries — the always-on twin of the 409. Reads
    the same :meth:`JobRegistry.machine_holder` the launch gate uses, so banner
    and gate never disagree. Cross-user holder info is intentionally exposed
    (the seed of an admin presence view).
    """
    holder = jobs.machine_holder(exclude_user_id=str(identity.user_id))
    if holder is None:
        return MachineStatusResponse(busy=False)
    return MachineStatusResponse(
        busy=True,
        holder=MachineHolder(
            user=holder.user_id,
            campaign_id=holder.campaign_id,
            cycle_id=holder.cycle_id,
            started_at=holder.started_at,
        ),
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
    # Every DECLARED node, not just the default pipeline's three. `parse_pipeline_response`
    # takes its node set from ``pipelines.default``, which is right for a dataset (the steps
    # that run) and wrong here: the optimizer picks its path per escalation, so `l2_context`
    # and `l3_plan` sit in the escalation pipelines and `checkin` in none at all — yet the
    # `view` this same response serves draws all six. The engine's own reader
    # (``get_optimizer_schema``) already walks ``nodes`` whole; this route was the odd one out,
    # and the three it dropped rendered as "declares no configurable params" while each
    # declares model, provider, reasoning_effort, temperature and max_tokens.
    schema = parse_pipeline_response(
        {**pipeline, "pipelines": {"default": list(pipeline.get("nodes", {}))}}
    )
    pipeline["node_config_schema"] = {
        node: [p.model_dump() for p in params]
        for node, params in schema.node_config_schema().items()
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
    "MachineStatusResponse",
]
