from __future__ import annotations

from pathlib import Path

from promptpotter.domain.cycle_index import CycleIndex, IndexRound, Intervention
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import CycleRecord
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    moves_index,
    scan_cell_formula,
    scan_cycle_facts,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.io import write_json
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import Moment


def read_cycle_index(cycle_dir: Path, *, moment: Moment | None) -> CycleIndex | None:
    """A cycle's own facts are its own ledger's; its rounds stand over its whole chain."""
    ledger = CycleLayout(cycle_dir).ledger
    facts = scan_cycle_facts(ledger, None if moment is None else moment.span(ledger).until)
    minted = facts.minted
    if minted is None:
        return None
    chain = ledger_chain(CycleDir(cycle_dir), moment=moment)
    standing = scan_standing_rounds(chain)
    ended, final = facts.ended, facts.final
    return CycleIndex(
        cycle_id=cycle_dir.name,
        created_at=minted.timestamp,
        updated_at=facts.updated_at,
        parent=minted.parent,
        forked_at_offset=minted.forked_at_offset,
        cut_round=minted.cut_round,
        fork=facts.fork,
        spawned_by=facts.spawned_by,
        scorer_cell_formula=scan_cell_formula(chain),
        interventions=[
            Intervention(kind=act.kind, at=act.timestamp) for act in facts.interventions
        ],
        stop_reason=None if ended is None else ended.stop_reason,
        finished_at=None if ended is None else ended.timestamp,
        interrupted_round=None if final is None else final.interrupted_round,
        crash_traceback=facts.crash_traceback,
        final=None if final is None else final.final,
        superseded_by=facts.superseded_by,
        rounds=[
            IndexRound(
                round=n, accuracy=None if (fold := held.close.fold) is None else fold.accuracy
            )
            for n, held in standing.rounds.items()
        ],
        open_rounds=sorted({rnd for rnd, _ in standing.arms} - set(standing.rounds)),
        elects_on=standing.elections[max(standing.elections)].elects_on
        if standing.elections
        else None,
        standing=standing.standing,
    )


def write_cycle_index(cycle_dir: Path) -> None:
    index = read_cycle_index(cycle_dir, moment=None)
    if index is not None:
        write_json(CycleLayout(cycle_dir).manifest, index.model_dump(mode="json"))


class CycleIndexProjection(Projection):
    def __init__(self, cycle_dir: CycleDir) -> None:
        self._cycle_dir = Path(cycle_dir)

    def feed(self, record: CycleRecord) -> None:
        if moves_index(record):
            write_cycle_index(self._cycle_dir)
