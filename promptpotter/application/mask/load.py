from __future__ import annotations

from promptpotter.application.campaign_config import CampaignConfig, apply_config_overrides
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
from promptpotter.application.scoring.cells import closed_rounds
from promptpotter.application.scoring.formula import (
    origin_anchors,
    parse_dials,
    realize_dials,
    split_scoring_block,
)
from promptpotter.application.scoring.metrics import fold_cells
from promptpotter.domain.cycle_listing import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.results import (
    ArmOutcome,
    RoundResult,
    ScoredCandidate,
    is_electable,
    measured_cells,
    merge_known_outcomes,
)
from promptpotter.domain.run_records import ConfigOverrides, ResumeCheckpointRecord
from promptpotter.domain.scoring import (
    NO_CELLS,
    CellSheet,
    GradedCell,
    Scorer,
    anchored_criterion_dials,
)
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_standing_rounds
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.read_model import LedgerSpan
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.judges.registry import judge_instrument
from promptpotter.shared.errors import NotFoundError


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


def _cycle_edge(index: CycleIndex) -> tuple[str | None, int | None]:
    return index.parent_cycle_id, None if index.fork is None else index.fork.from_round


def load_lineage_spine(stores: Stores, campaign_id: str) -> list[SpineCycle]:
    """Folded from each cycle's LEDGER, no round file opened: a layer waits on this to fork."""
    cycles: list[SpineCycle] = []
    for entry in stores.campaigns.enumerate_cycles():
        if entry.campaign_id != campaign_id:
            continue
        cid = entry.cycle_id
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cid)
        cdir = cycle_dir_for(stores.base_dir, hop)
        index = stores.campaigns.load(hop)
        if index is None:
            continue
        parent, from_round = _cycle_edge(index)
        # The cycle's OWN closes: a fork's lifted rounds are its parent's simulations.
        own = scan_standing_rounds([LedgerSpan(CycleLayout(cdir).ledger)])
        cycles.append(
            SpineCycle(
                cycle_id=cid,
                parent_cycle_id=parent,
                fork_from_round=from_round,
                # A round that never closed is absent: a 0.0 would hand UCB a real-looking datum.
                theta_by_round={
                    rnd: held.close.ability.theta
                    for rnd, held in own.rounds.items()
                    if held.close.ability is not None
                },
            )
        )
    return cycles


def _mask_eligible(sc: ScoredCandidate, sheet: CellSheet) -> bool:
    """``is_electable``, never ``is_leader_eligible``, which lets a collapsed arm top a round that refused it."""
    return (
        is_electable(sc, sheet.cells)
        and sc.outcome is not ArmOutcome.INVALID
        and not sc.validation_failures
    )


def _masked(sheet: CellSheet, samples: frozenset[int]) -> CellSheet:
    return sheet.where(lambda cell: cell.sample_id in samples)


def _arm_rows(
    closed: RoundResult, samples: frozenset[int] | None
) -> list[tuple[ScoredCandidate, CellSheet]]:
    out: list[tuple[ScoredCandidate, CellSheet]] = []
    for sc in closed.candidate_scores:
        sheet = closed.all_candidate_results.get(sc.candidate_id, NO_CELLS)
        out.append((sc, sheet if samples is None else _masked(sheet, samples)))
    return out


def _parent(
    closed: RoundResult,
    samples: frozenset[int] | None,
    carried: CellSheet | None,
) -> CellSheet | None:
    """Under a sample-set mask a carried full-set bar flips rounds on the mask's asymmetry alone."""
    if samples is None or carried is None:
        return carried
    references = list(closed.reference_results.values())
    if len(references) != 1:
        return None
    return _masked(references[0], samples) or None


def _mask_candidate(
    sc: ScoredCandidate,
    sheet: CellSheet,
    scorer: Scorer,
    winner_label: str,
) -> MaskCandidate:
    """The crown joins on ``label``: ``candidate_id`` names an individual, not the arm a round crowned."""
    return MaskCandidate(
        candidate_id=sc.candidate_id,
        reading=read_rows(sheet, scorer),
        is_selected=bool(winner_label) and sc.label == winner_label,
        is_eligible=_mask_eligible(sc, sheet),
        abort=_abort_contributor(sc),
    )


def _abort_contributor(sc: ScoredCandidate) -> str | None:
    """Never inferred from the outcome, which cannot tell a collapse from an ε cut."""
    return sc.elimination_context.get("gate")


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
    entries = [e for e in stores.campaigns.enumerate_cycles() if e.campaign_id == campaign_id]

    files: dict[str, dict[int, RoundResult]] = {}
    edges: dict[str, tuple[str | None, int | None]] = {}
    crowns: dict[str, dict[int, str]] = {}
    decisions: dict[str, dict[int, list[ResumeCheckpointRecord]]] = {}
    for e in entries:
        cid = e.cycle_id
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cid)
        cdir = cycle_dir_for(stores.base_dir, hop)
        index = stores.campaigns.load(hop)
        if index is None:
            continue
        edges[cid] = _cycle_edge(index)
        decisions[cid] = (
            scan_standing_rounds(ledger_chain(CycleDir(cdir))).decisions if with_replay else {}
        )
        # Off the cycle's OWN elections; a round a fork lifted is crowned where its parent ran it.
        crowns[cid] = {
            rnd: next(iter(election.selected_labels), "")
            for rnd, election in scan_standing_rounds(
                [LedgerSpan(CycleLayout(cdir).ledger)]
            ).elections.items()
        }
        own = scorer_of(resolve_campaign_config(stores, campaign, hop), verifier_graded=False)
        files[cid] = {closed.round: closed for closed in closed_rounds(stores, hop, own)}

    # Parents before children, so a fork inherits its branch-point winner.
    order: list[str] = []
    seen: set[str] = set()

    def _order(cid: str) -> None:
        if cid in seen:
            return
        parent = edges.get(cid, (None, None))[0]
        if parent in files and parent not in seen:
            _order(parent)
        seen.add(cid)
        order.append(cid)

    for cid in files:
        _order(cid)

    formula = lens.body if lens is not None and lens.kind == "score" else None
    if lens is not None and lens.kind == "dials":
        root = next((cid for cid in order if edges[cid][0] not in files), None)
        origin_file = files[root].get(0) if root is not None else None
        origin_rows = [
            cell
            for sheet in (origin_file.all_candidate_results if origin_file else {}).values()
            for cell in sheet
        ]
        formula = realize_dials(
            parse_dials(lens.body),
            _dial_anchors(resolve_campaign_config(stores, campaign, None), origin_rows),
        )
    overrides = lens_overrides(formula)

    # ROWS, never a reading: a fork grades its branch-point winner under the formula in effect HERE.
    winner_at: dict[tuple[str, int], CellSheet | None] = {}
    # Each round is handed the pool as it stood BEFORE it ran, which is what the live election reads.
    pool_at: dict[tuple[str, int], list[GradedCell]] = {}
    cycles: list[MaskCycle] = []
    for cid in order:
        parent, from_round = edges.get(cid, (None, None))
        carried: CellSheet | None = None
        pool: list[GradedCell] = []
        if parent is not None and from_round is not None:
            carried = winner_at.get((parent, from_round))
            pool = list(pool_at.get((parent, from_round), pool))
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cid)
        scorer = scorer_of(
            apply_config_overrides(resolve_campaign_config(stores, campaign, hop), overrides),
            verifier_graded=False,
        )
        rounds: list[MaskRound] = []
        for rn in sorted(files[cid]):
            round_file = files[cid][rn]
            winner_label = crowns.get(cid, {}).get(rn, "")
            arms = _arm_rows(round_file, samples)
            candidates = [_mask_candidate(sc, sheet, scorer, winner_label) for sc, sheet in arms]
            parent_rows = _parent(round_file, samples, carried)
            rounds.append(
                MaskRound(
                    cycle_id=cid,
                    round=rn,
                    candidates=candidates,
                    parent=None if parent_rows is None else read_rows(parent_rows, scorer),
                    round_data=round_file if with_replay else None,
                    known_outcomes=pool if with_replay else [],
                    decisions=decisions.get(cid, {}).get(rn, []),
                )
            )
            crowned = next((c for c in candidates if c.is_selected), None)
            won = round_file.all_candidate_results.get(crowned.candidate_id if crowned else "")
            if won:
                carried = won
            winner_at[(cid, rn)] = carried
            if with_replay:
                pool = merge_known_outcomes(pool, round_file.results)
            pool_at[(cid, rn)] = pool
        cycles.append(
            MaskCycle(
                cycle_id=cid, parent_cycle_id=parent, fork_from_round=from_round, rounds=rounds
            )
        )
    return MaskRecord(cycles=cycles, criterion=formula)


__all__ = [
    "lens_overrides",
    "load_lineage_spine",
    "load_mask_record",
    "read_rows",
]
