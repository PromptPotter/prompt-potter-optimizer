from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from promptpotter.domain.arm_reading import ArmElection, ArmReading, panel_counts, panel_cuts
from promptpotter.domain.arms import ArmState, arm_name
from promptpotter.domain.campaign import Campaign
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop, CyclePath
from promptpotter.domain.origin_panel import line_by_individual, overlap_line
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.results import Election
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_bench_readings,
    scan_ledger_verify,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment
from promptpotter.infrastructure.store.stores import Stores, inner_sandbox_store, resolve_cycle_path
from promptpotter.shared.measurement_context import MeasurementRole

# A cost bound, never a caller's dial: sandboxes nest re-entrantly, so an unbounded walk is unbounded on disk.
MAX_CYCLE_DEPTH = 3


class FamilyCycle(NamedTuple):
    store: Stores
    path: CyclePath
    # Not `index`: a NamedTuple field by that name shadows `tuple.index`.
    manifest: CycleIndex | None
    inner: bool
    depth: int = 0

    @property
    def created_at(self) -> str:
        return "" if self.manifest is None else self.manifest.created_at


class _Reads:
    """Per-walk and thrown away: a memo outliving the request serves a round that has closed."""

    def __init__(self) -> None:
        self._cycles: dict[Path, list[tuple[CycleHop, CycleIndex]]] = {}
        self._campaigns: dict[tuple[Path, str], Campaign | None] = {}
        # Keyed on the full identity: a cycle_id is a content hash, so campaigns and inner cells repeat it.
        self.seen: set[tuple[Path, str, str]] = set()

    def cycles(self, stores: Stores) -> list[tuple[CycleHop, CycleIndex]]:
        if (key := stores.base_dir) not in self._cycles:
            self._cycles[key] = stores.campaigns.enumerate_cycles()
        return self._cycles[key]

    def campaign(self, stores: Stores, campaign_id: str) -> Campaign | None:
        if (key := (stores.base_dir, campaign_id)) not in self._campaigns:
            self._campaigns[key] = stores.campaigns.load_campaign(campaign_id)
        return self._campaigns[key]


def _child_cycles(stores: Stores, path: CyclePath, reads: _Reads) -> list[FamilyCycle]:
    leaf = path[-1]
    reads.seen.add((stores.base_dir, leaf.campaign_id, leaf.cycle_id))
    out: list[FamilyCycle] = []
    for hop, index in reads.cycles(stores):
        if index.parent_cycle_id != leaf.cycle_id or hop.campaign_id != leaf.campaign_id:
            continue
        if (key := (stores.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
            continue
        reads.seen.add(key)
        out.append(
            FamilyCycle(
                store=stores,
                path=(*path[:-1], hop),
                manifest=index,
                inner=False,
            )
        )

    sandbox = inner_sandbox_store(stores, leaf.campaign_id, leaf.cycle_id)
    if sandbox is not None:
        for hop, index in reads.cycles(sandbox):
            # A cycle whose campaign does not load has no entry, so it is no cycle of the tree.
            if index.parent_cycle_id or reads.campaign(sandbox, hop.campaign_id) is None:
                continue
            if (key := (sandbox.base_dir, hop.campaign_id, hop.cycle_id)) in reads.seen:
                continue
            reads.seen.add(key)
            out.append(
                FamilyCycle(
                    store=sandbox,
                    path=(*path, hop),
                    manifest=index,
                    inner=True,
                )
            )
    return out


def iter_family_cycles(stores: Stores, path: CyclePath) -> list[FamilyCycle]:
    reads = _Reads()
    root_store, _ = resolve_cycle_path(stores, path)
    root = FamilyCycle(
        store=root_store,
        path=path,
        manifest=root_store.campaigns.load(path[-1], moment=None),
        inner=False,
    )
    out = [root]
    frontier = [root]
    while frontier:
        level: list[FamilyCycle] = []
        for cycle in frontier:
            for child in _child_cycles(cycle.store, cycle.path, reads):
                depth = len(child.path) - len(path)
                if depth > MAX_CYCLE_DEPTH:
                    continue
                level.append(child._replace(depth=depth))
        # Stable order: the time-ray's ETag must hold across identical requests.
        level.sort(key=lambda c: (c.created_at, c.path[-1].campaign_id, c.path[-1].cycle_id))
        out.extend(level)
        frontier = level
    return out


class CycleArm(NamedTuple):
    """An arm its round PROPOSED, read: a reference a round re-read is no arm of that round."""

    round: int
    key: ArmKey
    state: ArmState
    reading: ArmReading


class CycleRoundsRead(NamedTuple):
    arms: list[CycleArm]
    # Every round it holds a record of, by its election: `None` for one that elected nothing.
    rounds: dict[int, Election | None]
    # Each `parent`-role arm by (round, key): the candidate its first record names it re-reading.
    rereads: dict[tuple[int, ArmKey], RoundArm]


def _cycle_spans(
    stores: Stores, hop: CycleHop, index: CycleIndex, *, moment: Moment | None
) -> list[LedgerSpan]:
    """A sibling fork's lifted rounds are its parent's, so it reads its own ledger; a cycle cut from
    ANOTHER tree has no parent cycle here, and its prefix is its own record."""
    layout = stores.campaigns.layout(hop)
    if index.parent is not None and index.parent_cycle_id is None:
        return ledger_chain(CycleDir(layout.cycle_dir), moment=moment)
    return [LedgerSpan(layout.ledger) if moment is None else moment.span(layout.ledger)]


_NO_ELECTION = ArmElection(held=False, selected=False, leading=False, crown=None)


def read_cycle(
    stores: Stores, hop: CycleHop, index: CycleIndex, *, moment: Moment | None
) -> CycleRoundsRead:
    """One cycle's own record: its arms in round and position order, read, and each round's election."""
    spans = _cycle_spans(stores, hop, index, moment=moment)
    standing = scan_standing_rounds(spans)
    closes = [standing.rounds[r].close for r in sorted(standing.rounds, reverse=True)]
    line = line_by_individual(next((c.overlap for c in closes if overlap_line(c.overlap)), None))
    # A replay reads no verify: the verdict a fresh read banks is the head's, never the moment's.
    verify = (
        {}
        if moment is not None
        else {
            key: read
            for key, (_, read) in scan_ledger_verify(stores.campaigns.ledger(hop)).graded.items()
        }
    )
    bench = scan_bench_readings(spans)

    def election_of(rnd: int) -> Election | None:
        if (election := standing.elections.get(rnd)) is not None:
            return election
        held = standing.rounds.get(rnd)
        return None if held is None else held.close.election

    # A round's panel is its proposals': a re-read parent is no arm of it.
    proposals: dict[int, list[tuple[int, ArmKey]]] = {}
    for at, held in standing.arms.items():
        if held.state.role != MeasurementRole.PARENT:
            proposals.setdefault(at[0], []).append(at)
    cut = {
        at: flag
        for ats in proposals.values()
        for at, flag in zip(
            ats,
            panel_cuts(
                [panel_counts(standing.arms[at].state, standing.arms[at].fold) for at in ats]
            ),
            strict=True,
        )
    }
    arms: list[CycleArm] = []
    rereads: dict[tuple[int, ArmKey], RoundArm] = {}
    for (rnd, key), held in sorted(
        standing.arms.items(),
        key=lambda kv: (kv[0][0], 0 if kv[1].state.position is None else kv[1].state.position.idx),
    ):
        if held.state.role == MeasurementRole.PARENT:
            if (position := held.state.position) is not None:
                rereads[rnd, key] = RoundArm(round=position.round, arm=key)
            continue
        election = election_of(rnd)
        reading = ArmReading.of(
            arm_name(key, held.state, rnd),
            held.state,
            fold=held.fold,
            cut=cut[rnd, key],
            election=_NO_ELECTION
            if election is None
            else ArmElection.of(
                key,
                held=True,
                selected=election.selected,
                leading=election.leading,
                electable=election.electable,
            ),
            fit=None if election is None else election.fit.get(key),
        )
        arms.append(
            CycleArm(
                rnd,
                key,
                held.state,
                reading.on_line(line).model_copy(
                    update={"verify": verify.get(key), "bench": bench.get((rnd, key))}
                ),
            )
        )
    numbers = sorted({rnd for rnd, _ in standing.arms} | set(standing.rounds))
    return CycleRoundsRead(arms, {rnd: election_of(rnd) for rnd in numbers}, rereads)
