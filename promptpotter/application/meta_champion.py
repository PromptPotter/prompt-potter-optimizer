"""Meta-champion — reduce the on-disk pp-self corpus to one ranked champion table.

Zero LLM calls, every number re-derived from the on-disk round files, recomputed per read
— no persisted registry, no reigning-champion pointer, and naming a winner is where this
stops (graduating one is a hand-edit, not a verb). It ranks by the **anchor-to-origin**
paired effect: the per-cell ``candidate − origin`` composite diff, paired on the shared
cells that candidate was measured on. Absolute scores across runs are not comparable and
only these paired effects are, which is the whole reason the metric looks like this.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.wiring import backend_type_of_dataset
from promptpotter.domain.l4.verdict import cell_fitness
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    origin_cells_from_disk,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import campaign_cycles_dir
from promptpotter.shared.statistics import paired_diff_posterior, t_critical

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

# The connector whose cycles carry meta-prompt candidates — i.e. the L4 recursion.
_PP_SELF_BACKEND_TYPE = "promptpotter"
_ORIGIN_HASH = "origin"


class ChampionCellEffect(StrictModel):
    """One environment cell's paired (candidate − origin) effect for a state."""

    cell: str
    mean_d: float
    n: int


class ChampionProvenance(StrictModel):
    """Where one occurrence of a candidate state was measured on disk."""

    campaign_id: str
    cycle_id: str
    round: int
    candidate_id: str


class ChampionCandidate(StrictModel):
    """One unique meta-prompt state, aggregated across every occurrence in the corpus."""

    state_hash: str
    label: str
    prompt_state: dict[str, dict[str, str]]  # {node: {field: text}} — the meta-prompt edit
    provenance: list[ChampionProvenance]
    per_cell_effects: list[ChampionCellEffect]
    anchor_effect: float  # mean of the PER-CELL paired diffs — one point per cell, not per
    # occurrence, so an over-measured cell cannot outweigh uniform goodness (see _finalize)
    ci_lo: float
    ci_hi: float
    n_cells: int
    n_measurements: int


class ChampionRegistry(StrictModel):
    """The ranked champion table — recomputed from disk on every read, never persisted."""

    generated_at: str
    n_cycles_scanned: int
    candidates: list[ChampionCandidate]  # ranked desc by anchor_effect


def _state_hash(prompt_state: dict[str, dict[str, str]]) -> str:
    """Stable short hash of a meta-prompt state; empty state ⇒ the origin sentinel."""
    if not prompt_state:
        return _ORIGIN_HASH
    canonical = json.dumps(prompt_state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def _cell_composites(round_doc: dict[str, Any], candidate_id: str) -> dict[str, float]:
    """Per-cell composite fitness for one candidate: ``{cell_query: fitness}``.

    One row per outer sample: ``query`` is the cell id, ``fitness`` that cell's composite
    (its mean equals the candidate's round ``composite_fitness``). Thin adapter over the
    pure ``domain.l4.cell_fitness`` — input is an already-loaded round doc, not a disk read.
    """
    rows = (round_doc.get("all_candidate_results") or {}).get(candidate_id) or []
    return cell_fitness(rows)


class _Accum:
    """Mutable per-state accumulator collected during the disk walk."""

    def __init__(self, prompt_state: dict[str, dict[str, str]], label: str) -> None:
        self.prompt_state = prompt_state
        self.label = label
        self.provenance: list[ChampionProvenance] = []
        # cell -> paired (candidate_fit, origin_fit) lists across occurrences
        self.cand_by_cell: dict[str, list[float]] = {}
        self.orig_by_cell: dict[str, list[float]] = {}


def _pp_self_campaign_dirs(store: Stores) -> list[Path]:
    """Campaign dirs whose bound dataset drives the L4 recursion connector.

    Asks each dataset's ``pipeline.yaml::backend_type`` — the same predicate the sidebar's
    ``isSelfOptimization`` reads — never a list of dataset NAMES. A name allowlist silently
    skipped an A/B arm, a fork, or a renamed dataset instead of loudly rejecting it, and
    widening it by hand is what made it wrong twice.
    """
    kind_of: dict[str, str] = {}  # once per dataset, not once per campaign
    dirs: list[Path] = []
    for child in store.campaigns.iter_campaign_dirs():
        cfg = read_json_tolerant(child / "campaign.json", {})
        block_raw = cfg.get("campaign_config")
        block = block_raw if isinstance(block_raw, dict) else cfg
        dataset_name = str(block.get("dataset_name", ""))
        if not dataset_name:
            continue
        if dataset_name not in kind_of:
            kind_of[dataset_name] = backend_type_of_dataset(store, dataset_name)
        if kind_of[dataset_name] == _PP_SELF_BACKEND_TYPE:
            dirs.append(child)
    return dirs


def reduce_corpus(store: Stores) -> ChampionRegistry:
    accums: dict[str, _Accum] = {}
    n_cycles = 0

    for campaign_dir in _pp_self_campaign_dirs(store):
        cycles_dir = campaign_cycles_dir(campaign_dir)
        if not cycles_dir.is_dir():
            continue
        for cycle_dir in sorted(cycles_dir.iterdir()):
            rounds_dir = cycle_dir / "rounds"
            if not rounds_dir.is_dir():
                continue
            origin_cells = origin_cells_from_disk(cycle_dir)
            if not origin_cells:
                continue
            n_cycles += 1
            for round_file in sorted(rounds_dir.glob("round_*.json")):
                if round_file.name == "round_0000.json":
                    continue
                _accumulate_round(
                    read_json_tolerant(round_file, {}),
                    origin_cells,
                    campaign_dir.name,
                    cycle_dir.name,
                    accums,
                )

    rows = [_finalize(state_hash, acc) for state_hash, acc in accums.items()]
    rows.sort(key=lambda r: r.anchor_effect, reverse=True)
    return ChampionRegistry(
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        n_cycles_scanned=n_cycles,
        candidates=rows,
    )


def _accumulate_round(
    doc: dict[str, Any],
    origin_cells: dict[str, float],
    campaign_id: str,
    cycle_id: str,
    accums: dict[str, _Accum],
) -> None:
    round_num = int(doc.get("round", 0) or 0)
    for cand in doc.get("candidate_scores") or []:
        cand_id = str(cand.get("candidate_id", ""))
        if not cand_id:
            continue
        prompt_state = _coerce_state(cand.get("pipeline_params_override"))
        state_hash = _state_hash(prompt_state)
        if state_hash == _ORIGIN_HASH:
            continue  # the no-op arm anchors others; it is not itself a ranked candidate
        cand_cells = _cell_composites(doc, cand_id)
        paired = {c: cand_cells[c] for c in cand_cells if c in origin_cells}
        if not paired:
            continue
        acc = accums.get(state_hash)
        if acc is None:
            acc = _Accum(prompt_state, str(cand.get("label") or state_hash))
            accums[state_hash] = acc
        acc.provenance.append(
            ChampionProvenance(
                campaign_id=campaign_id, cycle_id=cycle_id, round=round_num, candidate_id=cand_id
            )
        )
        for cell, cand_fit in paired.items():
            acc.cand_by_cell.setdefault(cell, []).append(cand_fit)
            acc.orig_by_cell.setdefault(cell, []).append(origin_cells[cell])


def _coerce_state(raw: Any) -> dict[str, dict[str, str]]:
    """Coerce a candidate's ``pipeline_params_override`` to ``{node:{field:str}}``."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for node, fields in raw.items():
        if isinstance(fields, dict):
            out[str(node)] = {str(k): str(v) for k, v in fields.items()}
    return out


def _finalize(state_hash: str, acc: _Accum) -> ChampionCandidate:
    """Aggregate one meta-prompt state's measurements into its ranked row.

    **Aggregation is PER CELL, then across cells** — the same two-stage shape
    ``domain/l4/verdict.py::compute_outer_verdict`` uses, and for the same reasons: the SE
    comes from n = *cells* (a ~7-point panel), not n = total *measurements*, and a cell
    measured five times cannot outweigh five cells measured once. Both matter because this
    ranking is the ONLY thing standing between the corpus and a human hand-graduating a
    meta-prompt into ``promptpotter/assets/optimizer/``, so an overstated CI misleads a person
    making an irreversible edit.
    """
    per_cell: list[ChampionCellEffect] = []
    cell_cand: list[float] = []
    cell_orig: list[float] = []
    n_meas = 0
    for cell in sorted(acc.cand_by_cell):
        cand_vals = acc.cand_by_cell[cell]
        orig_vals = acc.orig_by_cell[cell]
        mean_d, _se_d, n = paired_diff_posterior(cand_vals, orig_vals)
        per_cell.append(ChampionCellEffect(cell=cell, mean_d=mean_d, n=n))
        n_meas += n
        # ONE paired point per cell — the cell's own mean level. Equal-length lists make
        # the elementwise paired mean identical to the difference of means, so this is
        # exactly ``mean_d`` re-expressed as a (candidate, origin) pair for stage two.
        cell_cand.append(sum(cand_vals) / len(cand_vals) if cand_vals else 0.0)
        cell_orig.append(sum(orig_vals) / len(orig_vals) if orig_vals else 0.0)

    anchor, anchor_se, n_cells = paired_diff_posterior(cell_cand, cell_orig)
    # Student-t on the CELL count, not z: the SE is estimated from the same handful of
    # cells it widens (≈7 cells → 2.45, not 1.96). Matches `compute_outer_verdict`.
    crit = t_critical(max(n_cells - 1, 1))
    return ChampionCandidate(
        state_hash=state_hash,
        label=acc.label,
        prompt_state=acc.prompt_state,
        provenance=acc.provenance,
        per_cell_effects=per_cell,
        anchor_effect=anchor,
        ci_lo=anchor - crit * anchor_se,
        ci_hi=anchor + crit * anchor_se,
        n_cells=len(per_cell),
        n_measurements=n_meas,
    )
