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


# Match calls only — ``(?<!def )`` skips the ``def record_decision(...)``
# helper definition in ``domain/run_records.py``. Then greedily skip the first
# argument (the decisions list / ledger sink) up to the first comma not nested
# in brackets/parens, and require the next token to start with ``DecisionKind.``.
# Bare-string second args ("round_winner") fail the match — exactly what we
# want to catch.
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
    s = Snapshot(
        event="sample_scored",
        round=1,
        candidate_idx=0,
        sample_idx=4,
        payload={"hit": True},
    )

    assert ledger.append(d) == 0
    assert ledger.append(p) == 1
    assert ledger.append(s) == 2

    records = list(ledger.iter())
    assert len(records) == 3
    assert isinstance(records[0], Decision) and records[0].outcome == "c1"
    assert isinstance(records[1], Phase) and records[1].phase == "l1_generate"
    assert isinstance(records[2], Snapshot) and records[2].sample_idx == 4


def test_open_cycle_ledger_lands_under_cycle_dir(tmp_path: Path) -> None:
    """``_open_cycle_ledger`` opens the ledger under the per-cycle audit dir.

    Phase 3 plumbing: the ledger MUST live at
    ``campaigns/{cycle_id}/.runtime/ledger.jsonl`` (per-cycle scope) so a
    fork's facts never bleed into the family-root telemetry stream.
    Returns ``None`` when no store is wired (test-bypass paths).
    """
    from types import SimpleNamespace

    from promptpotter.application.bootstrap import _open_cycle_ledger
    from promptpotter.infrastructure.store import build_stores

    stores = build_stores(tmp_path / "projects", datasets_root=tmp_path / "datasets")
    fake_session = SimpleNamespace(store=stores)

    ledger = _open_cycle_ledger(fake_session, "cycle_x")  # type: ignore[arg-type]
    assert ledger is not None
    assert ledger.path == stores.campaigns.campaign_dir("cycle_x") / ".runtime" / "ledger.jsonl"

    # Storeless session (test-bypass path) → no ledger; loop still proceeds.
    assert _open_cycle_ledger(SimpleNamespace(store=None), "cycle_x") is None  # type: ignore[arg-type]


def test_runlistener_emits_records_to_ledger(tmp_path: Path) -> None:
    """RunListener is the single ingress: every callback appends one typed record.

    Subscribers consume via ``on_record`` only; there is no parallel
    direct-callback path. The records carry the rich payload subscribers
    need (full result dicts, full score reports, view dicts) so any
    consumer can rebuild its view from the ledger alone.
    """
    from promptpotter.application.runner import RunListener
    from promptpotter.domain.phases import PhaseEvent

    ledger = RunLedger.open(CycleDir(tmp_path / "cyc1"))
    cb = RunListener(ledger=ledger)
    cb.set_round(3)

    cb.on_phase(PhaseEvent(phase="l1_generate", event="enter", round=3, data={"k": "v"}))
    cb.on_sample_scored(
        0, 1, 4, 5, {"hit": True, "score": 1.0, "pipeline_data": {"terminated_at": "llm_only"}}
    )
    cb.on_candidate_scored(0, 1, {"accuracy": 0.6, "hits": 6, "total": 10, "composite": 0.55})

    records = list(ledger.iter())
    assert len(records) == 3
    assert isinstance(records[0], Phase)
    assert records[0].phase == "l1_generate"
    assert records[0].payload["data"] == {"k": "v"}
    assert isinstance(records[1], Snapshot)
    assert records[1].event == "sample_scored"
    assert records[1].sample_idx == 4 and records[1].payload["result"]["hit"] is True
    assert isinstance(records[2], Snapshot)
    assert records[2].event == "candidate_scored"
    assert records[2].candidate_idx == 0
    assert records[2].payload["scores"]["accuracy"] == 0.6


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


