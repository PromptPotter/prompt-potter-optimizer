"""Physical-file ledger scans, deliberately physical: ``CycleEventLog.iter`` would replay a fork's
inherited prefix. Never swallow an ``OSError`` — "unreadable" would answer as "nothing on it"."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from promptpotter.domain.ruler import AbilityReading, DeltaRuler
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CycleSeed,
    ElectionRecord,
    LedgerCandidate,
    LedgerRoundClose,
)
from promptpotter.infrastructure.store.read_model import iter_jsonl

# The `ScoredCandidate` keys the fold copies verbatim — `LedgerCandidate`'s own field list
# minus the ones identity and the fold itself supply. DERIVED from `model_fields`, the same
# rule `build_round_summary` follows, so a field added to `LedgerCandidate` flows here with
# no second edit. Hand-written per-key reads are how the tree ended up silently missing a
# field the round summary already had.
_SCORED_INCLUDE = frozenset(LedgerCandidate.model_fields) - {
    "round",
    "idx",
    "parent_id",
    "source",
    "state",
}


def scan_ledger_cycle_seed(ledger_path: Path) -> CycleSeed | None:
    """The cycle's own seed, or ``None`` when it carries none (sweep / diag). Written once at mint, but
    the LAST match wins so a re-seed supersedes."""
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


def scan_ledger_ruler(ledger_path: Path) -> DeltaRuler | None:
    """The cycle's δ ruler, or ``None`` while it is still cold. Appended at lock and after every
    extension, so the LAST match wins — that record carries the widest membership."""
    found: DeltaRuler | None = None
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"ruler"})):
        if rec.get("record_type") != "ruler":
            continue
        data = rec.get("ruler")
        if isinstance(data, dict):
            try:
                found = DeltaRuler.model_validate(data)
            except ValidationError:
                continue
    return found


def scan_ledger_candidates(ledger_path: Path) -> list[LedgerCandidate]:
    """The candidate tier, folded from mint + score records — independent of round CLOSE, so a cycle whose
    producer died mid-round still names what it minted. Election and θ are round-close facts, not here."""
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
            # `(round, idx)` is the join, NOT the id — a re-run re-mints ids, position is
            # stable. `scores` IS a `ScoredCandidate.model_dump()` from EVERY sender, C0
            # included, so everything the candidate knows about itself is already here and
            # is copied by name. Election and θ are not: they belong to the ROUND, and the
            # round says so on its own close record (`scan_ledger_round_closes`).
            fields = {key: scores.get(key) for key in _SCORED_INCLUDE}
            if not int(scores.get("total") or 0):
                # A report over ZERO rows carries no measurement — an INVALID candidate's
                # synthetic 0.0 reads as getting every answer wrong. Identity and state survive;
                # numbers nothing earned do not (``_merge`` skips ``None``).
                fields["accuracy"] = None
                fields["composite_fitness"] = None
            _merge(
                (rnd, idx),
                state="invalid" if scores.get("invalid") else "measured",
                **fields,
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


def scan_ledger_decisions(ledger_path: Path) -> dict[int, list[dict[str, object]]]:
    """``round -> the decisions that round made``, in append order.

    Keyed on the STAMP ``record_decision`` was handed, never on ledger position. Position looks
    like the better signal — ``persist_round`` appends a drain immediately before its
    ``round:complete``, so the next close ought to name the flushing round — and it is wrong:
    round 0 closes TWICE, the second time when the ruler warms at round 1, so the next close
    after a round-1 decision reads 0. Trusting that misfiled 118 replayed decisions."""
    out: dict[int, list[dict[str, object]]] = {}
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"decision"})):
        rnd = rec.get("round")
        if not isinstance(rnd, int):
            continue
        out.setdefault(rnd, []).append(
            {
                "kind": rec.get("kind"),
                "inputs_ref": rec.get("inputs_ref") or {},
                "outcome": rec.get("outcome"),
                "data": rec.get("data") or {},
            }
        )
    return out


def scan_ledger_elections(ledger_path: Path) -> dict[int, ElectionRecord]:
    """``round -> the election it held``; last write per round wins, so a re-run supersedes. **A
    round with no entry never elected** — still scoring, or halted on a holed panel — which is a
    different fact from one that elected and crowned nobody (here, with an empty ``winner_label``).
    Only this scan separates them, so an absent crown is no evidence on its own."""
    out: dict[int, ElectionRecord] = {}
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"election"})):
        try:
            election = ElectionRecord.model_validate(rec)
        except ValidationError:
            continue
        out[election.round] = election
    return out


def scan_ledger_round_closes(ledger_path: Path) -> dict[int, LedgerRoundClose]:
    """``round -> LedgerRoundClose`` for every round that CLOSED; last write per round wins, so a rewind
    supersedes. **A round with no entry never closed, and that is the honest answer** — nothing invents one.
    A close with no readable payload is still a close; requiring one made this answer a narrower
    question than its name, so rewind admissibility grew a second full pass of its own."""
    out: dict[int, LedgerRoundClose] = {}
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"phase"})):
        if rec.get("phase") != "round" or rec.get("event") != "complete":
            continue
        rnd = rec.get("round")
        if not isinstance(rnd, int):
            continue
        payload = rec.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        ability = payload.get("ability")
        try:
            out[rnd] = LedgerRoundClose(
                round=rnd,
                ability=AbilityReading.model_validate(ability)
                if isinstance(ability, dict)
                else None,
                abilities=payload.get("abilities") or {},
            )
        except ValidationError:
            continue
    return out


__all__ = [
    "scan_ledger_candidates",
    "scan_ledger_cycle_seed",
    "scan_ledger_decisions",
    "scan_ledger_elections",
    "scan_ledger_round_closes",
]
