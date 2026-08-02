"""Rank every L4 optimizer prompt state on disk by how much it beat the origin.

Zero LLM calls, every number re-derived from the on-disk round files, recomputed per read
— nothing is persisted, nothing is crowned, and naming a leader is where this stops
(graduating one is a hand-edit, not a verb). It ranks by the **anchor-to-origin**
paired effect: the per-cell ``candidate − origin`` composite diff, paired on the shared
cells that candidate was measured on. Absolute scores across runs are not comparable and
only these paired effects are, which is the whole reason the metric looks like this.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.wiring import backend_type_of_dataset
from promptpotter.domain.l4.verdict import cell_fitness
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    origin_rows_from_disk,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.statistics import (
    min_detectable_effect,
    paired_diff_posterior,
    t_critical,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

# The connector whose cycles carry optimizer prompt candidates — i.e. the L4 recursion.
_PP_SELF_BACKEND_TYPE = "promptpotter"
_ORIGIN_HASH = "origin"


class CellEffect(StrictModel):
    """One environment cell's paired (candidate − origin) effect for a state."""

    cell: str
    mean_d: float
    n: int


class EffectProvenance(StrictModel):
    """Where one occurrence of a candidate state was measured on disk."""

    campaign_id: str
    cycle_id: str
    round: int
    candidate_id: str


class RankedOptimizerPrompt(StrictModel):
    """One unique optimizer prompt state, aggregated across every occurrence in the corpus."""

    state_hash: str
    label: str
    prompt_state: dict[str, dict[str, str]]  # {node: {field: text}} — the optimizer prompt edit
    provenance: list[EffectProvenance]
    per_cell_effects: list[CellEffect]
    anchor_effect: float  # mean of the PER-CELL paired diffs — one point per cell, not per
    # occurrence, so an over-measured cell cannot outweigh uniform goodness (see _finalize)
    ci_lo: float
    ci_hi: float
    n_cells: int
    n_measurements: int


class OuterSnr(StrictModel):
    """Can the panel resolve one optimizer prompt from another yet — and if not, by how much.

    Finish-line item 7's "two series, not one ratio", derived from the corpus this module
    already walks rather than bought with re-runs. Both series fall out of the same
    accumulators: a state measured more than once on the SAME cell gives the NOISE (the panel
    re-reading one arm), and the spread of ``anchor_effect`` across states gives the SIGNAL
    (arms genuinely differing).

    Until this existed the ratio was hand-computed off a corpus snapshot and written into prose
    — which is how the spec came to state two different answers for the same question (§4's
    "~35 cells" against item 7's own ``(2.8·σ/d)²``, which gives roughly half that). A number
    the loop recomputes on every read cannot drift from the disk it was derived from.

    Every field is ``None`` when the corpus cannot support it: fewer than two states, or no
    cell measured twice under one state. Absent is the honest answer — a fabricated 0 noise
    reads as a panel that can resolve anything.
    """

    # Pooled SD of repeated readings of the SAME (state, cell) — measurement noise, σ.
    within_sd: float | None = None
    within_n_groups: int = 0  # (state, cell) pairs with ≥2 readings behind `within_sd`
    # SD of `anchor_effect` across distinct states — how far apart the arms actually are, d.
    between_sd: float | None = None
    between_n_states: int = 0
    # (2.8·σ/d)² — cells a verdict needs at the current noise and contrast. `2.8` is
    # `min_detectable_effect`'s z(0.975)+z(0.8). Compare against the panel you actually run.
    n_cells_to_verdict: int | None = None


class OptimizerPromptRanking(StrictModel):
    """The ranking — recomputed from disk on every read, never persisted."""

    generated_at: str
    n_cycles_scanned: int
    candidates: list[RankedOptimizerPrompt]  # ranked desc by anchor_effect
    snr: OuterSnr


def _state_hash(prompt_state: dict[str, dict[str, str]]) -> str:
    """Stable short hash of an optimizer prompt state; empty state ⇒ the origin sentinel."""
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
        self.provenance: list[EffectProvenance] = []
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


def rank_optimizer_prompts(store: Stores) -> OptimizerPromptRanking:
    accums: dict[str, _Accum] = {}
    n_cycles = 0

    for campaign_dir in _pp_self_campaign_dirs(store):
        cycles_dir = campaign_cycles_dir(campaign_dir)
        if not cycles_dir.is_dir():
            continue
        for cycle_dir in sorted(cycles_dir.iterdir()):
            rounds_dir = CycleLayout(cycle_dir).rounds
            if not rounds_dir.is_dir():
                continue
            origin_cells = cell_fitness(origin_rows_from_disk(cycle_dir))
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
    return OptimizerPromptRanking(
        generated_at=utcnow_iso(),
        n_cycles_scanned=n_cycles,
        candidates=rows,
        snr=_outer_snr(accums, rows),
    )


def _sample_sd(xs: list[float]) -> float | None:
    """Sample SD (n−1). ``None`` below two points — one reading has no spread, and reporting
    0.0 for it would claim perfect precision from a single measurement."""
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    return float((sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5)


def _outer_snr(accums: dict[str, _Accum], rows: list[RankedOptimizerPrompt]) -> OuterSnr:
    """Split the corpus into the noise series and the signal series (see :class:`OuterSnr`).

    Noise is POOLED across (state, cell) groups rather than averaged over their SDs: a group of
    2 readings and a group of 9 say different amounts about σ, and averaging their SDs would
    weight them equally. Pooling on (n−1) degrees of freedom is the standard way to say so.
    """
    ss, df = 0.0, 0
    groups = 0
    for acc in accums.values():
        for vals in acc.cand_by_cell.values():
            if len(vals) < 2:
                continue
            mean = sum(vals) / len(vals)
            ss += sum((v - mean) ** 2 for v in vals)
            df += len(vals) - 1
            groups += 1
    within = (ss / df) ** 0.5 if df > 0 else None
    between = _sample_sd([r.anchor_effect for r in rows])
    n_needed: int | None = None
    if within is not None and between is not None and between > 0.0:
        # `min_detectable_effect(σ)` IS `2.8·σ` — call it rather than re-typing the constant,
        # so the alpha/power this planning number assumes can never drift from the alpha/power
        # the verdict's own `mde_remaining` reports.
        n_needed = math.ceil((min_detectable_effect(within) / between) ** 2)
    return OuterSnr(
        within_sd=within,
        within_n_groups=groups,
        between_sd=between,
        between_n_states=len(rows),
        n_cells_to_verdict=n_needed,
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
            EffectProvenance(
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


def _finalize(state_hash: str, acc: _Accum) -> RankedOptimizerPrompt:
    """Aggregate one optimizer prompt state's measurements into its ranked row.

    **Aggregation is PER CELL, then across cells** — the same two-stage shape
    ``domain/l4/verdict.py::compute_outer_verdict`` uses, and for the same reasons: the SE
    comes from n = *cells* (a ~7-point panel), not n = total *measurements*, and a cell
    measured five times cannot outweigh five cells measured once. Both matter because this
    ranking is the ONLY thing standing between the corpus and a human hand-graduating a
    optimizer prompt into ``promptpotter/assets/optimizer/``, so an overstated CI misleads a person
    making an irreversible edit.
    """
    per_cell: list[CellEffect] = []
    cell_cand: list[float] = []
    cell_orig: list[float] = []
    n_meas = 0
    for cell in sorted(acc.cand_by_cell):
        cand_vals = acc.cand_by_cell[cell]
        orig_vals = acc.orig_by_cell[cell]
        mean_d, _se_d, n = paired_diff_posterior(cand_vals, orig_vals)
        per_cell.append(CellEffect(cell=cell, mean_d=mean_d, n=n))
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
    return RankedOptimizerPrompt(
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
