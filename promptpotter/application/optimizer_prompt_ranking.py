"""Rank every L4 optimizer prompt state on disk by its ANCHOR-TO-ORIGIN paired effect — absolute
scores across runs are not comparable. Zero LLM calls; nothing persisted, nothing crowned."""

# The effect is in `mean_round_delta` LOGITS, not composite fitness. Fitness is what the
# per-campaign `campaign.yaml::scoring` formula made of the measurand, and this module pools one
# state across every campaign that ever measured it — so reducing on fitness averaged numbers
# produced by different formulas and called the result one quantity. The measurand is the same
# question in every campaign; the formula is not.

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.initialization.wiring import backend_type_of_dataset
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.l4.proxies import OUTER_PROXY_KEYS, cell_values
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.projections.live_dashboard.round_summary import (
    origin_rows_from_disk,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, campaign_cycles_dir
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.statistics import (
    paired_diff_posterior,
    t_critical,
)

if TYPE_CHECKING:
    from promptpotter.infrastructure.store.stores import Stores

# The connector whose cycles carry optimizer prompt candidates — i.e. the L4 recursion.
_PP_SELF_BACKEND_TYPE = "promptpotter"
_ORIGIN_HASH = "origin"
# The scored L4 measurand, in logits — see the module note on why this is not `fitness`.
_LEVEL_KEY = OUTER_PROXY_KEYS[0]


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


class OuterSpread(StrictModel):
    """How far apart the ranked arms actually are — the SD of ``anchor_effect`` across states.

    **Not a signal-to-noise ratio, and deliberately not one.** The noise half would be
    repeated readings of ONE (state, cell), and the inner instrument cannot produce them: it
    is content-addressed end to end — the campaign key, the ``shared_root`` caches, the seeded
    bank draw, CRN, the optimizer clamp — so a second ask replays the first answer and its
    spread is zero by construction, which reads as a perfect instrument. Manufacturing a
    second reading (numbering the draw to miss every cache) measures how noisy an LLM is,
    which is not a quantity the loop can optimise against.

    Measuring a candidate HARDER is ``verify``'s job: it re-scores one candidate on MORE
    samples, tightening the estimate of the thing being compared instead of sampling the same
    question twice. So the error bar on a comparison is the paired one this module already
    reports across cells, and this is the other half a reader needs — how much the arms differ
    at all. ``None`` when fewer than two states have been measured.
    """

    arm_effect_sd: float | None = None
    n_states: int = 0


class OptimizerPromptRanking(StrictModel):
    """The ranking — recomputed from disk on every read, never persisted."""

    generated_at: str
    n_cycles_scanned: int
    candidates: list[RankedOptimizerPrompt]  # ranked desc by anchor_effect
    spread: OuterSpread


def _state_hash(prompt_state: dict[str, dict[str, str]]) -> str:
    """Stable short hash of an optimizer prompt state; empty state ⇒ the origin sentinel."""
    if not prompt_state:
        return _ORIGIN_HASH
    canonical = json.dumps(prompt_state, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


class _Accum:
    def __init__(self, prompt_state: dict[str, dict[str, str]], label: str) -> None:
        self.prompt_state = prompt_state
        self.label = label
        self.provenance: list[EffectProvenance] = []
        # cell -> paired (candidate_fit, origin_fit) lists across occurrences
        self.cand_by_cell: dict[str, list[float]] = {}
        self.orig_by_cell: dict[str, list[float]] = {}


def _pp_self_campaign_dirs(stores: Stores) -> list[Path]:
    """Campaign dirs whose bound dataset drives the L4 recursion connector, asked via
    ``pipeline.yaml::backend_type`` — a NAME allowlist silently skips an A/B arm, a fork, a rename."""
    kind_of: dict[str, str] = {}  # once per dataset, not once per campaign
    dirs: list[Path] = []
    for child in stores.campaigns.iter_campaign_dirs():
        cfg = read_json_tolerant(child / "campaign.json", {})
        block_raw = cfg.get("campaign_config")
        block = block_raw if isinstance(block_raw, dict) else cfg
        dataset_name = str(block.get("dataset_name", ""))
        if not dataset_name:
            continue
        if dataset_name not in kind_of:
            kind_of[dataset_name] = backend_type_of_dataset(stores, dataset_name)
        if kind_of[dataset_name] == _PP_SELF_BACKEND_TYPE:
            dirs.append(child)
    return dirs


def rank_optimizer_prompts(stores: Stores) -> OptimizerPromptRanking:
    accums: dict[str, _Accum] = {}
    n_cycles = 0

    for campaign_dir in _pp_self_campaign_dirs(stores):
        cycles_dir = campaign_cycles_dir(campaign_dir)
        if not cycles_dir.is_dir():
            continue
        for cycle_dir in sorted(cycles_dir.iterdir()):
            rounds_dir = CycleLayout(cycle_dir).rounds
            if not rounds_dir.is_dir():
                continue
            origin_cells = cell_values(origin_rows_from_disk(cycle_dir), _LEVEL_KEY)
            if not origin_cells:
                continue
            n_cycles += 1
            for round_file in sorted(rounds_dir.glob(ROUND_GLOB)):
                if round_file.name == "round_0000.json":
                    continue
                _accumulate_round(
                    read_json_tolerant(round_file, {}),
                    origin_cells,
                    CycleHop(campaign_id=campaign_dir.name, cycle_id=cycle_dir.name),
                    accums,
                )

    rows = [_finalize(state_hash, acc) for state_hash, acc in accums.items()]
    rows.sort(key=lambda r: r.anchor_effect, reverse=True)
    return OptimizerPromptRanking(
        generated_at=utcnow_iso(),
        n_cycles_scanned=n_cycles,
        candidates=rows,
        spread=_outer_spread(rows),
    )


def _sample_sd(xs: list[float]) -> float | None:
    """Sample SD (n−1). ``None`` below two points — one reading has no spread, and reporting
    0.0 for it would claim perfect precision from a single measurement."""
    if len(xs) < 2:
        return None
    mean = sum(xs) / len(xs)
    return float((sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5)


def _outer_spread(rows: list[RankedOptimizerPrompt]) -> OuterSpread:
    return OuterSpread(
        arm_effect_sd=_sample_sd([r.anchor_effect for r in rows]),
        n_states=len(rows),
    )


def _accumulate_round(
    doc: dict[str, Any],
    origin_cells: dict[str, float],
    hop: CycleHop,
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
        cand_cells = cell_values(
            (doc.get("all_candidate_results") or {}).get(cand_id) or [], _LEVEL_KEY
        )
        paired = {c: cand_cells[c] for c in cand_cells if c in origin_cells}
        if not paired:
            continue
        acc = accums.get(state_hash)
        if acc is None:
            acc = _Accum(prompt_state, str(cand.get("label") or state_hash))
            accums[state_hash] = acc
        acc.provenance.append(
            EffectProvenance(
                campaign_id=hop.campaign_id,
                cycle_id=hop.cycle_id,
                round=round_num,
                candidate_id=cand_id,
            )
        )
        for cell, cand_fit in paired.items():
            acc.cand_by_cell.setdefault(cell, []).append(cand_fit)
            acc.orig_by_cell.setdefault(cell, []).append(origin_cells[cell])


def _coerce_state(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for node, fields in raw.items():
        if isinstance(fields, dict):
            out[str(node)] = {str(k): str(v) for k, v in fields.items()}
    return out


def _finalize(state_hash: str, acc: _Accum) -> RankedOptimizerPrompt:
    """Aggregate one state into its ranked row — **per cell, then across cells**, so the SE comes from
    n = CELLS and a cell measured five times cannot outweigh five cells measured once."""
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
        cell_cand.append(sum(cand_vals) / len(cand_vals))
        cell_orig.append(sum(orig_vals) / len(orig_vals))

    anchor, anchor_se, n_cells = paired_diff_posterior(cell_cand, cell_orig)
    # Student-t on the CELL count, not z: the SE is estimated from the same handful of
    # cells it widens (≈7 cells → 2.45, not 1.96). Same rule as the per-round interval
    # (`scoring/selection.py::matched_parent_lift`), so a corpus leader and a round verdict
    # cannot disagree by bracketing the same evidence two ways.
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
