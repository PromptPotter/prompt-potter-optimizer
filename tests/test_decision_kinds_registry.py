"""Decision-kind registry — single seam for divergence gating.

These tests enforce the contract documented in
``promptpotter/domain/run_records.py``:

1. Every ``DecisionKind`` member is paired with a ``GatingMode`` in
   ``DECISION_GATING`` (no orphans).
2. Every ``REPLAYED`` kind has a registered replayer; every ``ARCHIVAL``
   kind has none. The pairing is symmetric — a kind cannot be both gated
   and unreplayable, nor archival yet replayable.
3. The runtime ledger primitive (``RunLedger``) round-trips records
   through ``events.jsonl`` so projection writers see the same shape on
   replay as on live append.
4. No call site passes a bare string for ``record_decision(kind=...)`` —
   the only valid argument is a ``DecisionKind`` member. This catches
   regressions before mypy in code paths mypy hasn't yet covered.
"""

from __future__ import annotations

import re
from pathlib import Path

from promptpotter.application.optimization.cycle import REPLAYERS
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import (
    DECISION_GATING,
    Decision,
    DecisionKind,
    GatingMode,
    Phase,
    Snapshot,
)
from promptpotter.infrastructure.ledger import RunLedger

_SRC_ROOT = Path(__file__).resolve().parent.parent / "promptpotter"


def test_every_decision_kind_has_a_gating_entry() -> None:
    missing = [k for k in DecisionKind if k not in DECISION_GATING]
    extra = [k for k in DECISION_GATING if k not in set(DecisionKind)]
    assert not missing, f"DecisionKind members missing from DECISION_GATING: {missing}"
    assert not extra, f"DECISION_GATING contains unknown kinds: {extra}"


def test_replayed_kinds_have_a_replayer() -> None:
    expected = {k for k, mode in DECISION_GATING.items() if mode is GatingMode.REPLAYED}
    missing = expected - set(REPLAYERS)
    assert not missing, (
        f"REPLAYED kinds without a registered replayer: {sorted(k.value for k in missing)}"
    )


def test_archival_kinds_have_no_replayer() -> None:
    archival = {k for k, mode in DECISION_GATING.items() if mode is GatingMode.ARCHIVAL}
    leaked = archival & set(REPLAYERS)
    assert not leaked, (
        f"ARCHIVAL kinds must not register a replayer: {sorted(k.value for k in leaked)}"
    )


# Match calls only — ``(?<!def )`` skips the ``Cycle.record_decision`` method
# signature. Then greedily skip the first argument (the decisions list) up to
# the first comma not nested in brackets/parens, and require the next token to
# start with ``DecisionKind.``. Bare-string second args ("round_winner") fail
# the match — exactly what we want to catch.
_RECORD_DECISION = re.compile(
    r"""(?<!def\ )record_decision\s*\(
        \s*[^,()\[\]]+,           # arg 1: decisions list (no nested punctuation)
        \s*(?P<kind>[^,)]+)       # arg 2: kind expression up to next comma/paren
    """,
    re.VERBOSE | re.DOTALL,
)


def test_no_bare_string_decision_kinds() -> None:
    """Every record_decision call passes a DecisionKind, not a bare string."""
    offenders: list[str] = []
    for py in _SRC_ROOT.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "record_decision(" not in text:
            continue
        for match in _RECORD_DECISION.finditer(text):
            kind_expr = match.group("kind").strip()
            if not kind_expr.startswith("DecisionKind."):
                offenders.append(f"{py.relative_to(_SRC_ROOT.parent)}: {kind_expr!r}")
    assert not offenders, "bare-string decision kinds found:\n  " + "\n  ".join(offenders)


def test_runledger_roundtrips_typed_records(tmp_path: Path) -> None:
    """Append decision/phase/snapshot, read back via iter() — types preserved."""
    ledger = RunLedger.open(CycleDir(tmp_path / "cyc1"))

    d = Decision(
        kind=DecisionKind.ROUND_WINNER,
        inputs_ref={"candidate_ids": ["c1"], "round_num": 0},
        outcome="c1",
    )
    p = Phase(phase="l1_generate", event="enter", round=1, payload={"n_variants": 3})
    s = Snapshot(round=1, candidate_idx=0, sample_idx=4, payload={"hit": True})

    assert ledger.append(d) == 0
    assert ledger.append(p) == 1
    assert ledger.append(s) == 2

    records = list(ledger.iter())
    assert len(records) == 3
    assert isinstance(records[0], Decision)
    assert records[0].kind is DecisionKind.ROUND_WINNER
    assert records[0].outcome == "c1"
    assert isinstance(records[1], Phase)
    assert records[1].phase == "l1_generate" and records[1].event == "enter"
    assert isinstance(records[2], Snapshot)
    assert records[2].sample_idx == 4


def test_runledger_persists_across_open(tmp_path: Path) -> None:
    """Reopening the same cycle dir continues offsets — no clobber."""
    cycle_dir = CycleDir(tmp_path / "cyc2")
    first = RunLedger.open(cycle_dir)
    first.append(Phase(phase="init", event="enter"))
    first.append(Phase(phase="init", event="exit"))

    second = RunLedger.open(cycle_dir)
    assert second.next_offset == 2
    third_offset = second.append(Phase(phase="baseline", event="enter"))
    assert third_offset == 2
    assert sum(1 for _ in second.iter()) == 3


def test_runledger_bind_fans_out_to_subscribers(tmp_path: Path) -> None:
    """``bind`` projections receive subsequent appends; prior records are not replayed."""
    received: list[tuple[int, str]] = []

    class _Recorder:
        def on_record(self, record: Phase | Decision | Snapshot, offset: int) -> None:
            received.append((offset, record.record_type))

    ledger = RunLedger.open(CycleDir(tmp_path / "cyc3"))
    ledger.append(Phase(phase="init", event="enter"))  # before bind — should not fire
    ledger.bind(_Recorder())
    ledger.append(Phase(phase="init", event="exit"))  # offset 1, should fire
    ledger.append(Decision(kind=DecisionKind.PROBE_ROUND_COMMITMENT, outcome=False))  # offset 2

    assert received == [(1, "phase"), (2, "decision")]


def test_open_cycle_ledger_lands_under_cycle_dir(tmp_path: Path) -> None:
    """``_open_cycle_ledger`` opens the ledger under the per-cycle audit dir.

    Phase 3 plumbing: the ledger MUST live at
    ``campaigns/{cycle_id}/ledger.jsonl`` (per-cycle scope) so a fork's
    facts never bleed into the family-root telemetry stream. Returns
    ``None`` when no store is wired (test-bypass paths).
    """
    from types import SimpleNamespace

    from promptpotter.application.bootstrap import _open_cycle_ledger
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    fake_session = SimpleNamespace(store=stores)

    ledger = _open_cycle_ledger(fake_session, "cycle_x")  # type: ignore[arg-type]
    assert ledger is not None
    assert ledger.path == stores.campaigns.campaign_dir("cycle_x") / "ledger.jsonl"

    # Storeless session (test-bypass path) → no ledger; loop still proceeds.
    assert _open_cycle_ledger(SimpleNamespace(store=None), "cycle_x") is None  # type: ignore[arg-type]


def test_open_cycle_ledger_resumes_offsets(tmp_path: Path) -> None:
    """A reopened ledger continues counting from the existing tail.

    Prevents a resume from clobbering prior facts: a fresh ``open()``
    over a populated ``events.jsonl`` reads the existing offset count
    so subsequent appends land at offset N (not 0).
    """
    from types import SimpleNamespace

    from promptpotter.application.bootstrap import _open_cycle_ledger
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    fake_session = SimpleNamespace(store=stores)

    first = _open_cycle_ledger(fake_session, "cycle_y")  # type: ignore[arg-type]
    assert first is not None
    first.append(Phase(phase="round", event="complete", round=0))
    first.append(Decision(kind=DecisionKind.ROUND_WINNER, outcome="c1", round=0))

    second = _open_cycle_ledger(fake_session, "cycle_y")  # type: ignore[arg-type]
    assert second is not None
    assert second.next_offset == 2
    second.append(Phase(phase="round", event="complete", round=1))
    assert sum(1 for _ in second.iter()) == 3
