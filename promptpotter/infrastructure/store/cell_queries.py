"""Every CELL a cycle or campaign holds — the store walks behind `GET /datasets/{name}/cells`.

Two scopes, one source each, one shape (`domain/cells.py`):

- **cycle** — the round files (`rounds/round_*.json::all_candidate_results`, named by each round's
  `candidate_scores`), plus the round still being measured off `dashboard.json`, whose round file
  lands only at its close. Merged HERE and nowhere else, so the live round is counted once.
- **campaign** — every cycle of one campaign, pooled.

The DATASET scope reads the archive, which banks no grade, so it grades there first — it lives
with the grading, in `application/scoring/cells.py`. Candidates come back in chronological order
and cells in candidate order; the router lays the sample ranking over that. A read model — it
decides nothing."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from promptpotter.domain.cells import CellCandidate, CellRow
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.scoring import recorded_cost_s
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import derived, file_sig

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["ScopeCells", "campaign_cells", "cycle_cells", "row_cell"]

_PREDICTED_CHARS = 120

ScopeCells = tuple[list[CellCandidate], list[CellRow]]


def _trim(text: object) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= _PREDICTED_CHARS else t[: _PREDICTED_CHARS - 1] + "…"


def row_cell(item: dict[str, Any], *, run_id: str, key: str) -> CellRow | None:
    """A GRADED measurement row as a cell — a round file's, or an archive row its reader graded."""
    sid = item.get("sample_id")
    if not isinstance(sid, int):
        return None
    fitness = item.get("fitness")
    account = TokenAccount.from_step_tokens(item.get("pipeline_data"))
    return CellRow(
        sample_id=sid,
        run_id=run_id,
        candidate=key,
        status=sample_status(item),
        fitness=float(fitness) if isinstance(fitness, int | float) else None,
        cached=bool(item.get("cached", False)),
        predicted=_trim(item.get("predicted")),
        seconds=recorded_cost_s(item),  # type: ignore[arg-type]
        input_tokens=account.input if account else None,
        output_tokens=account.output if account else None,
    )


_RoundCells = tuple[int, ScopeCells]


def _live_round(dashboard: Path, cycle_id: str) -> _RoundCells | None:
    """The round IN FLIGHT, off `dashboard.json`'s served rows — the projection's own grading, not
    a second one."""
    dash = read_json_tolerant(dashboard)
    current = dash.get("current_round") if isinstance(dash, dict) else None
    if not isinstance(current, dict):
        return None
    round_no = current.get("round")
    if not isinstance(round_no, int):
        return None
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    for cand in current.get("candidates") or []:
        if not isinstance(cand, dict) or not cand.get("label"):
            continue
        label = str(cand["label"])
        run_id = cand.get("run_id")
        key = f"{cycle_id}/{label}"
        candidates.append(
            CellCandidate(
                key=key,
                label=label,
                candidate_id=cand.get("candidate_id"),
                run_id=run_id,
                round=round_no,
                cycle_id=cycle_id,
                live=True,
            )
        )
        samples = cand.get("samples") or []
        if run_id is None:
            if samples:
                raise ValueError(f"{cycle_id} live candidate {label} holds cells but names no run")
            continue
        for s in samples:
            sid = s.get("sample_id") if isinstance(s, dict) else None
            if not isinstance(sid, int):
                continue
            cells.append(
                CellRow(
                    sample_id=sid,
                    run_id=run_id,
                    candidate=key,
                    status=s["status"],
                    fitness=s.get("fitness"),
                    cached=bool(s.get("cached", False)),
                    predicted=str(s.get("predicted") or ""),
                    seconds=s.get("cost_s"),
                    input_tokens=s.get("input_tokens"),
                    output_tokens=s.get("output_tokens"),
                )
            )
    return round_no, (candidates, cells)


def _closed_round(round_path: Path, cycle_id: str) -> _RoundCells | None:
    doc = read_json_tolerant(round_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("round"), int):
        return None
    round_no = int(doc["round"])
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    acr = doc.get("all_candidate_results") or {}
    for cs in doc.get("candidate_scores") or []:
        if not isinstance(cs, dict) or not isinstance(cs.get("candidate_id"), str):
            continue
        label = str(cs["label"])
        key = f"{cycle_id}/{label}"
        run_id = cs["run_id"]
        candidates.append(
            CellCandidate(
                key=key,
                label=label,
                candidate_id=cs["candidate_id"],
                run_id=run_id,
                round=round_no,
                cycle_id=cycle_id,
            )
        )
        rows = acr.get(cs["candidate_id"]) or []
        if run_id is None:
            if rows:
                raise ValueError(f"{round_path} candidate {label} holds cells but names no run")
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            cell = row_cell(item, run_id=run_id, key=key)
            if cell is not None:
                cells.append(cell)
    return round_no, (candidates, cells)


def cycle_cells(stores: Stores, hop: CycleHop, wanted: set[int] | None = None) -> ScopeCells:
    """One cycle's cells: its closed rounds, then the round in flight. *wanted* keeps only those
    samples' cells; the candidates are kept whole, since a candidate is a column, not a row."""
    layout = CycleLayout(cycle_dir_for(stores.base_dir, hop))
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    closed: set[int] = set()
    cycle_id = hop.cycle_id
    paths = sorted(layout.rounds.glob(ROUND_GLOB)) if layout.rounds.is_dir() else []
    reads = [
        derived(
            ("round_cells", path),
            sig=file_sig(path),
            compute=partial(_closed_round, path, cycle_id),
        )
        for path in paths
    ]
    live = derived(
        ("live_cells", layout.dashboard),
        sig=file_sig(layout.dashboard),
        compute=partial(_live_round, layout.dashboard, cycle_id),
    )
    for read in reads:
        if read is not None:
            closed.add(read[0])
    for read in [*reads, None if live is None or live[0] in closed else live]:
        if read is None:
            continue
        round_candidates, round_cells = read[1]
        candidates.extend(round_candidates)
        cells.extend(c for c in round_cells if wanted is None or c.sample_id in wanted)
    return candidates, cells


def campaign_cells(stores: Stores, campaign_id: str, wanted: set[int] | None = None) -> ScopeCells:
    """Every cycle of one campaign, pooled in the order the store enumerates them."""
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    for entry in stores.campaigns.enumerate_cycles():
        if entry.campaign_id != campaign_id:
            continue
        cands, rows = cycle_cells(
            stores, CycleHop(campaign_id=campaign_id, cycle_id=entry.cycle_id), wanted
        )
        candidates.extend(cands)
        cells.extend(rows)
    return candidates, cells
