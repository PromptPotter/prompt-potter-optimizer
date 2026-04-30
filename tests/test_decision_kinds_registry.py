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
    Measurement,
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
    """Append decision/phase/snapshot/measurement, read back via iter() — types preserved."""
    ledger = RunLedger.open(CycleDir(tmp_path / "cyc1"))

    d = Decision(
        kind=DecisionKind.ROUND_WINNER,
        inputs_ref={"candidate_ids": ["c1"], "round_num": 0},
        outcome="c1",
    )
    p = Phase(phase="l1_generate", event="enter", round=1, payload={"n_variants": 3})
    s = Snapshot(round=1, candidate_idx=0, sample_idx=4, payload={"hit": True})
    m = Measurement(run_id="run_xyz", sample_id=7, hit=True, score=1.0, round=1)

    assert ledger.append(d) == 0
    assert ledger.append(p) == 1
    assert ledger.append(s) == 2
    assert ledger.append(m) == 3

    records = list(ledger.iter())
    assert len(records) == 4
    assert isinstance(records[0], Decision) and records[0].outcome == "c1"
    assert isinstance(records[1], Phase) and records[1].phase == "l1_generate"
    assert isinstance(records[2], Snapshot) and records[2].sample_idx == 4
    assert isinstance(records[3], Measurement)
    assert records[3].run_id == "run_xyz" and records[3].hit is True


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


def test_runlistener_tees_phase_and_snapshot_to_ledger(tmp_path: Path) -> None:
    """RunListener appends Phase + Snapshot records to its ledger when one is wired.

    Phase 3 contract: every callback that fans out to display sinks
    also tees a typed record onto the per-cycle ledger when
    ``RunListener.ledger`` is set. The legacy sinks still fire (dual-
    path) so behaviour is unchanged for callers that don't bind a
    ledger.
    """
    from promptpotter.application.runner import RunListener
    from promptpotter.domain.phases import PhaseEvent

    ledger = RunLedger.open(CycleDir(tmp_path / "cyc1"))
    cb = RunListener()
    cb.ledger = ledger
    cb.set_round(3)

    cb.on_phase(PhaseEvent(phase="l1_generate", event="enter", round=3, data={"k": "v"}))
    cb.on_sample_scored(
        0, 1, 4, 5, {"hit": True, "score": 1.0, "pipeline_data": {"terminated_at": "llm_only"}}
    )
    cb.on_candidate_scored(0, 1, {"accuracy": 0.6, "hits": 6, "total": 10, "composite": 0.55})

    records = list(ledger.iter())
    assert len(records) == 3
    assert isinstance(records[0], Phase)
    assert records[0].phase == "l1_generate" and records[0].payload["data_keys"] == ["k"]
    assert isinstance(records[1], Snapshot)
    assert records[1].sample_idx == 4 and records[1].payload["hit"] is True
    assert isinstance(records[2], Snapshot)
    assert records[2].candidate_idx == 0 and records[2].payload["accuracy"] == 0.6


def test_archive_save_with_ledger_appends_measurement_records(tmp_path: Path) -> None:
    """``MeasurementArchive.save(ledger=...)`` mirrors each measurement onto the ledger.

    The archive write is the source of truth (tenant-global); the
    Measurement records are the per-cycle slice so a fork's iter()
    sees which runs happened without re-scanning the global archive.
    """
    from promptpotter.domain.run_records import Measurement as MeasurementRecord
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    ledger = RunLedger.open(CycleDir(tmp_path / "cyc_archive"))
    stores.archive.save(
        "any-backend",
        "run_t1",
        {
            "run_id": "run_t1",
            "name": "run_t1",
            "content_hash": "hash_t1",
            "prompt_fields_id": "pf_x",
            "item_count": 2,
            "scores": {"accuracy": 0.5, "total": 2},
            "node_configs": [["llm_only", {"model": "X"}]],
            "pipeline_params": {"llm_only": {"model": "X"}},
            "created_at": "2026-04-30T00:00:00Z",
            "measurements": [
                {
                    "sample_id": 0,
                    "query": "q0",
                    "ground_truth": "a",
                    "predicted": "a",
                    "hit": True,
                    "score": 1.0,
                    "pipeline_data": {},
                },
                {
                    "sample_id": 1,
                    "query": "q1",
                    "ground_truth": "b",
                    "predicted": "x",
                    "hit": False,
                    "score": 0.0,
                    "pipeline_data": {},
                },
            ],
        },
        ledger=ledger,
        round_num=2,
    )

    records = [r for r in ledger.iter() if isinstance(r, MeasurementRecord)]
    assert len(records) == 2
    assert records[0].sample_id == 0 and records[0].hit is True
    assert records[0].run_id == "run_t1" and records[0].config_hash == "hash_t1"
    assert records[1].sample_id == 1 and records[1].hit is False
    assert {r.round for r in records} == {2}


def test_runledger_inherit_from_replays_parent_records_first(tmp_path: Path) -> None:
    """A fork's iter() walks parent's records up to the cut offset, then its own.

    Phase 4 contract: the fork's view of its history is parent[0:cut] +
    fork's own appends. Subscribers of the fork ledger only see new
    appends (the parent already broadcast its records when they
    happened) — but a downstream replay (e.g. a webapp opening the
    fork) walks the combined stream.
    """
    parent = RunLedger.open(CycleDir(tmp_path / "parent"))
    parent.append(Phase(phase="round", event="complete", round=0))
    parent.append(Decision(kind=DecisionKind.ROUND_WINNER, outcome="c1", round=0))
    parent.append(Phase(phase="round", event="complete", round=1))  # past the cut

    fork = RunLedger.open(CycleDir(tmp_path / "fork"))
    fork.inherit_from(parent, offset=2)  # parent's first 2 records
    fork.append(Decision(kind=DecisionKind.ROUND_WINNER, outcome="c2", round=1))

    history = list(fork.iter())
    assert len(history) == 3
    assert isinstance(history[0], Phase) and history[0].round == 0
    assert isinstance(history[1], Decision) and history[1].outcome == "c1"
    assert isinstance(history[2], Decision) and history[2].outcome == "c2"


def test_divergence_hint_lists_every_decision_kind() -> None:
    """The CLI hint shown on resume-divergence enumerates every kind by gating mode.

    Was hardcoded — silently rotted when a kind was added or moved between
    REPLAYED/ARCHIVAL. Now derived from ``DECISION_GATING`` so adding a
    kind updates the operator message automatically.
    """
    from promptpotter.presentation.cli.campaign_runner import _DIVERGENCE_HINT

    for kind, mode in DECISION_GATING.items():
        assert kind.value in _DIVERGENCE_HINT, (
            f"_DIVERGENCE_HINT must mention {kind.value} ({mode.value})"
        )


def test_runledger_inherit_from_rejects_rebind(tmp_path: Path) -> None:
    """Re-binding a fork's parent silently would mask the cutover lineage."""
    import pytest

    parent_a = RunLedger.open(CycleDir(tmp_path / "a"))
    parent_b = RunLedger.open(CycleDir(tmp_path / "b"))
    fork = RunLedger.open(CycleDir(tmp_path / "fork"))
    fork.inherit_from(parent_a, 0)
    # Idempotent re-bind to same args is fine.
    fork.inherit_from(parent_a, 0)
    with pytest.raises(ValueError, match="already inheriting"):
        fork.inherit_from(parent_b, 0)


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
