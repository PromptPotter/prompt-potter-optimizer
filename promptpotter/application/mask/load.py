"""Load the realized record from disk — pure read, never re-runs. Round ``N``'s anchor is ``N-1``'s winner, so a
fork's first round reaches into the PARENT; a genuinely absent anchor makes no claim rather than a fake one."""

from __future__ import annotations

from typing import Any

from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskRecord,
    MaskRound,
    SpineCycle,
)
from promptpotter.application.scoring.evaluators import materialize_row_derivable
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import ScoredCandidate, is_electable, merge_known_outcomes
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_ledger_decisions,
    scan_ledger_elections,
    scan_ledger_round_closes,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores


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
    """The campaign's rounds and edges, folded from each cycle's LEDGER — no round file opened.
    It used to take a whole `MaskRecord`: every manifest and every round document of every cycle,
    rebuilt to recover four scalars per round, while a layer waited to fork."""
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
                    rnd: close.cumulative_theta
                    for rnd, close in closes.items()
                    if close.cumulative_theta is not None
                },
            )
        )
    return cycles


def _mask_eligible(sc: ScoredCandidate, rows: list[dict[str, Any]]) -> bool:
    """The election's OWN admission rule, plus a guard against candidates whose composite was
    force-zeroed POST-formula: a re-score over their stored evaluators cannot reproduce that.

    ``is_electable``, not ``is_leader_eligible`` — its docstring names this exact caller and
    says why: the weaker test "lets a collapsed arm top a round that refused to crown it", so a
    lens fed the realizing criterion could report a divergence the run would never have made."""
    return is_electable(sc, rows) and not sc.invalid and not sc.validation_failures


def _candidates(
    round_file: dict[str, Any], samples: frozenset[int] | None, winner_label: str
) -> list[MaskCandidate]:
    # The per-sample rows already on disk. The row-derivable evaluator subset
    # (accuracy, output_compactness, latency_norm, …) is recomputed from these and
    # merged over the stored snapshot — present on every record regardless of when it
    # was written. A sample-set mask filters the rows to the selected subset first, so
    # those same evaluators (accuracy especially) re-score on the subset and a What-If
    # formula reshapes the election there.
    # `.get`, not a subscript: this walk is tolerant by contract, and the subscript raised
    # `KeyError` on a document missing the key.
    all_rows = round_file.get("all_candidate_results") or {}
    out: list[MaskCandidate] = []
    for cs in round_file.get("candidate_scores", []):
        if not isinstance(cs, dict) or not cs.get("candidate_id"):
            continue
        sc = ScoredCandidate.model_validate(cs)
        rows = all_rows.get(sc.candidate_id) or []
        if samples is not None:
            rows = [r for r in rows if r.get("sample_id") in samples]
            if not rows:
                # Never ran any selected sample → unscorable on this set. Empty
                # evaluators make the verdict + winner-threading skip it uniformly,
                # exactly like a candidate with no stored values.
                out.append(_mask_candidate(sc, {}, 0.0, 0, rows, winner_label))
                continue
        evaluators = dict(sc.evaluators)
        accuracy = sc.accuracy
        # Refresh the row-derivable subset from the rows (full set, or the masked
        # subset). The snapshot supplies the schema/opt_sp-bound names. An empty
        # snapshot = invalid/force-zeroed candidate → leave empty so it stays skipped.
        if evaluators and rows:
            evaluators.update(materialize_row_derivable(rows))
            accuracy = evaluators["accuracy"]
        out.append(_mask_candidate(sc, evaluators, accuracy, len(rows), rows, winner_label))
    return out


def _mask_candidate(
    sc: ScoredCandidate,
    evaluators: dict[str, float],
    accuracy: float,
    n_scored: int,
    rows: list[dict[str, Any]],
    winner_label: str,
) -> MaskCandidate:
    """The crown comes off the ledger's ELECTION record, joined on ``label`` — never re-derived
    from the document's own ``scoreboard[]`` on ``candidate_id``, a key the tree refuses because a
    resume re-mints it. A held round crowns nobody: its ``winner_label`` is empty."""
    return MaskCandidate(
        candidate_id=sc.candidate_id,
        evaluators=evaluators,
        accuracy=accuracy,
        n_scored=n_scored,
        is_winner=bool(winner_label) and sc.label == winner_label,
        is_eligible=_mask_eligible(sc, rows),
        abort=_abort_contributor(sc),
    )


def _abort_contributor(sc: ScoredCandidate) -> str | None:
    """Which PoBB contributor cut this candidate early — ``leader_locked``
    discriminates lock-in (B) from ε-elimination (A); ``None`` if it ran to term."""
    if not sc.elimination_stopped or not sc.elimination_context:
        return None
    return "lock_in" if sc.elimination_context.get("leader_locked") else "epsilon"


def load_mask_record(
    stores: Stores,
    campaign_id: str,
    samples: frozenset[int] | None = None,
    *,
    with_replay: bool = False,
) -> MaskRecord:
    """Read every cycle into a ``MaskRecord``. *with_replay* also carries the raw rows and reads through the TYPED
    loader, which raises on a document the current models cannot parse; a lens needs neither."""
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
            rnd: election.winner_label
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

    # Pass 2: thread the carried-forward winner. A round's anchor (origin) is the
    # winner at the end of the prior round; when origin holds (no candidate winner)
    # it carries unchanged. The winner's evaluators live on its candidate_scores
    # row, NOT the (often-empty) top-level round ``evaluators`` field.
    winner_at: dict[tuple[str, int], tuple[dict[str, float], float]] = {}
    # The known-outcomes pool threads the SAME way the anchor does — inherited across the
    # fork edge from the branch point, then folded round by round. Each round is handed the
    # pool as it stood BEFORE it ran, which is what the live election saw (`Cycle.absorb_round`
    # merges the round's own rows only after it finished).
    pool_at: dict[tuple[str, int], list[dict[str, Any]]] = {}
    cycles: list[MaskCycle] = []
    for cid in order:
        parent, from_round = edges.get(cid, (None, None))
        carried: tuple[dict[str, float], float] = ({}, 0.0)
        pool: list[dict[str, Any]] = []
        if parent is not None and from_round is not None:
            carried = winner_at.get((parent, from_round), carried)
            pool = list(pool_at.get((parent, from_round), pool))
        hop = CycleHop(campaign_id=campaign_id, cycle_id=cid)
        rounds: list[MaskRound] = []
        for rn in sorted(files[cid]):
            round_file = files[cid][rn]
            candidates = _candidates(round_file, samples, crowns.get(cid, {}).get(rn, ""))
            rounds.append(
                MaskRound(
                    cycle_id=cid,
                    round=rn,
                    candidates=candidates,
                    anchor_evaluators=carried[0],
                    anchor_accuracy=carried[1],
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
            winner = next((c for c in candidates if c.is_winner and c.evaluators), None)
            if winner is not None:
                carried = (dict(winner.evaluators), winner.accuracy)
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


__all__ = ["load_lineage_spine", "load_mask_record"]
