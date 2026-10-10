from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from promptpotter.application.scoring.closed_rounds import campaign_scorer, closed_round
from promptpotter.application.served_dashboard import served_dashboard
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.scoring import CellSheet, ground_truth_text
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
from promptpotter.infrastructure.runtime_flags import run_phase_validator_epoch
from promptpotter.infrastructure.store.family_ray_queries import (
    DEFAULT_RAY_LIMIT,
    MAX_RAY_LIMIT,
    RayCursor,
    RayResponse,
    build_family_ray,
    decode_ray_cursor,
    ray_validator_parts,
)
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    course_validator_ns,
    cycle_dir_for,
)
from promptpotter.infrastructure.store.lineage_queries import FamilyCourse, iter_family_courses
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError, NotFoundError

__all__ = [
    "DEFAULT_RAY_LIMIT",
    "MAX_RAY_LIMIT",
    "FamilyRay",
    "RayResponse",
    "ViewedCycle",
    "cycle_event_frames",
    "open_family_ray",
    "round_audit",
    "served_round",
    "view_cycle",
]

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

    @property
    def tree_changed_ns(self) -> int | None:
        return course_validator_ns(self.dir)

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
    """``None`` where none is on disk: a round still open, or one that ran no optimizer call."""
    [audit] = load_round_audits(cycle.dir, [round_num])
    return audit


@dataclass(frozen=True)
class _MarkedSheet(CellSheet):
    def wire(self) -> list[dict[str, object]]:
        return [
            {
                **cell.wire(),
                "status": sample_status(cell.facts, cell.grade),
                "ground_truth_text": ground_truth_text(cell.facts.ground_truth),
            }
            for cell in self.cells
        ]


def served_round(
    stores: Stores, hop: CycleHop, round_num: int, moment: Moment | None = None
) -> RoundResult | None:
    """A served projection: the round a resume, a fork and the round file read carries no ``status``."""
    graded_under = campaign_scorer(stores, hop.campaign_id)
    closed = (
        None
        if graded_under is None
        else closed_round(stores, hop, round_num, graded_under, moment=moment)
    )
    if closed is None:
        return None

    def marked(sheet: CellSheet) -> CellSheet:
        return _MarkedSheet(sheet.scorer_id, sheet.cells)

    def each(arms: dict[str, CellSheet]) -> dict[str, CellSheet]:
        return {k: marked(v) for k, v in arms.items()}

    return closed.model_copy(
        update={
            "results": marked(closed.results),
            "all_candidate_results": each(closed.all_candidate_results),
            "reference_results": each(closed.reference_results),
            "overlap_results": each(closed.overlap_results),
        }
    )


async def cycle_event_frames(cycle: ViewedCycle) -> AsyncIterator[str]:
    tail = CycleLedgerTail(cycle.dir, cycle.path)
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
