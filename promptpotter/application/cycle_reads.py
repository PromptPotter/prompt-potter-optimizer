from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from promptpotter.application import optimizers
from promptpotter.application.scoring.closed_rounds import campaign_scorer, closed_round
from promptpotter.application.served_dashboard import WarmingDashboard, served_dashboard
from promptpotter.application.views.render.export import ExportFiles, Provenance, export_files
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.export import ExportTier, ExportVersionError, PromptExport
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.ray import RayResponse
from promptpotter.domain.results import (
    ArmReading,
    ArmState,
    Election,
    ScoreboardRow,
)
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
from promptpotter.infrastructure.runtime_flags import run_phase_validator_epoch
from promptpotter.infrastructure.store.dataset_access import definition_version
from promptpotter.infrastructure.store.family_ray_queries import (
    RayCursor,
    build_family_ray,
    decode_ray_cursor,
    ray_validator_parts,
)
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.lineage_queries import (
    FamilyCourse,
    build_lineage_tree,
    iter_family_courses,
)
from promptpotter.infrastructure.store.read_model import Moment, file_sig
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, ConflictError, NotFoundError

_TAIL_POLL_INTERVAL_S = 0.5


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

    @property
    def dashboard_changed_at(self) -> float | None:
        """Covers the DERIVED phase: an mtime alone would 304 a dead producer at ``running``."""
        return run_phase_validator_epoch(self.dir)

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


def tree_version(stores: Stores, path: CyclePath, *, masked: bool) -> tuple[object, ...]:
    """Stats only: every store the family spans and, under a mask, what the mask grades from."""
    family = {c.store.base_dir: c.store for c in iter_family_courses(stores, path)}.values()
    if not masked:
        return tuple(held.campaigns.version() for held in family)
    names = {
        c.dataset_name for held in family for c in held.campaigns.list_campaigns(lifecycle="all")
    }
    archives = {held.archive.base_dir: held.archive for held in family}.values()
    return (
        *(held.campaigns.version() for held in family),
        *(archive.version() for archive in archives),
        *(file_sig(held.ruler.path) for held in family),
        *(definition_version(stores, name) for name in sorted(names)),
    )


def round_audit(cycle: ViewedCycle, round_num: int) -> RoundAudit | None:
    """``None`` where none is on disk: a round still open, or one that ran no optimizer call."""
    [audit] = load_round_audits(cycle.dir, [round_num])
    return audit


class RoundView(StrictModel):
    """One closed round as a surface reads it: its arms and its verdict. Its rows are the
    measurement log's (`GET /datasets/{name}/cells`); what the engine banks beside them — folds,
    diagnostics, optimizer state — stays on the ledger."""

    round: int
    arms: dict[ArmKey, ArmState]
    election: Election | None
    scoreboard: list[ScoreboardRow]
    headline: ArmReading | None = Field(
        description="The arm the round ENDED on (`election.ended_on`), read: the headline's "
        "numbers are its own. Null on a diag round, which elects nothing."
    )
    fitness_bands: list[int] = Field(
        description="How many scored rows of the `headline` arm's cells graded into each tenth of "
        "[0, 1], lowest first: a band is closed above, so a 0.5 counts in the fifth and a 1.0 in "
        "the tenth. A row carrying no verdict is in none — an uncharged error's 0.0 is a floor, "
        "not a low score."
    )


def served_round(
    stores: Stores, hop: CycleHop, round_num: int, *, moment: Moment | None
) -> RoundView | None:
    graded_under = campaign_scorer(stores, hop.campaign_id)
    closed = (
        None
        if graded_under is None
        else closed_round(stores, hop, round_num, graded_under, moment=moment)
    )
    if closed is None:
        return None
    return RoundView(
        round=closed.round,
        arms=closed.arms,
        election=closed.election,
        scoreboard=closed.scoreboard,
        headline=closed.headline,
        fitness_bands=closed.fitness_bands,
    )


def export_cycle(stores: Stores, path: CyclePath, tier: ExportTier) -> ExportFiles:
    """Every surface's export: the served tree, the served dashboard and ``export.json``, read once."""
    # A banked round names its optimizer's state kinds, which register as their packages import.
    optimizers.registered()
    cycle = view_cycle(stores, path)
    hop = cycle.hop
    campaign = cycle.stores.campaigns.load_campaign(hop.campaign_id)
    if campaign is None:
        raise _not_found(hop)
    dash = served_dashboard(cycle.stores, hop)
    # A cycle that declared no wiring yet has no arm, so no count needs its unit.
    unit: MeasuredUnit = "sample"
    standing, offset = None, None
    if not isinstance(dash, WarmingDashboard):
        unit, standing, offset = dash.measured_unit, dash.run_standing, dash.at_offset
    selection = None if standing is None else standing.selection
    round_view = (
        None if selection is None else served_round(cycle.stores, hop, selection.round, moment=None)
    )
    arm = None if round_view is None or selection is None else round_view.arms.get(selection.key)
    course = build_lineage_tree(stores, path, moment=None)
    models = (
        {}
        if arm is None
        else {
            node: str(config["model"])
            for node, config in node_config_items(arm.individual.pipeline_params)
            if "model" in config
        }
    )
    export: PromptExport | None = None
    no_export = "this cycle has none: it is written when the cycle ends with a round closed"
    try:
        export = cycle.stores.campaigns.read_export(hop)
    except ValueError as exc:
        # Old exports are disposable, so one that does not read refuses every tier, in one sentence.
        why = str(exc) if isinstance(exc, ExportVersionError) else "its export.json does not read"
        raise ConflictError(f"Cycle {hop.cycle_id}: {why}; re-run the cycle to export it.") from exc
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

    def window(self) -> RayResponse:
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
