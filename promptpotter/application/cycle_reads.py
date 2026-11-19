from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field
from pydantic_core import to_json

from promptpotter.application.caller import Caller
from promptpotter.application.hierarchy import entries_version
from promptpotter.application.hierarchy_subtree import subtree_cycle
from promptpotter.application.jobs.machine_status import served_machine_status
from promptpotter.application.jobs.registry import JobRegistry
from promptpotter.application.scoring.closed_rounds import campaign_scorer, closed_round
from promptpotter.application.served_dashboard import WarmingDashboard, served_dashboard
from promptpotter.application.views.render.export import ExportFiles, ExportProvenance, export_files
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.arm_reading import ArmReading
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import (
    CycleAt,
    CycleDir,
    CycleHop,
    CyclePath,
    encode_cycle_path,
)
from promptpotter.domain.dashboard_rows import ServedArm
from promptpotter.domain.export import ExportTier
from promptpotter.domain.hierarchy import HierarchySteps
from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.domain.results import Election, RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.audit_trail import round_audits
from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
from promptpotter.infrastructure.projections.live_dashboard import fold_at
from promptpotter.infrastructure.store.family_ray_queries import (
    RayCursor,
    build_family_ray,
    decode_ray_cursor,
    ray_validator_parts,
)
from promptpotter.infrastructure.store.io import make_dirs
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.lineage_queries import (
    FamilyCycle,
    iter_family_cycles,
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
    """One round as a surface reads it, filed or not yet (`unfiled`): its arms and its verdict.
    Its rows are the measurement log's (`GET /datasets/{name}/cells`); what the engine banks
    beside them — folds, diagnostics, optimizer state — stays on the ledger."""

    round: int
    election: Election | None
    candidates: list[ServedArm] = Field(
        description="The round's own arms, never the parent it re-read: a filed round's in rank "
        "order, an unfiled one's as announced."
    )
    headline: ArmReading | None = Field(
        description="The arm the round ENDED on (`election.ended_on`), read: the headline's "
        "numbers are its own."
    )
    unfiled: bool = Field(
        description="No file holds it yet: its arms stream in, with no election or headline."
    )


def _closed(
    stores: Stores, at: CampaignCycle, round_num: int, *, moment: Moment | None
) -> RoundResult | None:
    scorer = campaign_scorer(stores, at.campaign)
    return closed_round(stores, at.hop, round_num, scorer, moment=moment)


def served_round(
    stores: Stores, hop: CycleHop, round_num: int, *, moment: Moment | None
) -> RoundView | None:
    # One series: a round no file holds yet (the dashboard's `unfiled`) is the fold's, merged here.
    at = None if moment is None else moment.offset
    state = fold_at(CycleAt(CycleDir(cycle_dir_for(stores.base_dir, hop)), hop, at))
    if state is not None and state.unfiled() == round_num:
        return RoundView(
            round=round_num,
            election=None,
            candidates=[*state.current_round.candidates],
            headline=None,
            unfiled=True,
        )
    campaign = stores.campaigns.campaign(hop.campaign_id)
    closed = _closed(stores, CampaignCycle(campaign, hop.cycle_id), round_num, moment=moment)
    if closed is None:
        return None
    return RoundView(
        round=closed.round,
        election=closed.election,
        candidates=[
            ServedArm.of(row.reading, closed.arms[row.reading.arm.key])
            for row in closed.ranked()
            if row.reading.role.is_proposal
        ],
        headline=closed.headline,
        unfiled=False,
    )


def export_cycle(stores: Stores, path: CyclePath, tier: ExportTier) -> ExportFiles:
    """Every surface's export: the served tree, the served dashboard and ``export.json``, read once."""
    cycle = view_cycle(stores, path)
    hop = cycle.hop
    campaign = cycle.stores.campaigns.campaign(hop.campaign_id)
    dash = served_dashboard(cycle.stores, hop)
    # A cycle that declared no wiring yet has no arm, so no count needs its unit.
    unit: MeasuredUnit = "sample"
    offset = None
    if not isinstance(dash, WarmingDashboard):
        unit, offset = dash.measured_unit, dash.at_offset
    entry = subtree_cycle(stores, path)
    standing = entry.facts.standing
    selection = None if standing is None else standing.selection
    closed = (
        None
        if selection is None
        else _closed(
            cycle.stores, CampaignCycle(campaign, hop.cycle_id), selection.round, moment=None
        )
    )
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
        entry,
        ExportProvenance(
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


def write_export(files: ExportFiles, root: Path) -> None:
    """The CLI's `--out`: a file a person names is no run data, so it follows their umask, not 0600."""
    for rel, text in files.files.items():
        make_dirs((root / rel).parent)
        (root / rel).write_bytes(text.encode("utf-8"))


async def _signed_ticks(caller: Caller, every_s: float) -> AsyncIterator[None]:
    """A stream's ticks while its caller stands: each re-reads the carrier it opened on, so a
    revoke, a logout or an expiry ends the stream within one tick."""
    while await asyncio.to_thread(caller.still_signed):
        yield
        await asyncio.sleep(every_s)


async def cycle_event_frames(
    caller: Caller, cycle: ViewedCycle
) -> AsyncIterator[ProjectionEnvelope]:
    tail = CycleLedgerTail(cycle.dir, cycle.hop.cycle_id)
    yield await asyncio.to_thread(
        lambda: tail.snapshot_frame(
            served_dashboard(cycle.stores, cycle.hop).model_dump(mode="json")
        )
    )
    async for _ in _signed_ticks(caller, _TAIL_POLL_INTERVAL_S):
        for envelope in await asyncio.to_thread(tail.read_new):
            yield envelope


class FrameStamp(StrictModel):
    """What every frame of a stream carries beside its body: an `EventSource` reads no header."""

    contract: str = Field(
        description="The served contract (`X-Served-Contract`), compared as a fetch's answer is."
    )


class StampedEnvelope(ProjectionEnvelope, FrameStamp):
    """A cycle tail's frame as the stream serves it."""


async def stamped_frames(frames: AsyncIterator[StrictModel], contract: str) -> AsyncIterator[str]:
    """Each frame as a stream serves it: its body and the contract its door serves, written as every
    fetch's answer is (a NaN is `null`: a browser's `JSON.parse` refuses `NaN`)."""
    stamp = FrameStamp(contract=contract).model_dump()
    async for frame in frames:
        yield to_json(frame.model_dump(mode="json") | stamp, inf_nan_mode="null").decode()


# A read names its subject, by prefix, and answers the versions it stood on: taken before its body,
# so a frame re-asks only the reads that stood on another (`webapp/lib/read-cache.ts`).
FEED_SUBJECT_HEADER = "X-Feed-Subject"
FEED_VERSIONS_HEADER = "X-Feed-Versions"
_STORE = "store:"


class FeedFrame(StrictModel):
    """One subject moved: re-ask every read that names it. A hint, never the body."""

    subject: str = Field(description="`account`, `machine`, or `store:` and a store's hops.")
    version: str


class StampedFeedFrame(FeedFrame, FrameStamp):
    """A feed frame as the stream serves it."""


def _store_subjects(stores: Stores) -> Iterator[tuple[str, Stores]]:
    yield f"{_STORE}{encode_cycle_path(stores.inside)}", stores
    for campaign_id in stores.campaigns.list_campaign_ids():
        for hop in stores.campaigns.campaign_cycle_hops(campaign_id):
            if (inner := inner_sandbox_store(stores, hop)) is not None:
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
        held["machine"] = (served_machine_status(stores, jobs).model_dump_json(),)
    return {
        subject: hashlib.sha256(repr(version).encode()).hexdigest()[:16]
        for subject, version in held.items()
    }


async def feed_frames(caller: Caller) -> AsyncIterator[FeedFrame]:
    """Every subject once, then each move: the server writes every ledger, so it says what moved."""
    sent: dict[str, str] = {}
    async for _ in _signed_ticks(caller, _FEED_TICK_S):
        moved = await asyncio.to_thread(feed_versions, caller.stores, caller.jobs)
        for subject, version in moved.items():
            if sent.get(subject) != version:
                sent[subject] = version
                yield FeedFrame(subject=subject, version=version)


@dataclass(frozen=True)
class FamilyRay:
    """Opened, not yet read: a matching ``validator`` lets the caller skip the ledger reads."""

    cycles: list[FamilyCycle]
    limit: int
    before: str | None
    cursor: RayCursor | None

    @property
    def validator(self) -> tuple[object, ...]:
        return ray_validator_parts(self.cycles, limit=self.limit, before=self.before)

    def window(self) -> HierarchySteps:
        return build_family_ray(self.cycles, limit=self.limit, before=self.cursor)


def open_family_ray(
    stores: Stores, path: CyclePath, *, limit: int, before: str | None
) -> FamilyRay:
    """``before`` is a prior window's ``cursor_prev``; a mangled one is refused, never read as the head."""
    try:
        cursor = decode_ray_cursor(before)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    cycles = iter_family_cycles(stores, path)
    leaf = cycles[0].path[-1]
    if not cycle_dir_for(cycles[0].store.base_dir, leaf).is_dir():
        raise _not_found(leaf)
    return FamilyRay(cycles, limit, before, cursor)
