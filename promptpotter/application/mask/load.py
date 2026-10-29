from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.mask.record import (
    Lens,
    MaskCandidate,
    MaskCycle,
    MaskReading,
    MaskRecord,
    MaskRound,
    SpineCycle,
)
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.scoring.closed_rounds import closed_rounds, cycle_bank
from promptpotter.application.scoring.formula.compiler import (
    origin_anchors,
    parse_dials,
    realize_dials,
    split_scoring_block,
)
from promptpotter.application.scoring.judges.registry import judge_instrument
from promptpotter.application.scoring.metrics import fold_cells
from promptpotter.domain.arms import ArmState, Cut, arm_elimination, is_electable
from promptpotter.domain.campaign_config import CampaignConfig, apply_config_overrides
from promptpotter.domain.cell_fold import measured_cells
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import ConfigOverrides
from promptpotter.domain.scoring import (
    CellSheet,
    GradedCell,
    Scorer,
    anchored_criterion_dials,
)
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    StandingRounds,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.read_model import LedgerSpan
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.application.bench.difficulty import DifficultyView


def lens_overrides(formula: str | None) -> ConfigOverrides:
    return ConfigOverrides(scoring={"per_cell": formula}) if formula else ConfigOverrides()


def _dial_anchors(config: CampaignConfig, origin_rows: list[GradedCell]) -> dict[str, float]:
    """The criterion's own anchors win, so a lens left at the active weights reads the record back exactly."""
    spec = split_scoring_block(config.scoring, judge_instrument=judge_instrument(config.judges))
    active = anchored_criterion_dials(spec.per_cell) if spec.per_cell else None
    locked = {name: d.anchor for name, d in (active or {}).items() if d.anchor is not None}
    return {**origin_anchors(origin_rows), **locked}


def read_rows(sheet: CellSheet, scorer: Scorer) -> MaskReading | None:
    """``None`` where no row carries a verdict: no reading, never a 0.0 floor."""
    graded = sheet if sheet.scorer_id == scorer.id else scorer.sheet(cell.facts for cell in sheet)
    folded = fold_cells(graded)
    if folded.composite_fitness is None:
        return None
    return MaskReading(
        composite_fitness=folded.composite_fitness,
        accuracy=folded.accuracy,
        n_scored=len(measured_cells(graded)),
    )


def _branch_round(index: CycleIndex) -> int | None:
    """The parent round a fork's start was proposed in: its cut, or for one lifting none its arm's."""
    return None if index.fork is None else index.cut_round or index.fork.from_candidate.round


def load_lineage_spine(difficulty: DifficultyView) -> list[SpineCycle]:
    """Every cycle of the run's campaign read on ONE scale, so θ sum up a lineage that forks."""
    session = difficulty.session
    stores, scorer = session.store, session.state.scoring.require_scorer()
    edges: list[tuple[str, CycleIndex, frozenset[int]]] = []
    lines: list[list[RoundResult]] = []
    for hop, index in stores.campaigns.campaign_cycles(session.campaign_id):
        if not (rounds := closed_rounds(stores, hop, scorer, moment=None)):
            continue
        # The cycle's OWN closes: a fork's lifted rounds are its parent's simulations.
        own = frozenset(scan_standing_rounds([LedgerSpan(stores.campaigns.ledger(hop))]).rounds)
        edges.append((hop.cycle_id, index, own))
        lines.append(rounds)
    cycles: list[SpineCycle] = []
    for (cid, index, own), series in zip(edges, difficulty.level_series(lines), strict=True):
        cycles.append(
            SpineCycle(
                cycle_id=cid,
                parent_cycle_id=index.parent_cycle_id,
                cut_round=index.cut_round,
                theta_by_round={
                    rnd: None if (level := series.level(rnd)) is None else level.theta
                    for rnd in series.rounds
                    if rnd in own
                },
            )
        )
    return cycles


def _mask_eligible(arm: ArmState, sheet: CellSheet) -> bool:
    """``is_electable``, never ``is_leader_eligible``, which lets a collapsed arm top a round that refused it."""
    return (
        is_electable(arm, sheet.cells)
        and not (isinstance(arm, Cut) and arm.kind == "invalid")
        and not arm.validation_failures
    )


def _masked(sheet: CellSheet, samples: frozenset[str]) -> CellSheet:
    return sheet.where(lambda cell: cell.key in samples)


def _arm_rows(
    closed: RoundResult, samples: frozenset[str] | None
) -> list[tuple[ArmKey, ArmState, CellSheet]]:
    out: list[tuple[ArmKey, ArmState, CellSheet]] = []
    for key, arm in closed.panel.items():
        sheet = closed.rows(key)
        out.append((key, arm, sheet if samples is None else _masked(sheet, samples)))
    return out


def _parent(
    closed: RoundResult,
    samples: frozenset[str] | None,
    carried: CellSheet | None,
) -> CellSheet | None:
    """Under a sample-set mask a carried full-set bar flips rounds on the mask's asymmetry alone."""
    if samples is None or carried is None:
        return carried
    references = [closed.rows(key) for key in closed.arms if key not in closed.panel]
    if len(references) != 1:
        return None
    return _masked(references[0], samples) or None


def _mask_candidate(
    key: ArmKey, arm: ArmState, sheet: CellSheet, scorer: Scorer, crowned: str
) -> MaskCandidate:
    return MaskCandidate(
        key=key,
        reading=read_rows(sheet, scorer),
        is_selected=key == crowned,
        is_eligible=_mask_eligible(arm, sheet),
        abort=_abort_contributor(arm),
    )


def _abort_contributor(arm: ArmState) -> str | None:
    """Never inferred from the cut kind, which cannot tell a collapse from an ε cut."""
    if (elimination := arm_elimination(arm)) is None:
        return None
    gate = elimination.context.get("gate")
    return None if gate is None else str(gate)


def load_mask_record(
    stores: Stores,
    campaign_id: str,
    samples: frozenset[int] | None = None,
    *,
    lens: Lens | None,
    with_replay: bool = False,
) -> MaskRecord:
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise NotFoundError(f"Campaign '{campaign_id}' not found")

    # Each cycle's WHOLE line off its chain: a lifted round is crowned where its parent ran it.
    files: dict[str, dict[int, RoundResult]] = {}
    indexes: dict[str, CycleIndex] = {}
    lines: dict[str, StandingRounds] = {}
    for hop, index in stores.campaigns.campaign_cycles(campaign_id):
        cid = hop.cycle_id
        indexes[cid] = index
        lines[cid] = scan_standing_rounds(
            ledger_chain(CycleDir(cycle_dir_for(stores.base_dir, hop)), moment=None)
        )
        own = scorer_of(resolve_campaign_config(stores, campaign, hop), verifier_graded=False)
        files[cid] = {
            closed.round: closed for closed in closed_rounds(stores, hop, own, moment=None)
        }

    formula = lens.body if lens is not None and lens.kind == "score" else None
    if lens is not None and lens.kind == "dials":
        root = next((cid for cid in files if indexes[cid].parent_cycle_id not in files), None)
        origin_file = files[root].get(0) if root is not None else None
        origin_rows = [
            cell for sheet in (origin_file.cells if origin_file else {}).values() for cell in sheet
        ]
        formula = realize_dials(
            parse_dials(lens.body),
            _dial_anchors(resolve_campaign_config(stores, campaign, None), origin_rows),
        )
    overrides = lens_overrides(formula)

    cycles: list[MaskCycle] = []
    for cid, index in indexes.items():
        # ROWS, never a reading: a fork grades its line's winners under the formula in effect HERE.
        carried: CellSheet | None = None
        crowns = lines[cid].crowns
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cid)
        scorer = scorer_of(
            apply_config_overrides(resolve_campaign_config(stores, campaign, hop), overrides),
            verifier_graded=False,
        )
        # The mask names slots in the bank the cycle drew; its rows pair on keys.
        keys = (
            None
            if samples is None
            else frozenset(
                k for k, slot in cycle_bank(stores, hop).slot_of.items() if slot in samples
            )
        )
        rounds: list[MaskRound] = []
        for rn in sorted(files[cid]):
            round_file = files[cid][rn]
            crowned = crowns.get(rn, "")
            candidates = [
                _mask_candidate(key, arm, sheet, scorer, crowned)
                for key, arm, sheet in _arm_rows(round_file, keys)
            ]
            parent_rows = _parent(round_file, keys, carried)
            rounds.append(
                MaskRound(
                    cycle_id=cid,
                    round=rn,
                    candidates=candidates,
                    parent=None if parent_rows is None else read_rows(parent_rows, scorer),
                    round_data=round_file if with_replay else None,
                    decisions=lines[cid].decisions.get(rn, []) if with_replay else [],
                )
            )
            won = round_file.rows(ArmKey(crowned)) if crowned else None
            if won:
                carried = won
        cycles.append(
            MaskCycle(
                cycle_id=cid,
                parent_cycle_id=index.parent_cycle_id,
                branch_round=_branch_round(index),
                rounds=rounds,
            )
        )
    return MaskRecord(cycles=cycles, criterion=formula)
