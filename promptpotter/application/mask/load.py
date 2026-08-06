"""Load the realized **record** from disk — the read-time service the API calls.

Reads each cycle's public round files (the full ``RoundResult`` dump:
``candidate_scores`` with the materialized evaluator namespace + eligibility facts,
``scoreboard`` for ``is_winner``, top-level ``evaluators``/``accuracy`` for the
carried-forward winner) and builds a :class:`MaskRecord`. Never re-runs, never
writes — pure read. The fold + verdict then operate on the returned record.

The anchor (origin / carried-forward winner) of round ``N`` is round ``N-1``'s
winner; for a fork's first round that ancestor lives in the *parent* cycle, so the
loader reaches one file up to the branch-point winner. When an anchor is genuinely
absent (interrupted prefix, missing file) the round simply carries no anchor and the
verdict makes no claim — honest divergence, never fabricated.
"""

from __future__ import annotations

from typing import Any

from promptpotter.application.mask.record import (
    MaskCandidate,
    MaskCycle,
    MaskRecord,
    MaskRound,
)
from promptpotter.application.scoring.evaluators import materialize_row_derivable
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import (
    ScoredCandidate,
    is_leader_eligible,
    merge_known_outcomes,
)
from promptpotter.infrastructure.store.io import read_json_tolerant
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.stores import Stores


def _mask_eligible(sc: ScoredCandidate) -> bool:
    """Recorded election eligibility for mask re-selection: the realized
    ``is_leader_eligible`` filter (escalation-abort / degradation) plus a guard
    against structurally-invalid / validation-failed candidates whose realized
    composite was force-zeroed *post-formula* — a formula re-score over their stored
    evaluators cannot reproduce that zeroing, so they are not honest competitors."""
    return is_leader_eligible(sc) and not sc.invalid and not sc.validation_failures


def _candidates(round_file: dict[str, Any], samples: frozenset[int] | None) -> list[MaskCandidate]:
    winners = {
        c["candidate_id"]
        for c in round_file.get("scoreboard", [])
        if isinstance(c, dict) and c.get("is_winner") and c.get("candidate_id")
    }
    # The per-sample rows already on disk. The row-derivable evaluator subset
    # (accuracy, output_compactness, latency_norm, …) is recomputed from these and
    # merged over the stored snapshot — present on every record regardless of when it
    # was written. A sample-set mask filters the rows to the selected subset first, so
    # those same evaluators (accuracy especially) re-score on the subset and a What-If
    # formula reshapes the election there.
    all_rows = round_file["all_candidate_results"]
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
                out.append(_mask_candidate(sc, {}, 0.0, 0, winners))
                continue
        evaluators = dict(sc.evaluators)
        accuracy = sc.accuracy
        # Refresh the row-derivable subset from the rows (full set, or the masked
        # subset). The snapshot supplies the schema/opt_sp-bound names. An empty
        # snapshot = invalid/force-zeroed candidate → leave empty so it stays skipped.
        if evaluators and rows:
            evaluators.update(materialize_row_derivable(rows))
            accuracy = evaluators["accuracy"]
        out.append(_mask_candidate(sc, evaluators, accuracy, len(rows), winners))
    return out


def _mask_candidate(
    sc: ScoredCandidate,
    evaluators: dict[str, float],
    accuracy: float,
    n_scored: int,
    winners: set[str],
) -> MaskCandidate:
    return MaskCandidate(
        candidate_id=sc.candidate_id,
        evaluators=evaluators,
        accuracy=accuracy,
        n_scored=n_scored,
        is_winner=sc.candidate_id in winners,
        is_eligible=_mask_eligible(sc),
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
    """Read every cycle in *campaign_id* into a :class:`MaskRecord` (read-only).

    *samples* (the **sample-set mask**) re-scores each candidate's accuracy over only
    those sample ids — the carried-forward winner threads its subset accuracy too, so
    the election re-runs on the subset and the fold finds where the subset-best
    diverges from the recorded (full-set) winner. ``None`` ⇒ stored full-set values.

    *with_replay* additionally carries each round's own ``RoundResult`` and the pool of
    known per-sample outcomes as it stood before that round ran — what a replay verdict
    re-derives from. Off by default because those are the raw rows: a scoring or abort
    lens reads none of them, and the tree endpoint serves one of those on every request.
    It also reads each round through the store's TYPED loader, which raises on a document
    the current models cannot parse — a replay must re-derive from a document it can read,
    where a lens is happy with the summary fields.
    """
    entries = [e for e in stores.campaigns.enumerate_cycles() if e["campaign_id"] == campaign_id]

    # Pass 1: read each cycle's round files (keyed by round number) + tree edges.
    files: dict[str, dict[int, dict[str, Any]]] = {}
    edges: dict[str, tuple[str | None, int | None]] = {}
    for e in entries:
        cid = e["cycle_id"]
        cdir = cycle_dir_for(stores.base_dir, CycleHop(campaign_id=campaign_id, cycle_id=cid))
        index = read_json_tolerant(CycleLayout(cdir).manifest)
        if not isinstance(index, dict):
            continue
        fork = index.get("fork")
        from_round = fork.get("from_round") if isinstance(fork, dict) else None
        edges[cid] = (
            index.get("parent_cycle_id") or None,
            from_round if isinstance(from_round, int) else None,
        )
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
            candidates = _candidates(round_file, samples)
            theta = round_file.get("cumulative_theta")
            rounds.append(
                MaskRound(
                    cycle_id=cid,
                    round=rn,
                    candidates=candidates,
                    anchor_evaluators=carried[0],
                    anchor_accuracy=carried[1],
                    cumulative_theta=float(theta) if isinstance(theta, int | float) else 0.0,
                    # Through the store's typed read, not a second ``model_validate`` here:
                    # that one is the sole typed read of a round document, and it RAISES on a
                    # file the current models cannot parse. Correct for a replay, which
                    # re-derives from the document — while the summary fields above stay
                    # tolerant, so a scoring or abort lens still serves a drifted cycle.
                    round_data=stores.campaigns.load_round_file(hop, rn) if with_replay else None,
                    known_outcomes=pool if with_replay else [],
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


__all__ = ["load_mask_record"]
