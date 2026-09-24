"""Physical-file ledger scans, deliberately physical: ``CycleEventLog.iter`` would replay a fork's
inherited prefix. Never swallow an ``OSError`` — "unreadable" would answer as "nothing on it".

``rewind_to_round`` consults THIS, not the public ``rounds/`` tree, for admissibility: ``--from N``
is valid iff the ledger carries a closing ``PhaseRecord`` for round N — ``(phase="round",
event="complete")``, the one closing signature.

Round 0 closes through that same path via ``emit_origin_round``, and it closes **twice**: again
when the ruler warms at round 1, since the origin's theta cannot be fit before a second arm
exists, and only that SECOND record carries the usable theta. So a max-scan is safe while a count,
a first-match, or a reader updating on ``display`` alone is not — ``max()`` over
``scan_ledger_round_closes`` is the answer, and a second scan asking only for the maximum was the
same pass under another name. It never instantiates ``CycleEventLog``, so no subscribers fire
during an admissibility check.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptpotter.domain.phases import CampaignPhase, RunPhase
from promptpotter.domain.ruler import AbilityReading, DeltaRuler
from promptpotter.domain.run_records import (
    CandidateMintedRecord,
    CycleSeed,
    ElectionRecord,
    LedgerCandidate,
    LedgerRoundClose,
    SpendCeilingRecord,
    WallClock,
)
from promptpotter.domain.spend import TOKEN_KIND_BUCKET, BudgetChange
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.clock import epoch_seconds

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
    """The cycle's own seed, or ``None`` when it carries none (a diag). Written once at mint, but
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


def scan_ledger_spend_ceiling(ledger_path: Path) -> BudgetChange:
    """The cycle's standing operator ceiling — the LAST ``SpendCeilingRecord`` on its OWN ledger,
    so a fork never reads its parent's. ``(None, None)`` where the operator never set one."""
    found = BudgetChange(None, None)
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"spend_ceiling"})):
        if rec.get("record_type") != "spend_ceiling":
            continue
        try:
            ceiling = SpendCeilingRecord.model_validate(rec)
        except ValidationError:
            continue
        found = BudgetChange(ceiling.usd, ceiling.tokens)
    return found


def scan_ledger_rulers(ledger_path: Path) -> dict[str, DeltaRuler]:
    """Every δ scale this ledger carries, by the dataset whose sample ids its keys ARE. Appended
    at lock and after every extension, so the LAST record per dataset wins — that one carries the
    widest membership. A cycle owns one; an L4 outer cycle also owns the shared inner scale."""
    found: dict[str, DeltaRuler] = {}
    for rec in iter_jsonl(ledger_path, record_types=frozenset({"ruler"})):
        name, data = rec.get("dataset_name"), rec.get("ruler")
        if rec.get("record_type") != "ruler" or not isinstance(name, str):
            continue
        if isinstance(data, dict):
            try:
                found[name] = DeltaRuler.model_validate(data)
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


def _phase_seconds(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Bracketed clock per :class:`CampaignPhase`, paired on ``(phase, round)``.

    The roster is the ENUM, never a hand-listed set: ``round`` is an open marker with no exit,
    ``control`` is the run-phase channel and ``backend`` a warning channel, and each would read as
    a bracket that never closes. An unpaired enter contributes nothing — a phase the run died
    inside measured no span, and inventing one would close it at a moment nothing recorded."""
    brackets = {p.value for p in CampaignPhase}
    open_at: dict[tuple[str, object], float] = {}
    out: dict[str, float] = {}
    for rec in rows:
        phase = rec.get("phase")
        if rec.get("record_type") != "phase" or phase not in brackets:
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is None:
            continue
        key = (str(phase), rec.get("round"))
        if rec.get("event") == "enter":
            open_at[key] = at
        elif rec.get("event") == "exit" and (entered := open_at.pop(key, None)) is not None:
            out[str(phase)] = out.get(str(phase), 0.0) + max(0.0, at - entered)
    return out


def _gate_seconds(rows: list[dict[str, Any]], *, until: float | None) -> float:
    """Seconds held at the origin gate, off the ``control`` channel ``declare_run_phase`` owns.

    A rescore re-declares ``gate``, so a second one CLOSES the first: the wait and the re-measure
    both happened inside it, and nothing else brackets the re-measure, so it is counted here. An
    abandoned gate — abort, or a producer that vanished — closes at *until*, because the operator
    held it that long."""
    total, opened = 0.0, None
    for rec in rows:
        if rec.get("record_type") != "phase" or rec.get("phase") != "control":
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is None:
            continue
        if opened is not None:
            total += max(0.0, at - opened)
            opened = None
        if rec.get("event") == RunPhase.GATE.value:
            opened = at
    if opened is not None and until is not None:
        total += max(0.0, until - opened)
    return total


def _worked_seconds(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Summed call time per spend bucket. Cached calls are excluded for the reason the BILL
    excludes them — a replay reached no wire and occupied no clock."""
    out: dict[str, float] = {}
    for rec in rows:
        if rec.get("record_type") != "token_usage" or rec.get("cached"):
            continue
        bucket = TOKEN_KIND_BUCKET.get(rec.get("kind"))  # type: ignore[arg-type]
        seconds = rec.get("duration_s")
        if bucket is None or not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            continue
        out[bucket] = out.get(bucket, 0.0) + max(0.0, float(seconds))
    return out


def _round_ended_seconds(rows: list[dict[str, Any]], *, opened: float | None) -> dict[str, float]:
    """``round -> seconds from the run's start to that round's FIRST close``, the wall clock beside
    every round count. ``setdefault`` rather than last-wins: round 0 closes again when the ruler
    warms and a rewind re-runs its round, and neither is when the campaign first got there."""
    out: dict[str, float] = {}
    if opened is None:
        return out
    for rec in rows:
        if rec.get("record_type") != "phase" or rec.get("phase") != "round":
            continue
        rnd = rec.get("round")
        if rec.get("event") != "complete" or not isinstance(rnd, int) or isinstance(rnd, bool):
            continue
        if (at := epoch_seconds(rec.get("timestamp"))) is not None:
            out.setdefault(str(rnd), max(0.0, at - opened))
    return out


def _unworked_seconds(rows: list[dict[str, Any]]) -> float | None:
    """Seconds the run's cells were not ALLOWED to spend, off each cell's own envelope.

    ``None`` where no measured cell carried one — an unenveloped backend installs no give-back, so
    nothing WATCHED for a suspend and 0.0 would be a reading nobody took. A replayed cell is
    skipped for the same reason its bill is: the seconds on it were another run's."""
    total: float | None = None
    for rec in rows:
        if rec.get("record_type") != "snapshot" or rec.get("event") != "sample_scored":
            continue
        result = (rec.get("payload") or {}).get("result")
        if not isinstance(result, dict) or result.get("cached"):
            continue
        data = result.get("pipeline_data")
        seconds = data.get("unworked_s") if isinstance(data, dict) else None
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            continue
        total = (total or 0.0) + max(0.0, float(seconds))
    return total


def scan_ledger_wall_clock(ledger_path: Path, *, started_at: str, finished_at: str) -> WallClock:
    """Where this cycle's wall clock went — ONE screened pass, four folds, banked by ``_finalize_run``.

    Physical like its neighbours, so a fork answers for its OWN clock and not its parent's history.
    The endpoints are the RUNNER's, because the ledger's first record is already past
    ``init_services``: the ``init`` bracket reads under two seconds and is not a setup measurement.

    ``sample_scored`` is an event and not a record type, but the screen is a raw-line substring
    probe, so naming it there is what keeps the per-cell rows in and every other snapshot out."""
    rows = iter_jsonl(
        ledger_path, record_types=frozenset({"phase", "token_usage", "sample_scored"})
    )
    opened, closed = epoch_seconds(started_at), epoch_seconds(finished_at)
    # A resumed cycle's ledger holds every earlier launch, while both endpoints are THIS launch's —
    # so the folds read this launch alone, and a round an earlier one closed reports no clock
    # rather than an instant one.
    if opened is not None:
        rows = [
            r
            for r in rows
            if (at := epoch_seconds(r.get("timestamp"))) is not None and at >= opened
        ]
    elapsed = None if opened is None or closed is None else max(0.0, closed - opened)
    phase_s = _phase_seconds(rows)
    gate_s = _gate_seconds(rows, until=closed)
    return WallClock(
        elapsed_s=elapsed,
        phase_s=phase_s,
        worked_s=_worked_seconds(rows),
        round_ended_s=_round_ended_seconds(rows, opened=opened),
        gate_s=gate_s,
        unattributed_s=(
            None if elapsed is None else max(0.0, elapsed - sum(phase_s.values()) - gate_s)
        ),
        unworked_s=_unworked_seconds(rows),
    )


__all__ = [
    "scan_ledger_candidates",
    "scan_ledger_cycle_seed",
    "scan_ledger_decisions",
    "scan_ledger_elections",
    "scan_ledger_round_closes",
    "scan_ledger_wall_clock",
]
