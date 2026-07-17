"""Physical-file ledger scans — the highest closed round, the cycle seed, the candidates.

Both read through ``iter_jsonl``, the declared read-model primitive: corruption-
tolerant (a torn trailing line degrades to "not there") but NOT failure-tolerant.
Never swallow an ``OSError`` here — "unreadable" would return what "nothing on the
ledger" returns, and a seeded cycle would silently become an unseeded one.

These scan the PHYSICAL file, deliberately. ``CycleEventLog.iter`` replays a fork's
inherited parent prefix first; a fork's own seed and its own closed rounds are the
appends in its own file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.run_records import CandidateMintedRecord, CycleSeed, LedgerCandidate
from promptpotter.infrastructure.store.read_model import iter_jsonl


def scan_ledger_max_round_complete(ledger_path: Path) -> int:
    """Highest round with a closing PhaseRecord in ``ledger_path``; ``-1`` if none closed.

    One closing event: ``(phase="round", event="complete", round=N)``. Round 0 closes through
    it too — ``emit_origin_round`` sends the origin down the same ``close_round`` seam every L1
    round uses.
    """
    max_complete = -1
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"phase"})):
        if rec.get("record_type") != "phase":
            continue
        if rec.get("phase") == "round" and rec.get("event") == "complete":
            rnd = rec.get("round")
            if isinstance(rnd, int) and rnd > max_complete:
                max_complete = rnd
    return max_complete


def scan_ledger_cycle_seed(ledger_path: Path) -> CycleSeed | None:
    """The cycle's own seed — the last ``CycleSeedRecord``'s ``seed`` in ``ledger_path``,
    or ``None`` when the cycle carries none (sweep / diag).

    The seed is written once at mint, but we take the last match so a re-seed wins.
    """
    found: CycleSeed | None = None
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"cycle_seed"})):
        if rec.get("record_type") != "cycle_seed":
            continue
        seed_data = rec.get("seed")
        if isinstance(seed_data, dict):
            try:
                found = CycleSeed.model_validate(seed_data)
            except ValidationError:
                continue
    return found


def scan_ledger_candidates(ledger_path: Path) -> list[LedgerCandidate]:
    """The cycle's candidate tier, folded out of ``ledger_path`` in ``(round, idx)`` order.

    Independent of round CLOSE, which is the whole point: a cycle whose producer died
    mid-round still names the candidates it minted, and at L4 the inner campaigns they
    spawned still have a parent to hang off.

    A fold over TWO events, because identity and measurement are two facts arriving at two
    times: ``CandidateMintedRecord`` names the candidate, ``candidate_scored`` gives it a
    number. Either alone yields a candidate — a minted one not yet scored is `minted`, and
    a cycle that pre-dates the mint record is still named by its score. Re-runs of the same
    ``(round, idx)`` overwrite, so a rewind's re-mint wins, as in ``scan_ledger_cycle_seed``.

    **Election and θ are deliberately absent, because the ledger does not have them.** The
    ``ROUND_WINNER`` decision goes to a plain list that lands in the round file, not to the
    ledger (``record_decision``'s sink is polymorphic — only some call sites pass the
    ``CycleEventLog``), and θ is stamped onto the candidate at election time, *after* this
    snapshot was emitted. Both are round-CLOSE facts and reach a reader through
    ``dashboard.json::rounds[]``. Do not fold them here from a lookalike record.
    """
    found: dict[tuple[int, int], dict[str, object]] = {}

    def _merge(key: tuple[int, int], **fields: object) -> None:
        acc = found.setdefault(key, {"round": key[0], "idx": key[1]})
        acc.update({k: v for k, v in fields.items() if v is not None})

    for rec in iter_jsonl(
        ledger_path, record_types=frozenset({"candidate_minted", "candidate_scored"})
    ):
        kind = rec.get("record_type")
        if kind == "candidate_minted":
            try:
                minted = CandidateMintedRecord.model_validate(rec)
            except ValidationError:
                continue
            _merge(
                (minted.round, minted.idx),
                candidate_id=minted.candidate_id,
                parent_id=minted.parent_id,
                label=minted.label,
                changes_description=minted.changes_description,
                source=minted.source,
            )
        elif kind == "snapshot" and rec.get("event") == "candidate_scored":
            rnd, idx = rec.get("round"), rec.get("candidate_idx")
            scores = (rec.get("payload") or {}).get("scores")
            if not isinstance(rnd, int) or not isinstance(idx, int):
                continue
            if not isinstance(scores, dict):
                continue
            # `(round, idx)` is the join, NOT the id. The origin deposits its aggregate
            # through this same event carrying no id and no label — anonymous as identity,
            # but it is still round 0's score, and the mint record above supplies the name.
            # `scores` IS a `ScoredCandidate.model_dump()` (the origin's aggregate is the one
            # leaner sender) — so the evaluator namespace, the CI and the sample counts are
            # already here, and no reader needs a second witness to ask for them.
            _merge(
                (rnd, idx),
                candidate_id=scores.get("candidate_id"),
                label=scores.get("label"),
                changes_description=scores.get("changes_description"),
                accuracy=scores.get("accuracy"),
                composite_fitness=scores.get("composite_fitness"),
                evaluators=scores.get("evaluators"),
                composite_ci_lo=scores.get("composite_ci_lo"),
                composite_ci_hi=scores.get("composite_ci_hi"),
                scored_samples=scores.get("scored_samples"),
                expected_samples=scores.get("expected_samples"),
                state="measured",
            )

    # An `id` + a `label` are what make a candidate a NODE. A fold that saw neither event
    # in full (a torn line, a `candidate_started` with no id) yields nothing rather than a
    # nameless row — an absent node is honest, a nameless one is not.
    out: list[LedgerCandidate] = []
    for key in sorted(found):
        try:
            out.append(LedgerCandidate.model_validate(found[key]))
        except ValidationError:
            continue
    return out


__all__ = [
    "scan_ledger_candidates",
    "scan_ledger_cycle_seed",
    "scan_ledger_max_round_complete",
]
