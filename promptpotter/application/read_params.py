"""A read's wire inputs and their binding: one model per `domain/command_kinds.py::ReadKind` row,
named by the row, whose `answer` turns what a door sent into its application callable's call.

Every door reads through it: a route builds the model from its path and query, a host from a tool
call's arguments (`host_surface.py`), so a parse or a resolution lives here once.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AfterValidator, Field

from promptpotter.application import account, cycle_files
from promptpotter.application.backend_listing import (
    BackendHealthResponse,
    BackendResponse,
    backend_health,
    list_backends,
)
from promptpotter.application.caller import Caller
from promptpotter.application.command_reading import CommandReading, read_command
from promptpotter.application.cycle_addressing import (
    ActiveSessionResponse,
    active_session,
    resolve_subject,
)
from promptpotter.application.cycle_files import FileContentResponse, FileScope, FilesResponse
from promptpotter.application.cycle_reads import (
    FamilyRay,
    RoundView,
    ViewedCycle,
    cycle_event_frames,
    export_cycle,
    open_family_ray,
    round_audit,
    served_round,
    view_cycle,
)
from promptpotter.application.datasets.draft_build import DraftReopenResponse, reopen_draft
from promptpotter.application.evidence.metric_catalogue import MEASURAND
from promptpotter.application.evidence.read import Evidence, select_evidence
from promptpotter.application.head_to_head_price import HeadToHeadPrice, price_head_to_head
from promptpotter.application.hierarchy import list_hierarchy
from promptpotter.application.hierarchy_subtree import hierarchy_subtree, subtree_moment
from promptpotter.application.jobs.account_activity import (
    ActivityGroupBy,
    ActivityResponse,
    ActivityWindow,
    account_activity,
)
from promptpotter.application.jobs.machine_status import MachineStatusResponse, machine_status
from promptpotter.application.jobs.quota import (
    CouponStatus,
    QuotaStatus,
    coupon_status,
    quota_status,
)
from promptpotter.application.knobs import ConfigMapResponse
from promptpotter.application.library import LibraryResponse, library
from promptpotter.application.maintenance.storage_report import (
    LeafBytes,
    WorkspaceStorageResponse,
    campaign_storage,
    workspace_storage,
)
from promptpotter.application.origin_listing import (
    DatasetIndexResponse,
    OriginListResponse,
    dataset_index,
    origin_index,
)
from promptpotter.application.pipeline_resolve import (
    campaign_config_map,
    fork_preview,
    resolve_pipeline_at,
    resolve_pipeline_for_dataset,
)
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    DatasetPipelineResponse,
    ForkPreviewResponse,
    OptimizerRoster,
    optimizer_roster,
)
from promptpotter.application.scoring.measurement_log import (
    CellsScope,
    cells_scope,
    measurement_log,
    open_cell,
)
from promptpotter.application.served_dashboard import (
    ServedDashboard,
    WarmingDashboard,
    served_dashboard,
)
from promptpotter.application.views.render.export import ExportFiles
from promptpotter.domain.cells import Cell, CellsResponse, HardSampleOrder, HeatmapScope
from promptpotter.domain.command_kinds import COMMAND_ID_CHARS, ReadKind
from promptpotter.domain.cycle_paths import (
    CyclePath,
    Subject,
    decode_cycle_path,
    parse_lens,
    parse_sample_ids,
)
from promptpotter.domain.dataset_name import validate_dataset_name
from promptpotter.domain.export import ExportTier
from promptpotter.domain.head_to_head_plan import HeadToHeadPlan
from promptpotter.domain.hierarchy import (
    DEFAULT_STEP_LIMIT,
    MAX_STEP_LIMIT,
    HierarchyEntry,
    HierarchyResponse,
    HierarchySteps,
    ListingLifecycle,
)
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.scoring import SampleStatus
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError
from promptpotter.shared.named_rows import require_own_docstring

_DESCEND = (
    "The L4 inner-cycle tail: `~`-joined `campaign::cycle` hops below the root hop, the "
    "hierarchy address's `inside` byte-exact."
)


# Filled as each model declares the row it answers: the ONE row → params table.
PARAMS_FOR_READ: dict[ReadKind, type[ReadParams]] = {}


# Parsed where the params parse: a malformed name is the caller's 422, never a store's 500.
DatasetName = Annotated[str, AfterValidator(validate_dataset_name)]


class ReadParams(StrictModel):
    """What a read takes off the wire; its `answer(caller)` is the row's answer, sync or async.

    A row's model declares its own docstring, its HTTP `route` and its `method`: the docstring is
    the read's one description, a host tool's and the route's alike, and the route is where
    `api/routers/reads.py` mounts it (a GET's fields not in the route ride the query, a POST's its
    body).
    """

    read: ClassVar[ReadKind]
    route: ClassVar[str]
    method: ClassVar[Literal["GET", "POST"]]

    def __init_subclass__(
        cls,
        read: ReadKind | None = None,
        route: str = "",
        method: Literal["GET", "POST"] = "GET",
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        if read is None:
            return
        require_own_docstring(cls, read)
        if read in PARAMS_FOR_READ:
            raise TypeError(f"{read} already answers through {PARAMS_FOR_READ[read].__name__}")
        if not route.startswith("/"):
            raise TypeError(f"{cls.__name__} declares no route for {read}")
        cls.read, cls.route, cls.method = read, route, method
        PARAMS_FOR_READ[read] = cls

    def answer(self, caller: Caller) -> object:
        """Overridden by each row's model; the override's return is the read's answer model."""
        raise NotImplementedError(type(self).__name__)

    async def answered(self, caller: Caller) -> object:
        """`answer`, awaited by every door alike: a sync answer reads the disk, so it runs off the
        event loop (`off_loop`)."""
        if inspect.iscoroutinefunction(type(self).answer):
            return await self.answer(caller)  # type: ignore[misc]
        return await off_loop(self.answer, caller)


# The reads' own workers, as many as the threads a sync route had: a read never queues behind a
# command's offloaded work on the loop's default executor.
_READ_WORKERS = ThreadPoolExecutor(max_workers=40, thread_name_prefix="read")


async def off_loop[*A, T](work: Callable[[*A], T], *args: *A) -> T:
    """A read's disk work on the reads' workers, in the caller's context: every read door's one
    offload, `answered`'s and a framed route's alike."""
    run = functools.partial(contextvars.copy_context().run, work, *args)
    return await asyncio.get_running_loop().run_in_executor(_READ_WORKERS, run)


class _CycleAddressed(ReadParams):
    campaign_id: str = Field(min_length=1, max_length=128)
    cycle_id: str = Field(min_length=1, max_length=128)
    descend: str | None = Field(default=None, max_length=512, description=_DESCEND)

    @property
    def path(self) -> CyclePath:
        return Subject.below(self.campaign_id, self.cycle_id, self.descend).path

    def viewed(self, stores: Stores) -> ViewedCycle:
        return view_cycle(stores, self.path)


class MeParams(ReadParams, read=ReadKind.ME, route="/auth/me"):
    """The caller's identity envelope; ``available_providers`` is the configured ones minus the
    connected one."""

    def answer(self, caller: Caller) -> account.MeResponse:
        stores = caller.stores
        return account.me(stores, account.account_read(stores), configured=caller.providers)


class UserSettingsParams(ReadParams, read=ReadKind.USER_SETTINGS, route="/auth/user-settings"):
    """The current user's preferences."""

    def answer(self, caller: Caller) -> account.UserSettings:
        return account.user_settings(caller.stores)


class QuotaStatusParams(ReadParams, read=ReadKind.QUOTA_STATUS, route="/auth/quota-status"):
    """The caller's live quota: usage is unclamped, and the ceilings are the resolved ones, never
    the raw nullable overrides."""

    def answer(self, caller: Caller) -> QuotaStatus:
        return quota_status(stores=caller.stores, job_registry=caller.jobs)


class CouponParams(ReadParams, read=ReadKind.COUPON, route="/auth/coupon"):
    """The host's allowance for the caller on the box's keys: its face, what is left, and the keys
    the caller sends on past it. What is left is derived from the ledgers, never a counter."""

    def answer(self, caller: Caller) -> CouponStatus:
        return coupon_status(caller.stores)


class ActivityParams(ReadParams, read=ReadKind.ACTIVITY, route="/auth/activity"):
    """Time-bucketed spend, requests and tokens over the window, each billed call at the price it
    was stamped with."""

    window: ActivityWindow = "1d"
    group_by: ActivityGroupBy = Field(
        default="model",
        description="`model` (the exact model string) or `api_key` (the provider that billed the "
        "call, and whether the host's key or the caller's own sent it).",
    )

    def answer(self, caller: Caller) -> ActivityResponse:
        return account_activity(caller.stores, window=self.window, group_by=self.group_by)


class ActiveSessionParams(ReadParams, read=ReadKind.ACTIVE_SESSION, route="/sessions/active"):
    """The caller-tenant's active-session pointer: its latest launch from any entry point.

    No active session is a steady state: it answers null ids, never a not-found.
    """

    def answer(self, caller: Caller) -> ActiveSessionResponse:
        return active_session(caller.stores)


class MachineStatusParams(ReadParams, read=ReadKind.MACHINE_STATUS, route="/machine-status"):
    """Machine occupancy: how many campaigns may run here and how many do, and the slots the
    caller may see."""

    def answer(self, caller: Caller) -> MachineStatusResponse:
        return machine_status(caller.jobs, identity=caller.stores.identity)


class CommandParams(ReadParams, read=ReadKind.COMMAND, route="/commands/{command_id}"):
    """One command the caller filed, by its id: where it stands, at the address it was filed
    on; a launch's by the jobs it launched. Not found for another's."""

    command_id: str = Field(min_length=1, max_length=COMMAND_ID_CHARS)

    def answer(self, caller: Caller) -> CommandReading:
        return read_command(caller.stores, caller.jobs, self.command_id)


class OptimizersParams(ReadParams, read=ReadKind.OPTIMIZERS, route="/optimizers"):
    """The optimizers this install can run, one per registered manifest: what
    ``optimization.optimizer`` accepts."""

    def answer(self, caller: Caller) -> OptimizerRoster:  # noqa: ARG002 -- install-wide
        return optimizer_roster()


class LibraryParams(ReadParams, read=ReadKind.LIBRARY, route="/library"):
    """Every snippet the caller's library holds, the install's packs beside its own, for a target
    or an optimizer prompt alike."""

    def answer(self, caller: Caller) -> LibraryResponse:
        return library(caller.stores.block_packs)


class BackendsParams(ReadParams, read=ReadKind.BACKENDS, route="/backends"):
    """Every registered connector, at the endpoint the operator configured for it."""

    def answer(self, caller: Caller) -> list[BackendResponse]:  # noqa: ARG002 -- install-wide
        return list_backends()


class BackendHealthParams(
    ReadParams, read=ReadKind.BACKEND_HEALTH, route="/backends/{backend_id}/health"
):
    """Whether a launch against this backend would be admitted, by the reading admission itself
    takes.

    A down backend is an answer, never an error; only an unregistered ``backend_id`` is not found.
    """

    backend_id: str = Field(min_length=1, max_length=128)

    async def answer(self, caller: Caller) -> BackendHealthResponse:  # noqa: ARG002 -- install-wide
        return await backend_health(self.backend_id)


class HierarchyParams(ReadParams, read=ReadKind.HIERARCHY, route="/hierarchy"):
    """Every campaign in one store the caller owns, as hierarchy entries, each with its courses.

    ``descend`` enters an inner store exactly as every per-cycle read does.
    """

    descend: str | None = Field(default=None, max_length=512, description=_DESCEND)
    dataset: str | None = Field(default=None, description="Filter to one dataset")
    lifecycle: ListingLifecycle = Field(
        default="active",
        description="'active' (default), 'archived' or 'deleted' — the visibility intent; "
        "'unstarted' — the open check-ins; or 'all'",
    )
    campaign: str | None = Field(default=None, description="Filter to one campaign id")
    attached: bool = Field(default=False, description="Keep only the courses a producer holds")
    depth: int = Field(
        default=1, ge=0, le=1, description="0 serves campaigns alone, 1 their courses"
    )

    @property
    def inside(self) -> CyclePath:
        return decode_cycle_path(self.descend)

    def answer(self, caller: Caller) -> HierarchyResponse:
        stores, inside = caller.stores, self.inside
        named = None if self.campaign is None else Subject(self.campaign, inside=inside)
        return list_hierarchy(
            stores,
            inside=inside,
            dataset=self.dataset,
            lifecycle=self.lifecycle,
            campaign_id=None if named is None else resolve_subject(stores, named).campaign_id,
            attached_only=self.attached,
            depth=self.depth,
        )


class HierarchyStepsParams(
    ReadParams, read=ReadKind.HIERARCHY_STEPS, route="/hierarchy/{address:path}/@"
):
    """The course's chronology: its steps, its forks' and its inner runs', merged by time.

    The replay read: the SSE tail always seeks to EOF, so history has no other home. Windowed
    newest-first, delivered oldest-first; ``before`` is the opaque ``cursor_prev`` of a prior
    window (absent = the head window, malformed = refused). Consecutive windows page back without
    overlap or hole; any window revalidates with ``If-None-Match``.
    """

    address: str = Field(min_length=1, description="A course's hierarchy address")
    limit: int = Field(default=DEFAULT_STEP_LIMIT, ge=1, le=MAX_STEP_LIMIT)
    before: str | None = Field(default=None, description="A prior window's `cursor_prev`")

    def opened(self, stores: Stores) -> FamilyRay:
        return open_family_ray(
            stores, Subject.parse_address(self.address).path, limit=self.limit, before=self.before
        )

    def answer(self, caller: Caller) -> HierarchySteps:
        return self.opened(caller.stores).window()


class HierarchySubtreeParams(
    ReadParams, read=ReadKind.HIERARCHY_SUBTREE, route="/hierarchy/{address:path}"
):
    """The entry at ``address`` with every level under it: courses, rounds, candidates, inner
    campaigns.

    ``at`` is a line of the ledger of ``at_cycle``: a course minted later is not in the subtree,
    and run-state stays live. ``lens`` re-grades rows under another ``per_cell`` composite
    (``score:``), the same as weights (``dials:``), or with a gate off (``abort:<gate>_off`` or
    ``abort:all_off``); ``samples`` masks to those sample ids. With neither, the subtree is the
    raw read. Not found where no entry stands at ``address``.
    """

    address: str = Field(min_length=1, description="The entry's hierarchy address")
    lens: str | None = Field(
        default=None,
        description="`score:<formula>`, `dials:<term=weight,…>` or `abort:<variant>`",
    )
    samples: str | None = Field(default=None, description="A comma-separated sample-id mask")
    at: int | None = Field(default=None, ge=0, description="A line of `at_cycle`'s own ledger")
    at_cycle: str | None = Field(
        default=None, description="A whole cycle path, root hop included; absent, the root's"
    )

    def answer(self, caller: Caller) -> HierarchyEntry:
        stores = caller.stores
        return hierarchy_subtree(
            stores,
            self.address,
            parse_lens(self.lens, allow_abort=True) if self.lens else None,
            parse_sample_ids(self.samples),
            moment=subtree_moment(stores, self.address, self.at, decode_cycle_path(self.at_cycle)),
        )


class CycleDashboardParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_DASHBOARD,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard",
):
    """Live telemetry for the viewed cycle: the fold of its own ledger chain.

    The ids address the root cycle; ``descend`` walks ``.inner/`` sandboxes one
    ``campaign::cycle`` hop at a time. A cycle with no declared wiring answers ``warming_up``, an
    absent one is not found; over HTTP it honours ``If-Modified-Since`` with a 304. ``at`` is an
    offset in the LEAF cycle's own ledger, the moment a hierarchy step's ``facts.offset``
    carries: the state as of that line.
    """

    at: int | None = Field(default=None, ge=0, description="A line of the leaf cycle's own ledger")

    def served(self, cycle: ViewedCycle) -> ServedDashboard | WarmingDashboard:
        """Off a cycle already viewed, which a door reads its validator from first."""
        return served_dashboard(cycle.stores, cycle.hop, at=self.at)

    def answer(self, caller: Caller) -> ServedDashboard | WarmingDashboard:
        return self.served(self.viewed(caller.stores))


class CycleRoundParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_ROUND,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}",
):
    """One closed round: its last close's arms and verdict, and the rows it names, each carrying
    its mark as ``status``.

    A fork answers for the rounds it inherited; a round that does not stand closed is not found.
    ``at`` is the dashboard read's: the round as it stood at that line of the cycle's own ledger.
    """

    round_num: int = Field(ge=0)
    at: int | None = Field(default=None, ge=0, description="A line of the leaf cycle's own ledger")

    def answer(self, caller: Caller) -> RoundView:
        cycle = self.viewed(caller.stores)
        closed = served_round(cycle.stores, cycle.hop, self.round_num, moment=cycle.moment(self.at))
        if closed is None:
            raise NotFoundError(
                f"Round {self.round_num} of '{cycle.hop.campaign_id}/{cycle.hop.cycle_id}' is not closed"
            )
        return closed


class CycleRoundAuditParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_ROUND_AUDIT,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}/audit",
):
    """The round's audit twin: the per-node LLM I/O, which the round beside it does not carry.

    A fork answers for the rounds it inherited; ``null`` where the ledger holds none: a round
    still open, or one that ran no optimizer call.
    """

    round_num: int = Field(ge=0)

    def answer(self, caller: Caller) -> RoundAudit | None:
        return round_audit(self.viewed(caller.stores), self.round_num)


class CycleExportParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_EXPORT,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/export",
):
    """One reading of the cycle, chosen by what its reader does next — the CLI's ``export --tier``.

    ``chat`` and ``report`` are text to paste, ``deploy`` is ``export.json``; ``notebook`` and
    ``package`` are several files, served as one zip. ``deploy`` and ``package`` are refused as a
    conflict until the cycle has written ``export.json``.
    """

    tier: ExportTier

    def answer(self, caller: Caller) -> ExportFiles:
        return export_cycle(caller.stores, self.path, self.tier)


class CycleEventsParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_EVENTS,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe",
):
    """Subscribe to a cycle's live ledger over SSE, at any depth through ``descend``.

    Not found only for an unknown cycle; a running, paused or finished one answers one
    ``stream_snapshot`` frame (the served dashboard, ``sequence`` = the offset the tail picks up
    at), then every later record as a ``ProjectionEnvelope`` (``kind`` = its ``record_type``,
    ``sequence`` = its ledger offset), with a heartbeat comment every 15 s.
    """

    def answer(self, caller: Caller) -> AsyncIterator[str]:
        return cycle_event_frames(self.viewed(caller.stores))


class CycleFilesParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_FILES,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/files",
):
    """The recursive file tree of the cycle directory plus the campaign-level artifacts, at any
    depth through ``descend``."""

    def answer(self, caller: Caller) -> FilesResponse:
        return cycle_files.list_cycle_files(caller.stores, self.path)


class CycleFileParams(
    _CycleAddressed,
    read=ReadKind.CYCLE_FILE,
    route="/campaigns/{campaign_id}/cycles/{cycle_id}/file",
):
    """The contents of one file under the cycle or campaign scope; with ``descend``, the inner
    cycle's, never the outer root's."""

    scope: FileScope
    # `path` on the wire; the cycle's own `path` is the address.
    file: str = Field(
        min_length=1, alias="path", description="Relative path under the chosen scope root"
    )

    def answer(self, caller: Caller) -> FileContentResponse:
        return cycle_files.read_cycle_file(
            caller.stores, self.path, scope=self.scope, file=self.file
        )


class EvidenceParams(ReadParams, read=ReadKind.EVIDENCE, route="/evidence"):
    """The cross-subject read: the bench head-to-head, roster, comparability, replicates and
    decomposition, then a merged interval per subject with every pairwise test under the selected
    metric."""

    subject: list[str] = Field(
        default_factory=list,
        description=(
            "Subjects to pool, one per repetition of the parameter: "
            "`campaign:<campaign_id>` (its root origin), `course:<campaign_id>/<cycle_id>` "
            "(one branch, read at its last elected winner) or "
            "`candidate:<campaign_id>/<cycle_id>/<candidate_id>` (one searchpoint). May span "
            "campaigns and datasets, which the comparability verdict then reports on.\n\n"
            "An L4 inner run is addressed like any other, plus the sandbox chain it lives "
            "in: `;in=<campaign>::<cycle>`, root-first and `~`-joined, the same codec as "
            "`?descend=` because it is the same question. A sandbox is structurally an "
            "ordinary projects tree, so every kind above resolves inside one unchanged.\n\n"
            "A MASK rides the address, `;`-separated, so the record and the "
            "counterfactual can be two channels of one read: `;samples=3,7,11` restricts "
            "every value to those samples, and `;lens=score:<formula>` (courses only) "
            "re-decides the branch's elections under another criterion and reads it at the "
            "winner that would have stood. The response carries what that changed in "
            "`subjects[].scenario`, worded once as `scenario.said`, its caveat the said's detail."
        ),
    )
    dataset: str = Field(
        default="",
        description=(
            "Expand to one `campaign:` subject per campaign measuring the bank this dataset "
            "name labels now, in addition to any named by `subject`. Matched on the bank hash "
            "each manifest declares, so it catches an A/B arm, a fork and a rename, and two "
            "banks under one name never pool."
        ),
    )
    ranking: bool = Field(
        default=False,
        description=(
            "Also rank every SEARCHPOINT measured in the selection's CAMPAIGN subjects against "
            "its own campaign's origin, keyed on `sp_hash` so a prompt-only edit ranks like a "
            "config one. Rows pool within ONE campaign and never across two — they share no "
            "anchor to be compared through. OFF by default because it is the widest walk here: "
            "the roster half reads one document per subject, while this opens EVERY round of "
            "every campaign selected. Scored on the SELECTED metric, in its units."
        ),
    )
    winner_chain: bool = Field(
        default=False,
        description=(
            "Also serve the branch standing behind each course / candidate subject — the "
            "winner chain from its origin to its head, each point read on its own cells. "
            "OFF by default for the same reason as `ranking`: every point past the origin "
            "opens a round file. A campaign subject serves none, having no branch."
        ),
    )
    config: bool = Field(
        default=False,
        description=(
            "Also serve WHAT each searchpoint is — one flat `key -> value` map over its "
            "RESOLVED node config plus its prompt fields, which is what lines two of them "
            "up against each other. OFF by default because a prompt field is the largest "
            "thing this read puts on the wire and a four-channel comparison carries four."
        ),
    )
    metric: str = Field(
        default=MEASURAND,
        description=(
            "Which number to compare on: a key from the catalogue the response echoes "
            "back, or `expr:<formula>` composed over `metric.namespace`. Both are resolved "
            "against THIS selection: a metric no selected subject carries is not offered, "
            "and `measurand` is the seed's own lift where the cells carry one and the cell's "
            "own fitness where a cell is a sample."
        ),
    )
    grid: str = Field(
        default="",
        description=(
            "Cross two of the factors in `factors` and serve the POOLED value at each "
            "coordinate — `dataset,llm_only.model`. Answers 'which combination', where the "
            "factor marginals answer 'which level on average'. Every other factor is "
            "marginalised into the cells and named in `grid.marginalised`; to hold one fixed, "
            "narrow `subject` to its level instead, so the server pools over what is actually "
            "being looked at. Requested rather than served for all pairs because a roster "
            "varying on fifteen keys has 105 of them. Two axes at any number of factors."
        ),
    )

    def answer(self, caller: Caller) -> Evidence:
        stores = caller.stores
        return select_evidence(
            stores,
            subjects=[resolve_subject(stores, Subject.parse(raw)) for raw in self.subject],
            dataset=self.dataset,
            grid=self.grid,
            include_ranking=self.ranking,
            include_winner_chain=self.winner_chain,
            include_config=self.config,
            metric=self.metric,
        )


class HeadToHeadPriceParams(
    ReadParams, read=ReadKind.HEAD_TO_HEAD_PRICE, route="/head-to-head/price", method="POST"
):
    """What a head-to-head plan costs before any of it is minted, in dollars, calls and wall
    clock; each budget point carries the ``head-to-head`` payload that mints its arms.

    A read despite the POST: no LLM call is made and nothing is minted.
    """

    plan: HeadToHeadPlan

    async def answer(self, caller: Caller) -> HeadToHeadPrice:
        return await price_head_to_head(caller.stores, self.plan)


class DraftParams(ReadParams, read=ReadKind.DRAFT, route="/drafts/{draft_id}"):
    """Re-open a check-in: its draft and its last resolver turn. Not found once started or
    discarded."""

    draft_id: str = Field(min_length=1, max_length=128)

    async def answer(self, caller: Caller) -> DraftReopenResponse:
        return await reopen_draft(caller.stores, self.draft_id)


class CampaignPipelineParams(
    ReadParams, read=ReadKind.CAMPAIGN_PIPELINE, route="/campaigns/{campaign_id}/pipeline"
):
    """What this campaign runs, the one server-owned answer; ownership-gated, not found on a
    cross-tenant read."""

    campaign_id: str = Field(min_length=1, max_length=128)
    at: str = Field(
        default="",
        description="Searchpoint subject (`Subject.parse` grammar); defaults to the campaign root",
    )

    def answer(self, caller: Caller) -> CampaignPipelineResponse:
        return resolve_pipeline_at(caller.stores, self.campaign_id, self.at)


class ForkPreviewParams(
    ReadParams,
    read=ReadKind.FORK_PREVIEW,
    route="/campaigns/{campaign_id}/fork-preview",
    method="POST",
):
    """Whether this steer takes the babysit path: the fork gate's own verdict, asked without
    forking.

    A read despite the POST: nothing is written, and no capability is needed to ask.
    """

    campaign_id: str = Field(min_length=1, max_length=128)
    pipeline_overlay: dict[str, dict[str, Any]] = Field(
        description="The `nodes.*.config` overlay the fork would carry, as its `CycleSeed` sends it"
    )

    def answer(self, caller: Caller) -> ForkPreviewResponse:
        return fork_preview(caller.stores, self.campaign_id, self.pipeline_overlay)


class ConfigMapParams(
    ReadParams, read=ReadKind.CONFIG_MAP, route="/campaigns/{campaign_id}/config-map"
):
    """One campaign's knob coupling and provenance map: what moves which estimand, what
    overwrites what, which knobs collide.

    Read over the config its root runs under; not found on a cross-user read.
    """

    campaign_id: str = Field(min_length=1, max_length=128)

    def answer(self, caller: Caller) -> ConfigMapResponse:
        return campaign_config_map(caller.stores, self.campaign_id)


class CampaignStorageParams(
    ReadParams, read=ReadKind.CAMPAIGN_STORAGE, route="/campaigns/{campaign_id}/storage"
):
    """On-disk size of the campaign tree, split into its MECE leaves. Not found on a
    cross-user read."""

    campaign_id: str = Field(min_length=1, max_length=128)

    def answer(self, caller: Caller) -> LeafBytes:
        return campaign_storage(caller.stores, self.campaign_id)


class WorkspaceStorageParams(
    ReadParams, read=ReadKind.WORKSPACE_STORAGE, route="/workspace/storage"
):
    """On-disk size per campaign across the caller's workspace, fattest first, archived
    campaigns included.

    The shared caches and a residual ``other`` slice ride along, so the total is the tenant's real
    footprint.
    """

    def answer(self, caller: Caller) -> WorkspaceStorageResponse:
        return workspace_storage(caller.stores)


class DatasetsParams(ReadParams, read=ReadKind.DATASETS, route="/datasets"):
    """Every dataset this identity may read: its own tenant origins, then install content."""

    def answer(self, caller: Caller) -> DatasetIndexResponse:
        return dataset_index(caller.stores)


class DatasetPipelineParams(
    ReadParams, read=ReadKind.DATASET_PIPELINE, route="/datasets/{name:path}/pipeline"
):
    """One dataset's declared pipeline, gated by the resolver every dataset read shares."""

    name: DatasetName = Field(min_length=1, description="The dataset name")

    async def answer(self, caller: Caller) -> DatasetPipelineResponse:
        return await resolve_pipeline_for_dataset(caller.stores, self.name)


class DatasetCellsParams(
    ReadParams, read=ReadKind.DATASET_CELLS, route="/datasets/{name:path}/cells"
):
    """The measurement log; under a filter, ``samples`` and ``candidates`` shrink to the ones
    holding a kept cell."""

    name: DatasetName = Field(min_length=1, description="The dataset name")
    limit: int = Field(default=50, ge=1, le=1000, description="Samples per page.")
    max_unmeasured: int | None = Field(
        default=None,
        ge=0,
        le=1000,
        description="Cap on unmeasured samples kept in the ranking; None = no trim.",
    )
    scope: HeatmapScope = Field(
        default="dataset",
        description="dataset=cross-campaign; campaign=the cycle holding the campaign's line "
        "(needs campaign_id); cycle=one cycle (needs both ids).",
    )
    campaign_id: str | None = Field(
        default=None, max_length=128, description="Required when scope is campaign or cycle."
    )
    cycle_id: str | None = Field(
        default=None, max_length=128, description="Required when scope=cycle."
    )
    descend: str | None = Field(
        default=None,
        max_length=512,
        description=f"{_DESCEND} Present, every scope is read from the inner sandbox; needs "
        "campaign_id + cycle_id (the root hop) to walk from.",
    )
    order: HardSampleOrder | None = Field(
        default=None,
        description="Override the ranking key for this read; unset, the dataset's "
        "`CampaignConfig.hard_sample_order`. The resolved value comes back on `order`.",
    )
    candidate_id: str | None = Field(
        default=None,
        description="Keep only this individual's cells (`CellCandidate.candidate_id`). A "
        "campaign's candidates carry one; a dataset-scope column does not, so there it keeps "
        "nothing.",
    )
    round: int | None = Field(
        default=None,
        description="Keep only this round's cells. A dataset-scope column carries no round, so "
        "there it keeps nothing.",
    )
    status: SampleStatus | None = Field(default=None, description="Keep only cells with this mark.")
    at: int | None = Field(
        default=None,
        ge=0,
        description="Replay scope=cycle to this line of the cycle's own ledger — the dashboard "
        "route's `at`: the candidates named and the cells walked by then. Refused on any other "
        "scope, which no single ledger offset addresses.",
    )

    def scoped(self, stores: Stores) -> CellsScope:
        return cells_scope(
            stores,
            self.name,
            scope=self.scope,
            campaign_id=self.campaign_id,
            cycle_id=self.cycle_id,
            descend=decode_cycle_path(self.descend),
            at=self.at,
        )

    def logged(self, cells: CellsScope) -> CellsResponse:
        return measurement_log(
            cells,
            limit=self.limit,
            max_unmeasured=self.max_unmeasured,
            order=self.order,
            candidate_id=self.candidate_id,
            round=self.round,
            status=self.status,
        )

    def answer(self, caller: Caller) -> CellsResponse:
        return self.logged(self.scoped(caller.stores))


class DatasetCellParams(
    ReadParams, read=ReadKind.DATASET_CELL, route="/datasets/{name:path}/cells/{answer}"
):
    """One answer opened: its row assembled into a trace."""

    name: DatasetName = Field(min_length=1, description="The dataset name")
    # `answer` on the wire; `answer` is the read's own method.
    answer_id: str = Field(
        min_length=1, alias="answer", description="The answer's address in the log"
    )

    async def answer(self, caller: Caller) -> Cell:
        return await open_cell(caller.stores, self.name, self.answer_id)


class OriginsParams(ReadParams, read=ReadKind.ORIGINS, route="/origins"):
    """Every minted origin in the caller's tenant, newest first; a dataset no campaign ran is the
    ``datasets`` read's.

    Tenant-scoped, not owner-filtered.
    """

    def answer(self, caller: Caller) -> OriginListResponse:
        return origin_index(caller.stores)


# The last model is declared above, so a row answered by none fails right here.
if _unanswered := sorted(set(ReadKind) - PARAMS_FOR_READ.keys()):
    raise RuntimeError(f"read kinds answered by no params model: {_unanswered}")
