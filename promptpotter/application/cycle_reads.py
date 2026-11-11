from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.hierarchy import entries_version
from promptpotter.application.hierarchy_subtree import subtree_course
from promptpotter.application.jobs.machine_status import machine_status
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.scoring.closed_rounds import campaign_scorer, closed_round
from promptpotter.application.served_dashboard import WarmingDashboard, served_dashboard
from promptpotter.application.views.render.export import ExportFiles, Provenance, export_files
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.arm_reading import ArmReading
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, encode_cycle_path
from promptpotter.domain.dashboard_rows import ServedArm
from promptpotter.domain.export import ExportTier
from promptpotter.domain.hierarchy import HierarchySteps
from promptpotter.domain.results import Election, RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.audit_trail import round_audits
from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
from promptpotter.infrastructure.store.family_ray_queries import (
    RayCursor,
    build_family_ray,
    decode_ray_cursor,
    ray_validator_parts,
)
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.lineage_queries import (
    FamilyCourse,
    iter_family_courses,
)
from promptpotter.infrastructure.store.read_model import Moment, file_sig
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, NotFoundError

_TAIL_POLL_INTERVAL_S = 0.5
_FEED_TICK_S = 2.0


@dataclass(frozen=True)
class ViewedCycle:
    """``stores`` is the LEAF's own — a sandbox's at depth — so every read below is the plain one."""

    stores: Stores
    # The whole address from the caller's own tree, which `stores` — the leaf's — cannot give back.
    path: CyclePath
    dir: Path

    @property
    def hop(self) -> CycleHop:
        return self.path[-1]

    def moment(self, at: int | None) -> Moment | None:
        return None if at is None else Moment(CycleLayout(self.dir).ledger, at)


def _not_found(hop: CycleHop) -> NotFoundError:
    return NotFoundError(f"Cycle '{hop.campaign_id}/{hop.cycle_id}' not found")


def view_cycle(stores: Stores, path: CyclePath) -> ViewedCycle:
    """404 where no such cycle stands — never the warming answer of one still at check-in."""
    leaf_stores, leaf = resolve_cycle_path(stores, path)
    cycle_dir = cycle_dir_for(leaf_stores.base_dir, leaf)
    if not cycle_dir.is_dir():
        raise _not_found(leaf)
    return ViewedCycle(leaf_stores, path, cycle_dir)


def round_audit(cycle: ViewedCycle, round_num: int) -> RoundAudit | None:
    """``None`` where the ledger holds none: a round still open, or one that ran no optimizer call."""
    [audit] = round_audits(cycle.dir, [round_num])
    return audit


class RoundView(StrictModel):
    """One closed round as a surface reads it: its arms and its verdict. Its rows are the
    measurement log's (`GET /datasets/{name}/cells`); what the engine banks beside them — folds,
    diagnostics, optimizer state — stays on the ledger."""

    round: int
    election: Election | None
    candidates: list[ServedArm] = Field(description="Every arm the round read, in rank order.")
    headline: ArmReading | None = Field(
        description="The arm the round ENDED on (`election.ended_on`), read: the headline's "
        "numbers are its own. Null on a diag round, which elects nothing."
    )


def _closed(
    stores: Stores, hop: CycleHop, round_num: int, *, moment: Moment | None
) -> RoundResult | None:
    scorer = campaign_scorer(stores, hop.campaign_id)
    return None if scorer is None else closed_round(stores, hop, round_num, scorer, moment=moment)


def served_round(
    stores: Stores, hop: CycleHop, round_num: int, *, moment: Moment | None
) -> RoundView | None:
    closed = _closed(stores, hop, round_num, moment=moment)
    if closed is None:
        return None
    return RoundView(
        round=closed.round,
        election=closed.election,
        candidates=[
            ServedArm.of(row.reading, closed.arms[row.reading.arm.key]) for row in closed.scoreboard
        ],
        headline=closed.headline,
    )


def export_cycle(stores: Stores, path: CyclePath, tier: ExportTier) -> ExportFiles:
    """Every surface's export: the served tree, the served dashboard and ``export.json``, read once."""
    # A banked round names its optimizer's state kinds, which register as their packages import.
    optimizers.NODE_MEMBERS.complete()
    cycle = view_cycle(stores, path)
    hop = cycle.hop
    campaign = cycle.stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise _not_found(hop)
    dash = served_dashboard(cycle.stores, hop)
    # A cycle that declared no wiring yet has no arm, so no count needs its unit.
    unit: MeasuredUnit = "sample"
    offset = None
    if not isinstance(dash, WarmingDashboard):
        unit, offset = dash.measured_unit, dash.at_offset
    course = subtree_course(stores, path)
    standing = course.facts.standing
    selection = None if standing is None else standing.selection
    closed = None if selection is None else _closed(cycle.stores, hop, selection.round, moment=None)
    arm = None if closed is None or selection is None else closed.arms.get(selection.key)
    models = (
        {}
        if arm is None
        else {
            node: config.model
            for node, config in arm.individual.pipeline_params.nodes.items()
            if config.model is not None
        }
    )
    no_export = "this cycle has none: it is written when the cycle ends with a round closed"
    export = cycle.stores.campaigns.read_export(hop)
    return export_files(
        tier,
        course,
        Provenance(
            tool_version=APP_VERSION,
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            dataset_name=campaign.dataset_name,
            dataset_hash=campaign.declaration.dataset_hash,
            treatment=campaign.treatment,
            models=models,
            ledger_offset=offset,
        ),
        unit=unit,
        export=export,
        no_export=no_export,
    )


async def cycle_event_frames(cycle: ViewedCycle) -> AsyncIterator[str]:
    tail = CycleLedgerTail(cycle.dir, cycle.hop.cycle_id)
    snapshot = await asyncio.to_thread(
        lambda: tail.snapshot_frame(
            served_dashboard(cycle.stores, cycle.hop).model_dump(mode="json")
        )
    )
    yield snapshot.model_dump_json()
    while True:
        for envelope in await asyncio.to_thread(tail.read_new):
            yield envelope.model_dump_json()
        await asyncio.sleep(_TAIL_POLL_INTERVAL_S)


# A read names its subject, by prefix, and answers the versions it stood on: taken before its body,
# so a frame re-asks only the reads that stood on another (`webapp/lib/read-cache.ts`).
FEED_SUBJECT_HEADER = "X-Feed-Subject"
FEED_VERSIONS_HEADER = "X-Feed-Versions"
_STORE = "store:"


class FeedFrame(StrictModel):
    """One subject moved: re-ask every read that names it. A hint, never the body."""

    subject: str = Field(description="`account`, `machine`, or `store:` and a store's hops.")
    version: str


def _store_subjects(stores: Stores) -> Iterator[tuple[str, Stores]]:
    yield f"{_STORE}{encode_cycle_path(stores.inside)}", stores
    for campaign_id in stores.campaigns.list_campaign_ids():
        for cycle_dir in stores.campaigns.campaign_cycle_dirs(campaign_id):
            if (inner := inner_sandbox_store(stores, campaign_id, cycle_dir.name)) is not None:
                yield from _store_subjects(inner)


def feed_versions(stores: Stores, jobs: JobRegistry, named: str = "") -> dict[str, str]:
    """Each subject *named* covers, by prefix (every one by default), at its version: stats and
    the slots, never a fold."""
    held: dict[str, tuple[object, ...]] = {}
    if named.startswith(_STORE) or _STORE.startswith(named):
        held |= {
            subject: entries_version([store])
            for subject, store in _store_subjects(stores)
            if subject.startswith(named)
        }
    if "account".startswith(named):
        # What the account filed: an edit, a setting, a draft turn. A run's appends do not move it.
        held["account"] = (file_sig(stores.commands.path), stores.drafts.version())
    if "machine".startswith(named):
        held["machine"] = (machine_status(jobs, identity=stores.identity).model_dump_json(),)
    return {
        subject: hashlib.sha256(repr(version).encode()).hexdigest()[:16]
        for subject, version in held.items()
    }


async def feed_frames(stores: Stores, jobs: JobRegistry) -> AsyncIterator[str]:
    """Every subject once, then each move: the server writes every ledger, so it says what moved."""
    sent: dict[str, str] = {}
    while True:
        for subject, version in (await asyncio.to_thread(feed_versions, stores, jobs)).items():
            if sent.get(subject) != version:
                sent[subject] = version
                yield FeedFrame(subject=subject, version=version).model_dump_json()
        await asyncio.sleep(_FEED_TICK_S)


@dataclass(frozen=True)
class FamilyRay:
    """Opened, not yet read: a matching ``validator`` lets the caller skip the ledger reads."""

    courses: list[FamilyCourse]
    limit: int
    before: str | None
    cursor: RayCursor | None

    @property
    def validator(self) -> tuple[object, ...]:
        return ray_validator_parts(self.courses, limit=self.limit, before=self.before)

    def window(self) -> HierarchySteps:
        return build_family_ray(self.courses, limit=self.limit, before=self.cursor)


def open_family_ray(
    stores: Stores, path: CyclePath, *, limit: int, before: str | None
) -> FamilyRay:
    """``before`` is a prior window's ``cursor_prev``; a mangled one is refused, never read as the head."""
    try:
        cursor = decode_ray_cursor(before)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    courses = iter_family_courses(stores, path)
    leaf = courses[0].path[-1]
    if not cycle_dir_for(courses[0].store.base_dir, leaf).is_dir():
        raise _not_found(leaf)
    return FamilyRay(courses, limit, before, cursor)
