"""Load the realized record from disk — pure read, never re-runs. Round ``N``'s parent is ``N-1``'s winner, so a
fork's first round reaches into the parent CYCLE; a genuinely absent parent makes no claim rather than a fake one.

Every arm is read the way the loop reads it: its round-document rows graded by ``rescore_results``
under a ``CellScorer``, then folded by ``fold_cells``. A ``score:F`` lens is the cycle's own scoring
block with ``per_cell`` replaced — the ``ConfigOverrides`` a fork applying it runs under — so a
masked reading is what a fresh run under ``F`` reports, not a formula over round aggregates."""

from __future__ import annotations

from typing import Any, cast

from promptpotter.application.campaign_config import apply_config_overrides
from promptpotter.application.datasets.authored import config_cell_scorer
from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskReading,
    MaskRecord,
    MaskRound,
    SpineCycle,
)
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.application.scoring.formula import rescore_results
from promptpotter.application.scoring.metrics import fold_cells
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import (
    ArmOutcome,
    ScoredCandidate,
    is_electable,
    measured_cells,
    merge_known_outcomes,
)
from promptpotter.domain.run_records import ConfigOverrides
from promptpotter.domain.scoring import CellScorer, QueryMeasurement
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_decisions,
    scan_ledger_elections,
    scan_ledger_round_closes,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError


def lens_overrides(formula: str | None) -> ConfigOverrides:
    """What a ``score:<formula>`` lens IS: the fork override applying it. ``None`` reads each cycle
    under its own scorer — a sample-set mask alone, or an ``abort:`` lens."""
    return ConfigOverrides(scoring={"per_cell": formula}) if formula else ConfigOverrides()


def read_rows(rows: list[dict[str, Any]], scorer: CellScorer) -> MaskReading | None:
    """*rows* graded and folded under *scorer*, on copies — the round document keeps its own grades.
    ``None`` where no row carries a verdict, which is no reading rather than a 0.0 floor."""
    graded = rescore_results([dict(r) for r in rows], scorer)
    cells = measured_cells(graded)
    if not cells:
        return None
    folded = fold_cells(cast("list[QueryMeasurement]", graded))
    return MaskReading(
        composite_fitness=float(folded["composite_fitness"]),
        accuracy=folded["accuracy"],
        n_scored=len(cells),
    )


def _cycle_edge(index: dict[str, Any]) -> tuple[str | None, int | None]:
    """``(parent_cycle_id, fork_from_round)`` off a cycle manifest — read by both loaders below,
    so it is spelled once rather than in whichever of them was written first."""
    fork = index.get("fork")
    from_round = fork.get("from_round") if isinstance(fork, dict) else None
    return (
        index.get("parent_cycle_id") or None,
        from_round if isinstance(from_round, int) else None,
    )


def load_lineage_spine(stores: Stores, campaign_id: str) -> list[SpineCycle]:
    """The campaign's rounds and edges, folded from each cycle's LEDGER — no round file opened,
    because a layer waits on this to fork and the four scalars per round are all it needs."""
    cycles: list[SpineCycle] = []
    for entry in stores.campaigns.enumerate_cycles():
        if entry["campaign_id"] != campaign_id:
            continue
        cid = entry["cycle_id"]
        cdir = cycle_dir_for(stores.base_dir, CycleHop(campaign_id=campaign_id, cycle_id=cid))
        index = read_json_tolerant(CycleLayout(cdir).manifest)
        if not isinstance(index, dict):
            continue
        parent, from_round = _cycle_edge(index)
        closes = scan_ledger_round_closes(CycleLayout(cdir).ledger)
        cycles.append(
            SpineCycle(
                cycle_id=cid,
                parent_cycle_id=parent,
                fork_from_round=from_round,
                # A round that never CLOSED contributes no ability, and inventing 0.0 for it
                # would hand UCB a real-looking datum for a simulation that never finished.
                theta_by_round={
                    rnd: close.ability.theta
                    for rnd, close in closes.items()
                    if close.ability is not None
                },
            )
        )
    return cycles


def _mask_eligible(sc: ScoredCandidate, rows: list[dict[str, Any]]) -> bool:
    """The election's OWN admission rule, plus a guard against candidates whose composite was
    force-zeroed POST-formula: a re-grade of their rows cannot reproduce that.

    ``is_electable``, not ``is_leader_eligible`` — its docstring names this exact caller and
    says why: the weaker test "lets a collapsed arm top a round that refused to crown it", so a
    lens fed the realizing criterion could report a divergence the run would never have made."""
    return (
        is_electable(sc, rows)
        and sc.outcome is not ArmOutcome.INVALID
        and not sc.validation_failures
    )


def _arm_rows(
    round_file: dict[str, Any], samples: frozenset[int] | None
) -> list[tuple[ScoredCandidate, list[dict[str, Any]]]]:
    """Each arm beside its rows on disk, cut to the sample-set mask. `.get`, not a subscript: this
    walk is tolerant by contract, and the subscript raised on a document missing the key."""
    all_rows = round_file.get("all_candidate_results") or {}
    out: list[tuple[ScoredCandidate, list[dict[str, Any]]]] = []
    for cs in round_file.get("candidate_scores", []):
        if not isinstance(cs, dict) or not cs.get("candidate_id"):
            continue
        sc = ScoredCandidate.model_validate(cs)
        rows = list(all_rows.get(sc.candidate_id) or [])
        if samples is not None:
            rows = [r for r in rows if r.get("sample_id") in samples]
        out.append((sc, rows))
    return out


def _parent(
    round_file: dict[str, Any],
    samples: frozenset[int] | None,
    carried: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """The rows of the bar this round's arms were held to, cut to the same mask they were.

    Unmasked it is *carried* — round ``N-1``'s elected winner's own rows. Under a SAMPLE-SET mask
    every arm is read on the selected cells, so a carried full-set bar would flip rounds on the
    mask's asymmetry alone; the parent's own rows on THIS round's subset (``reference_results``)
    stand instead.

    A round with no such rows cannot answer on a subset at all — round 0 has no parent — and nor
    can one whose arms were read against several individuals (``lift_reference: parents``), which
    has no single bar. ``None`` then, which ``masked_election`` reads as ``decidable=False``."""
    if samples is None or carried is None:
        return carried
    references = list(round_file["reference_results"].values())
    if len(references) != 1:
        return None
    return [r for r in references[0] if r.get("sample_id") in samples] or None


def _mask_candidate(
    sc: ScoredCandidate,
    rows: list[dict[str, Any]],
    scorer: CellScorer,
    winner_label: str,
) -> MaskCandidate:
    """The crown comes off the ledger's ELECTION record, joined on ``label`` — never re-derived
    from the document's own ``scoreboard[]`` on ``candidate_id``, a key the tree refuses because a
    resume re-mints it. A held round crowns nobody: its ``winner_label`` is empty."""
    return MaskCandidate(
        candidate_id=sc.candidate_id,
        reading=read_rows(rows, scorer),
        is_selected=bool(winner_label) and sc.label == winner_label,
        is_eligible=_mask_eligible(sc, rows),
        abort=_abort_contributor(sc),
    )


def _abort_contributor(sc: ScoredCandidate) -> str | None:
    """Which PoBB gate stopped this candidate early; ``None`` if it ran to term. Read off the
    gate PoBB named, never inferred from the outcome, which cannot tell a collapse from an ε cut."""
    return sc.elimination_context.get("gate")


def load_mask_record(
    stores: Stores,
    campaign_id: str,
    samples: frozenset[int] | None = None,
    *,
    lens: str | None,
    with_replay: bool = False,
) -> MaskRecord:
    """Every cycle, each arm graded under *lens* (a ``per_cell`` formula) or its cycle's own scorer.
    *with_replay* reads through the TYPED loader, which raises on a document the models reject."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        raise NotFoundError(f"Campaign '{campaign_id}' not found")
    overrides = lens_overrides(lens)
    entries = [e for e in stores.campaigns.enumerate_cycles() if e["campaign_id"] == campaign_id]

    # Pass 1: each cycle's round files + tree edges + the crowns its LEDGER recorded. The rows
    # come from the document because nothing else holds them; the election does not.
    files: dict[str, dict[int, dict[str, Any]]] = {}
    edges: dict[str, tuple[str | None, int | None]] = {}
    crowns: dict[str, dict[int, str]] = {}
    decisions: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for e in entries:
        cid = e["cycle_id"]
        cdir = cycle_dir_for(stores.base_dir, CycleHop(campaign_id=campaign_id, cycle_id=cid))
        index = read_json_tolerant(CycleLayout(cdir).manifest)
        if not isinstance(index, dict):
            continue
        edges[cid] = _cycle_edge(index)
        decisions[cid] = scan_ledger_decisions(CycleLayout(cdir).ledger) if with_replay else {}
        crowns[cid] = {
            rnd: next(iter(election.selected_labels), "")
            for rnd, election in scan_ledger_elections(CycleLayout(cdir).ledger).items()
        }
        by_round: dict[int, dict[str, Any]] = {}
        for r in index.get("rounds") or []:
            rn = r.get("round") if isinstance(r, dict) else None
            if not isinstance(rn, int):
                continue
            rf = read_json_tolerant(CycleLayout(cdir).round_file(rn))
            if isinstance(rf, dict):
                by_round[rn] = rf
        files[cid] = by_round

    # Order parents before children so a fork inherits its branch-point winner.
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

    # Pass 2: thread the carried-forward winner's ROWS. A round's parent is the winner at the end
    # of the prior round; when it holds (no candidate winner) it carries unchanged. Rows rather
    # than a reading, so a fork grades its branch-point winner under the formula in effect HERE.
    winner_at: dict[tuple[str, int], list[dict[str, Any]] | None] = {}
    # The known-outcomes pool threads the SAME way the parent does — inherited across the
    # fork edge from the branch point, then folded round by round. Each round is handed the
    # pool as it stood BEFORE it ran, which is what the live election saw (`Cycle.absorb_round`
    # merges the round's own rows only after it finished).
    pool_at: dict[tuple[str, int], list[dict[str, Any]]] = {}
    cycles: list[MaskCycle] = []
    for cid in order:
        parent, from_round = edges.get(cid, (None, None))
        carried: list[dict[str, Any]] | None = None
        pool: list[dict[str, Any]] = []
        if parent is not None and from_round is not None:
            carried = winner_at.get((parent, from_round))
            pool = list(pool_at.get((parent, from_round), pool))
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cid)
        scorer, _ = config_cell_scorer(
            apply_config_overrides(resolve_campaign_config(stores, campaign, hop), overrides)
        )
        rounds: list[MaskRound] = []
        for rn in sorted(files[cid]):
            round_file = files[cid][rn]
            winner_label = crowns.get(cid, {}).get(rn, "")
            arms = _arm_rows(round_file, samples)
            candidates = [_mask_candidate(sc, rows, scorer, winner_label) for sc, rows in arms]
            parent_rows = _parent(round_file, samples, carried)
            rounds.append(
                MaskRound(
                    cycle_id=cid,
                    round=rn,
                    candidates=candidates,
                    parent=None if parent_rows is None else read_rows(parent_rows, scorer),
                    # Through the store's typed read, not a second ``model_validate`` here:
                    # that one is the sole typed read of a round document, and it RAISES on a
                    # file the current models cannot parse. Correct for a replay, which
                    # re-derives from the document — while the summary fields above stay
                    # tolerant, so a scoring or abort lens still serves a drifted cycle.
                    round_data=stores.campaigns.load_round_file(hop, rn) if with_replay else None,
                    known_outcomes=pool if with_replay else [],
                    decisions=decisions.get(cid, {}).get(rn, []),
                )
            )
            crowned = next((c for c in candidates if c.is_selected), None)
            won = (round_file.get("all_candidate_results") or {}).get(
                crowned.candidate_id if crowned else ""
            )
            if won:
                carried = list(won)
            winner_at[(cid, rn)] = carried
            if with_replay:
                pool = merge_known_outcomes(pool, list(round_file.get("results") or []))
            pool_at[(cid, rn)] = pool
        cycles.append(
            MaskCycle(
                cycle_id=cid, parent_cycle_id=parent, fork_from_round=from_round, rounds=rounds
            )
        )
    return MaskRecord(cycles=cycles)


__all__ = [
    "lens_overrides",
    "load_lineage_spine",
    "load_mask_record",
    "read_rows",
]
