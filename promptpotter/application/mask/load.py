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
from promptpotter.application.optimization.l1.score.winner import is_leader_eligible
from promptpotter.application.scoring.metrics import accuracy_over_samples
from promptpotter.domain.results import ScoredCandidate
from promptpotter.infrastructure.store import cycle_dir_for
from promptpotter.infrastructure.store.base import read_json_optional
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
    # Sample-set mask: re-score accuracy over only the selected samples, read from
    # the per-sample rows already on disk. The full evaluator namespace stays at its
    # stored (full-set) values — only `accuracy`, the sample-dependent term, moves —
    # so a What-If formula reweighting accuracy reshapes the election on the subset.
    per_sample = round_file.get("all_candidate_results") or {} if samples is not None else {}
    out: list[MaskCandidate] = []
    for cs in round_file.get("candidate_scores", []):
        if not isinstance(cs, dict) or not cs.get("candidate_id"):
            continue
        sc = ScoredCandidate.model_validate(cs)
        evaluators = dict(sc.evaluators)
        accuracy = sc.accuracy
        if samples is not None:
            acc, n_measured = accuracy_over_samples(per_sample.get(sc.candidate_id) or [], samples)
            if n_measured == 0:
                # Never ran any selected sample → unscorable on this set. Empty
                # evaluators make the verdict + winner-threading skip it uniformly,
                # exactly like a candidate with no stored values.
                evaluators = {}
                accuracy = 0.0
            else:
                accuracy = acc
                if evaluators:
                    evaluators["accuracy"] = acc
        out.append(
            MaskCandidate(
                candidate_id=sc.candidate_id,
                evaluators=evaluators,
                accuracy=accuracy,
                is_winner=sc.candidate_id in winners,
                is_eligible=_mask_eligible(sc),
                abort=_abort_contributor(sc),
            )
        )
    return out


def _abort_contributor(sc: ScoredCandidate) -> str | None:
    """Which PoBB contributor cut this candidate early — ``leader_locked``
    discriminates lock-in (B) from ε-elimination (A); ``None`` if it ran to term."""
    if not sc.elimination_stopped or not sc.elimination_context:
        return None
    return "lock_in" if sc.elimination_context.get("leader_locked") else "epsilon"


def load_mask_record(
    store: Stores, campaign_id: str, samples: frozenset[int] | None = None
) -> MaskRecord:
    """Read every cycle in *campaign_id* into a :class:`MaskRecord` (read-only).

    *samples* (the **sample-set mask**) re-scores each candidate's accuracy over only
    those sample ids — the carried-forward winner threads its subset accuracy too, so
    the election re-runs on the subset and the fold finds where the subset-best
    diverges from the recorded (full-set) winner. ``None`` ⇒ stored full-set values.
    """
    entries = [e for e in store.campaigns.enumerate_cycles() if e["campaign_id"] == campaign_id]

    # Pass 1: read each cycle's round files (keyed by round number) + tree edges.
    files: dict[str, dict[int, dict[str, Any]]] = {}
    edges: dict[str, tuple[str | None, int | None]] = {}
    for e in entries:
        cid = e["cycle_id"]
        cdir = cycle_dir_for(store.base_dir, campaign_id, cid)
        index = read_json_optional(cdir / "index.json")
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
            rf = read_json_optional(cdir / "rounds" / f"round_{rn:04d}.json")
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
    cycles: list[MaskCycle] = []
    for cid in order:
        parent, from_round = edges.get(cid, (None, None))
        carried: tuple[dict[str, float], float] = (
            winner_at.get((parent, from_round), ({}, 0.0))
            if parent is not None and from_round is not None
            else ({}, 0.0)
        )
        rounds: list[MaskRound] = []
        for rn in sorted(files[cid]):
            candidates = _candidates(files[cid][rn], samples)
            rounds.append(
                MaskRound(
                    cycle_id=cid,
                    round=rn,
                    candidates=candidates,
                    anchor_evaluators=carried[0],
                    anchor_accuracy=carried[1],
                )
            )
            winner = next((c for c in candidates if c.is_winner and c.evaluators), None)
            if winner is not None:
                carried = (dict(winner.evaluators), winner.accuracy)
            winner_at[(cid, rn)] = carried
        cycles.append(
            MaskCycle(
                cycle_id=cid, parent_cycle_id=parent, fork_from_round=from_round, rounds=rounds
            )
        )
    return MaskRecord(cycles=cycles)


__all__ = ["load_mask_record"]
