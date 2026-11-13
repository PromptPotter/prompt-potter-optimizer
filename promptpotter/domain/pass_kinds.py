from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from promptpotter.domain.phases import CampaignPhase
from promptpotter.shared.measurement_context import MeasurementRole, RoleScope

if TYPE_CHECKING:
    from promptpotter.domain.spend import TokenUsageKind


@dataclass(frozen=True)
class Pass:
    """What a pass of one kind walks and files, declared once; every scorer reads its row."""

    kind: MeasurementRole
    # The readings that see its cells; two scopes never pair.
    scopes: frozenset[RoleScope]
    # Its cells are an ARM of their round; any other kind's are a pass beside it.
    arm: bool
    # Its cells reach the ledger one by one, so a live surface draws and ticks them.
    recorded: bool
    # Asked of the loop's own measurement, which an answer's provenance grade credits.
    deliberate: bool
    # The spend kind its calls bill as, whatever each would otherwise bank as.
    spend: TokenUsageKind | None
    # Re-measured whatever the archive holds: a replay would fabricate a zero spread.
    fresh: bool
    # A run stop ends it short on the cells it took and comes back with it, never unwinding it.
    keeps: bool
    # The ledger bracket a held-out pass is entered, graded and exited under.
    phase: CampaignPhase | None
    # The look-ahead it sends at: the operator's request, which a round spends, or the backend's ceiling.
    depth: Literal["requested", "ceiling"]
    # It ends early on the campaign's degradation checks.
    checks: bool

    @property
    def bracket(self) -> CampaignPhase:
        if self.phase is None:
            raise ValueError(f"a {self.kind} pass is no held-out pass")
        return self.phase


_DECISION = frozenset({RoleScope.DECISION, RoleScope.REPORT})
_REPORT = frozenset({RoleScope.REPORT})


def _pass(
    kind: MeasurementRole,
    scopes: frozenset[RoleScope],
    *,
    arm: bool = False,
    recorded: bool = False,
    deliberate: bool = True,
    spend: TokenUsageKind | None = None,
    fresh: bool = False,
    keeps: bool = False,
    phase: CampaignPhase | None = None,
    depth: Literal["requested", "ceiling"] = "requested",
    checks: bool = False,
) -> Pass:
    return Pass(kind, scopes, arm, recorded, deliberate, spend, fresh, keeps, phase, depth, checks)


PASSES: dict[MeasurementRole, Pass] = {
    row.kind: row
    for row in (
        _pass(MeasurementRole.ORIGIN, _DECISION, arm=True, recorded=True),
        _pass(MeasurementRole.PANEL, _DECISION, arm=True, recorded=True, checks=True),
        _pass(MeasurementRole.BACKFILL, _DECISION),
        _pass(MeasurementRole.PARENT, _DECISION, arm=True, recorded=True),
        _pass(MeasurementRole.REPAIR, _DECISION),
        _pass(MeasurementRole.OVERLAP, _REPORT),
        _pass(
            MeasurementRole.VERIFY,
            _REPORT,
            recorded=True,
            deliberate=False,
            spend="diagnostic",
            keeps=True,
            phase=CampaignPhase.VERIFY,
            depth="ceiling",
            checks=True,
        ),
        _pass(
            MeasurementRole.BENCH,
            frozenset({RoleScope.BENCH}),
            recorded=True,
            spend="bench",
            keeps=True,
            phase=CampaignPhase.BENCH,
            depth="ceiling",
            checks=True,
        ),
        # It re-reads rows already standing, so it joins no reading.
        _pass(
            MeasurementRole.NOISE_FLOOR,
            frozenset(),
            deliberate=False,
            spend="diagnostic",
            fresh=True,
            phase=CampaignPhase.VERIFY,
            depth="ceiling",
            checks=True,
        ),
    )
}
assert set(PASSES) == set(MeasurementRole), "a pass kind no row declares"

SCOPE_ROLES: dict[RoleScope, frozenset[MeasurementRole]] = {
    scope: frozenset(kind for kind, row in PASSES.items() if scope in row.scopes)
    for scope in RoleScope
}


class IndividualWalk[C: tuple[Any, ...]](NamedTuple):
    """A cell is any tuple LEADING with its sample's content key."""

    individual_id: str
    roles: frozenset[MeasurementRole]
    cells: Sequence[C]


def individual_cells[C: tuple[Any, ...]](
    walks: Iterable[IndividualWalk[C]], individual_id: str, scope: RoleScope
) -> list[C]:
    """Nothing is folded: the reader grades these and takes ``CellSheet.standing``."""
    visible = SCOPE_ROLES[scope]
    return [
        cell
        for walk in walks
        if walk.individual_id == individual_id and walk.roles <= visible
        for cell in walk.cells
    ]
