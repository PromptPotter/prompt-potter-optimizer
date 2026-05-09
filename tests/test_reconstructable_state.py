"""Reconstructable state — ledger as the single source of truth.

Per ``docs/architecture.md`` §0 and ``docs/specs/m10-cleanup.md`` §3.8:
:class:`Session` (and its bundled :class:`EscalationState` /
:class:`AuditTrailView` projections) MUST be reconstructable from
``events.jsonl`` alone, modulo a small allowlist of wiring/identity
fields whose values come from bootstrap config, not the ledger.

This test ratifies the invariant for every reconstructable surface
that exists today. New mutable cross-round state added without an
event source — or new ledger events that lose data needed for
reconstruction — fail this test. Each failure becomes either:

* (a) a bug — add the missing field to the relevant ledger event,
* (b) a derivation — convert the field to a ``@property`` over ledger
  reads (drops the in-memory mirror), or
* (c) an allowlist entry — wiring/identity that's bootstrap-sourced
  and can't be recovered from events.

Incremental coverage: the test starts with what works today
(``EscalationState`` + ``AuditTrailView``); follow-up PRs
expand to ``Cycle.tracking``, ``opt_sp.round_history``, etc.
"""

from __future__ import annotations

from pathlib import Path

from promptpotter.application.optimization.escalation.state import EscalationState
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import LLMCallRecord, PhaseRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.projections.audit_trail import AuditTrailView


def _scripted_ledger(tmp_path: Path) -> CycleEventLog:
    """Two-round + L2-fire scripted ledger; no LLM calls fired."""
    ledger = CycleEventLog.open(CycleDir(tmp_path / "cyc"))
    # Round 1: did not improve.
    ledger.append(
        PhaseRecord(
            phase="round",
            event="complete",
            round=1,
            payload={"improved": False, "composite_fitness": 0.5, "accuracy": 0.5},
        )
    )
    # Round 2: improved.
    ledger.append(
        PhaseRecord(
            phase="round",
            event="complete",
            round=2,
            payload={"improved": True, "composite_fitness": 0.6, "accuracy": 0.6},
        )
    )
    # L2 fire on round 3 (carries best-at-entry baselines).
    ledger.append(
        PhaseRecord(
            phase="l2_context",
            event="exit",
            round=3,
            payload={
                "data": {
                    "l2_round": 1,
                    "l2_stall_count": 0,
                    "l2_best_accuracy_at_entry": 0.6,
                    "l2_best_composite_fitness_at_entry": 0.6,
                }
            },
        )
    )
    return ledger


def test_escalation_state_round_trips_through_ledger(tmp_path: Path) -> None:
    """Live-mutated ``EscalationState`` snapshot equals the value rebuilt from the ledger.

    Drives the exact sequence the round loop drives: ``observe_round``
    on round-complete, ``record_l2_fired`` on L2 exit. Snapshots the
    private counters (no public setter exists), then replays through
    ``EscalationState.from_ledger`` and compares every counter.
    """
    ledger = _scripted_ledger(tmp_path)
    rebuilt = EscalationState.from_ledger(ledger)
    # The driving observation methods produce the same fold outcome:
    live = EscalationState()
    live.observe_round(
        improved=False,
        current_accuracy=0.5,
        l1_patience=10,
        enable_l2=True,
    )
    live.observe_round(
        improved=True,
        current_accuracy=0.6,
        l1_patience=10,
        enable_l2=True,
    )
    live.record_l2_fired(best_accuracy=0.6, best_composite_fitness=0.6)

    # Every private counter must match — there is no public setter for
    # any of these, so the only way they can drift is a fold/observation
    # asymmetry (which IS a bug per §3.8).
    fields = [
        "_l1_stall_count",
        "_l2_round",
        "_l2_stall_count",
        "_l2_best_accuracy_at_entry",
        "_l2_best_composite_fitness_at_entry",
        "_l3_round",
        "_l3_stall_count",
        "_l3_best_accuracy_at_entry",
        "_l3_best_composite_fitness_at_entry",
    ]
    for field in fields:
        assert getattr(rebuilt, field) == getattr(live, field), (
            f"EscalationState.{field} drift: ledger-rebuilt {getattr(rebuilt, field)} "
            f"vs live-mutated {getattr(live, field)}"
        )


def test_audit_trail_round_trips_llm_call_records(tmp_path: Path) -> None:
    """``AuditTrailView``'s sticky/round node state is fully ledger-driven.

    With ``LLMCallRecord`` + ``PhaseRecord('round', 'enter'|'complete')``
    as the only inputs, the audit-trail projection must produce the
    same on-disk ``round_NNNN.json`` shape from a replayed ledger as it
    would from a live append-and-flush sequence.
    """
    cycle_dir = tmp_path / "campaigns" / "cyc1"
    cycle_dir.mkdir(parents=True)
    ledger = CycleEventLog.open(CycleDir(cycle_dir))
    proj = AuditTrailView.from_cycle_dir(CycleDir(cycle_dir))
    ledger.bind(proj)

    ledger.append(PhaseRecord(phase="round", event="enter", round=2))
    ledger.append(
        LLMCallRecord(
            node="l1_generate",
            round=2,
            payload={"type": "l1_generate", "response": "ok", "duration_s": 0.05},
        )
    )
    ledger.append(PhaseRecord(phase="round", event="complete", round=2))

    written = cycle_dir / ".runtime" / "cache" / "rounds" / "round_0002.json"
    assert written.exists(), "round-complete must flush a derived view of the ledger"

    # Replay path: a fresh projection bound to a no-history ledger that
    # iterates the existing events.jsonl produces identical content.
    fresh_dir = tmp_path / "fresh" / "cyc1"
    fresh_dir.mkdir(parents=True)
    fresh = AuditTrailView(fresh_dir / ".runtime" / "cache" / "rounds")
    for offset, record in enumerate(ledger.iter()):
        fresh.on_record(record, offset)

    fresh_written = fresh_dir / ".runtime" / "cache" / "rounds" / "round_0002.json"
    assert fresh_written.exists(), "ledger replay must reconstruct the same round file"

    # Structural equivalence — strip the wall-clock ``started_at`` /
    # ``finished_at`` slots which are set at observation time
    # (``datetime.now()``), not from event timestamps. They land on the
    # ``_ALLOWLIST`` below as projection-only fields; the §3.8 follow-up
    # is to source them from the round-enter / round-complete
    # ``PhaseRecord.timestamp`` instead.
    import json as _json

    live_payload = _json.loads(written.read_text(encoding="utf-8"))
    fresh_payload = _json.loads(fresh_written.read_text(encoding="utf-8"))
    for k in ("started_at", "finished_at"):
        live_payload.pop(k, None)
        fresh_payload.pop(k, None)
    assert fresh_payload == live_payload, (
        "AuditTrailView drift: live-bind vs replay-from-ledger produced different content"
    )


# Documented allowlist for fields that intentionally do NOT round-trip
# through the ledger — they're bootstrap-sourced wiring/identity, not
# state. New entries need a one-line "why exempt" comment.
_ALLOWLIST: dict[str, str] = {
    # backend_client: the live transport handle. Identity is reconstructed
    # from session config (backend_url + auth), not ledger events.
    "backend_client": "transport handle; reconstructed from session config",
    # tenant_id: bootstrap identity. Stored in `active_session.json`, not
    # in events.jsonl.
    "tenant_id": "bootstrap identity",
    # obs: the observability bridge (Langfuse + MLflow + audit trail).
    # Re-wired on each session boot.
    "obs": "observability bridge; re-wired at boot",
    # AuditTrailView.started_at / finished_at — wall-clock stamps
    # captured at begin_round/flush (datetime.now()) rather than the
    # corresponding PhaseRecord.timestamp. §3.8 follow-up: source from
    # the ledger event's timestamp so the round file becomes a pure
    # derived view.
    "AuditTrailView.started_at": "wall-clock observation; §3.8 follow-up to source from PhaseRecord.timestamp",
    "AuditTrailView.finished_at": "wall-clock observation; §3.8 follow-up to source from PhaseRecord.timestamp",
}


def test_allowlist_entries_are_documented() -> None:
    """Every ``_ALLOWLIST`` entry carries a one-line ``why exempt`` rationale.

    Bare entries are an indicator of unaudited divergence — every exempt
    field MUST have its rationale recorded, so a future review can
    distinguish "deliberately exempt" from "we forgot to fix this".
    """
    bare = [name for name, why in _ALLOWLIST.items() if not why.strip()]
    assert not bare, "Reconstructable-state allowlist entries missing rationale:\n  " + "\n  ".join(
        bare
    )
