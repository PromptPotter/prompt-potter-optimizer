"""Every CELL a scope holds — the store walks behind `GET /datasets/{name}/cells`.

Three scopes, three sources, one shape (`domain/cells.py`):

- **cycle** — the round files (`rounds/round_*.json::all_candidate_results`, named by each round's
  `candidate_scores`), plus the round still being measured off `dashboard.json`, whose round file
  lands only at its close. Merged HERE and nowhere else, so the live round is counted once.
- **campaign** — every cycle of one campaign, pooled.
- **dataset** — the archive runs filed under the dataset, whatever campaign paid for them.

Candidates come back in chronological order and cells in candidate order; the router lays the
sample ranking over that. A read model — it decides nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from promptpotter.domain.cells import CellCandidate, CellRow
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.dashboard_rows import sample_status
from promptpotter.domain.scoring import recorded_cost_s
from promptpotter.domain.spend import TokenAccount
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, cycle_dir_for

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["ScopeCells", "campaign_cells", "cycle_cells", "dataset_cells"]

_PREDICTED_CHARS = 120

ScopeCells = tuple[list[CellCandidate], list[CellRow]]


def _trim(text: object) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= _PREDICTED_CHARS else t[: _PREDICTED_CHARS - 1] + "…"


def _row_cell(item: dict[str, Any], *, run_id: str, key: str) -> CellRow | None:
    """A banked measurement row as a cell — the shape both the round files and the archive hold."""
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


def _live_cells(
    cycle_dir: Path, cycle_id: str, closed: set[int], wanted: set[int] | None
) -> ScopeCells:
    """The round IN FLIGHT, off `dashboard.json`'s served rows — the projection's own grading, not
    a second one. A round a file already carries is skipped."""
    dash = read_json_tolerant(CycleLayout(cycle_dir).dashboard)
    current = dash.get("current_round") if isinstance(dash, dict) else None
    if not isinstance(current, dict):
        return [], []
    round_no = current.get("round")
    if not isinstance(round_no, int) or round_no in closed:
        return [], []
    run_of = {
        str(c.get("label")): c
        for c in current.get("candidates") or []
        if isinstance(c, dict) and c.get("label")
    }
    block = ((current.get("nodes") or {}).get("l1_score") or {}).get("output") or {}
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    for cand in block.get("candidates") or []:
        if not isinstance(cand, dict) or not cand.get("label"):
            continue
        label = str(cand["label"])
        served = run_of.get(label) or {}
        run_id = str(served.get("run_id") or "")
        key = f"{cycle_id}/{label}"
        candidates.append(
            CellCandidate(
                key=key,
                label=label,
                candidate_id=served.get("candidate_id"),
                run_id=run_id,
                round=round_no,
                cycle_id=cycle_id,
                live=True,
            )
        )
        for s in cand.get("samples") or []:
            sid = s.get("sample_id") if isinstance(s, dict) else None
            if not isinstance(sid, int) or (wanted is not None and sid not in wanted):
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
    return candidates, cells


def cycle_cells(stores: Stores, hop: CycleHop, wanted: set[int] | None = None) -> ScopeCells:
    """One cycle's cells: its closed rounds, then the round in flight. *wanted* keeps only those
    samples' cells; the candidates are kept whole, since a candidate is a column, not a row."""
    cycle_dir = cycle_dir_for(stores.base_dir, hop)
    rounds_dir = CycleLayout(cycle_dir).rounds
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    closed: set[int] = set()
    for round_path in sorted(rounds_dir.glob(ROUND_GLOB)) if rounds_dir.is_dir() else ():
        doc = read_json_tolerant(round_path)
        if not isinstance(doc, dict) or not isinstance(doc.get("round"), int):
            continue
        round_no = int(doc["round"])
        closed.add(round_no)
        acr = doc.get("all_candidate_results") or {}
        for cs in doc.get("candidate_scores") or []:
            if not isinstance(cs, dict) or not isinstance(cs.get("candidate_id"), str):
                continue
            label = str(cs["label"])
            key = f"{hop.cycle_id}/{label}"
            run_id = str(cs.get("run_id") or "")
            candidates.append(
                CellCandidate(
                    key=key,
                    label=label,
                    candidate_id=cs["candidate_id"],
                    run_id=run_id,
                    round=round_no,
                    cycle_id=hop.cycle_id,
                )
            )
            for item in acr.get(cs["candidate_id"]) or []:
                if not isinstance(item, dict):
                    continue
                if wanted is not None and item.get("sample_id") not in wanted:
                    continue
                cell = _row_cell(item, run_id=run_id, key=key)
                if cell is not None:
                    cells.append(cell)
    live_candidates, live_cells = _live_cells(cycle_dir, hop.cycle_id, closed, wanted)
    return candidates + live_candidates, cells + live_cells


def campaign_cells(stores: Stores, campaign_id: str, wanted: set[int] | None = None) -> ScopeCells:
    """Every cycle of one campaign, pooled in the order the store enumerates them."""
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    for entry in stores.campaigns.enumerate_cycles():
        if entry["campaign_id"] != campaign_id:
            continue
        cands, rows = cycle_cells(
            stores, CycleHop(campaign_id=campaign_id, cycle_id=entry["cycle_id"]), wanted
        )
        candidates.extend(cands)
        cells.extend(rows)
    return candidates, cells


def dataset_cells(
    stores: Stores, *, dataset_name: str, wanted: set[int] | None = None
) -> ScopeCells:
    """Every archive run filed under *dataset_name*, oldest first. *dataset_name* is REQUIRED: a
    ``sample_id`` names a sample only within one dataset."""
    runs: list[tuple[str, str, dict[str, Any]]] = []
    for entry in stores.archive.list_all(dataset_name=dataset_name):
        run_id = entry["run_id"]
        detail = stores.archive.load_by_id(run_id)
        if detail is not None:
            runs.append((str(detail.get("created_at", "")), run_id, detail))
    runs.sort(key=lambda r: (r[0], r[1]))
    candidates: list[CellCandidate] = []
    cells: list[CellRow] = []
    for created_at, run_id, detail in runs:
        candidates.append(
            CellCandidate(
                key=run_id,
                label=str(detail.get("name") or run_id[:12]),
                run_id=run_id,
                created_at=created_at or None,
            )
        )
        for item in detail.get("measurements", []):
            if not isinstance(item, dict):
                continue
            if wanted is not None and item.get("sample_id") not in wanted:
                continue
            cell = _row_cell(item, run_id=run_id, key=run_id)
            if cell is not None:
                cells.append(cell)
    return candidates, cells
