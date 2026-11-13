"""The race's catch-up pool: every comparison its stop rule makes is on identical cells, one
measurement per (prior, cell), walked by `query_loop.py::run_walks` beside the arms."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.search_point_scorer import close_walk, open_walk
from promptpotter.domain.scoring import CellSheet
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.optimizers.nodes import Race
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.domain.search_point import JobSearchPoint


@dataclass(frozen=True)
class CaughtUp:
    """What one settle took: the racing member, and each prior's catch-up cells."""

    member: str
    cells: Mapping[ArmKey, Sequence[GradedCell]]


# A started catch-up: its call, the commit writing its row once a walk takes the cell, the discard.
type CatchUp = tuple[asyncio.Future[Any], Callable[[], Sequence[GradedCell]], Callable[[], None]]


class CatchUpPool:
    """A prior's configuration on the cell just taken: awaited before judged, then committed."""

    def __init__(
        self,
        race: Race,
        session: Session,
        sample_index: SampleIndex | None,
        member: str,
        sps: Mapping[ArmKey, JobSearchPoint],
    ) -> None:
        self._race, self._session, self._index = race, session, sample_index
        self._member, self._sps = member, sps
        self._pending: dict[tuple[ArmKey, int], CatchUp] = {}
        # Each prior's scored cells, kept as the race admits them, so a turn costs priors, not rows.
        self._held: dict[ArmKey, set[str]] = {}
        # Each prior's committed catch-up cells, every one the race admitted.
        self.caught: dict[ArmKey, list[GradedCell]] = {}

    def _scored(self, key: ArmKey) -> set[str]:
        if key not in self._held:
            self._held[key] = {row.key for row in self._race.priors[key].scoreable}
        return self._held[key]

    def _unstarted(self, sample: Sample) -> list[ArmKey]:
        # A prior holds a cell it SCORED: an errored one is caught up again.
        return [
            key
            for key in self._race.priors
            if sample.key not in self._scored(key) and (key, sample.id) not in self._pending
        ]

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        """Nothing is written or graded until :meth:`commit_backfills` takes them."""
        started: list[asyncio.Future[Any]] = []
        for cid in self._unstarted(sample)[: max(room, 0)]:
            backfill = _catch_up(self._session, self._index, self._sps[cid], sample, cid)
            self._pending[(cid, sample.id)] = backfill
            started.append(backfill[0])
        return started

    def owed_backfills(self, sample: Sample) -> int:
        return len(self._unstarted(sample))

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return [call for call, *_ in self._pending.values() if not call.done()]

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]:
        return [call for (_, sid), (call, *_) in self._pending.items() if sid == sample.id]

    def commit_backfills(self, sample: Sample) -> CaughtUp | None:
        """In prior order, when a serial round would have measured them, so what reaches disk is the serial round's."""
        taken: dict[ArmKey, Sequence[GradedCell]] = {}
        for cid in list(self._race.priors):
            backfill = self._pending.pop((cid, sample.id), None)
            if backfill is not None:
                cells = taken[cid] = backfill[1]()
                self._race.catch_up(cid, cells)
                self._scored(cid).update(c.key for c in CellSheet("", tuple(cells)).scoreable)
                self.caught.setdefault(cid, []).extend(cells)
        return CaughtUp(self._member, taken) if taken else None

    def bank_backfills(self, samples: Sequence[Sample]) -> CaughtUp | None:
        """Written UNGRADED and never admitted: cells a stopped round's walks were sure to take, so the resume replays them unpaid."""
        wanted = {s.id for s in samples}
        taken: dict[ArmKey, list[GradedCell]] = {}
        for key, (call, commit, _) in list(self._pending.items()):
            landed = call.done() and not call.cancelled() and call.exception() is None
            if key[1] in wanted and landed:
                del self._pending[key]
                taken.setdefault(key[0], []).extend(commit())
        return CaughtUp(self._member, taken) if taken else None

    def discard_backfills(self) -> None:
        """Paid and never written: a serial round would never have made these."""
        for _call, _commit, discard in self._pending.values():
            discard()
        self._pending.clear()


def _catch_up(
    session: Session,
    sample_index: SampleIndex | None,
    sp: JobSearchPoint,
    sample: Sample,
    prior: ArmKey,
) -> CatchUp:
    # No stop rule: it recurses into the eliminator.
    walk = open_walk(
        sp,
        [sample],
        session,
        kind=MeasurementRole.BACKFILL,
        sample_index=sample_index,
        # The PRIOR being caught up, never the arm whose cell triggered it.
        measured=prior,
    )
    walk.release()
    _, cell = walk.launch(1)

    def commit() -> Sequence[GradedCell]:
        walk.collect()
        walk.end(walk.take(cell) or walk.judge(), cancel=True)
        return close_walk(walk).sheet.cells

    def discard() -> None:
        # Collected first, so a cell already back releases its claim now rather than on a callback.
        walk.collect()
        walk.end(None, cancel=True)

    return cell, commit, discard
