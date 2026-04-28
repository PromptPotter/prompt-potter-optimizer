"""Interactive composite-score steering — hot-swap contract.

Guards two invariants:

1. A valid ``scoring_steer.json`` replaces ``session.scoring.round_scorer``
   and is archived to ``scoring_steer.applied.{ts}.json`` so the next
   round-end does not re-apply it.
2. An invalid file (broken formula, undefined name) does NOT corrupt
   ``session.scoring.round_scorer``; the file is left in place for the
   operator to fix.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _write_steer(cycle_dir: Path, payload: dict) -> Path:
    p = cycle_dir / "scoring_steer.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _make_session(tmp_path: Path) -> SimpleNamespace:
    """Minimal fake satisfying ``apply_steer_file``'s session contract."""
    cycle_id = "cycle_steer_test"
    cycle_dir = tmp_path / "campaigns" / cycle_id
    cycle_dir.mkdir(parents=True)
    campaigns = SimpleNamespace(campaign_dir=lambda _cid: cycle_dir)
    store = SimpleNamespace(campaigns=campaigns)
    state = SimpleNamespace(cycle_id=cycle_id)
    scoring = SimpleNamespace(round_scorer=None, scorer_round_formula=None)
    return SimpleNamespace(
        state=state,
        store=store,
        scoring=scoring,
        _cycle_dir=cycle_dir,
    )


def test_valid_steer_swaps_round_scorer_and_archives_file(tmp_path: Path) -> None:
    from promptpotter.application.scoring.scoring_steer import apply_steer_file

    session = _make_session(tmp_path)
    _write_steer(session._cycle_dir, {"per_round": "0.5 * accuracy + 0.5 * latency_norm"})

    events: list[dict] = []
    applied = apply_steer_file(
        session, round_num=3, on_phase=lambda evt: events.append(evt.model_dump())
    )

    assert applied == "0.5 * accuracy + 0.5 * latency_norm"
    assert callable(session.scoring.round_scorer)
    # Quick sanity: the new scorer evaluates against the registry namespace.
    assert session.scoring.round_scorer({"accuracy": 1.0, "latency_norm": 1.0}) == pytest.approx(
        1.0
    )
    assert session.scoring.scorer_round_formula == applied

    # File archived, not left in place.
    assert not (session._cycle_dir / "scoring_steer.json").exists()
    assert any(p.name.startswith("scoring_steer.applied.") for p in session._cycle_dir.iterdir())

    # Phase event surfaced for the operator-facing log.
    assert len(events) == 1
    assert events[0]["phase"] == "scoring_steer"
    assert events[0]["event"] == "applied"
    assert events[0]["data"]["formula"] == applied


def test_invalid_steer_leaves_state_untouched(tmp_path: Path) -> None:
    from promptpotter.application.scoring.scoring_steer import apply_steer_file

    session = _make_session(tmp_path)
    sentinel = object()
    session.scoring.round_scorer = sentinel
    session.scoring.scorer_round_formula = "original"
    _write_steer(session._cycle_dir, {"per_round": "nonexistent_evaluator * 1.0"})

    applied = apply_steer_file(session, round_num=0, on_phase=None)

    assert applied is None
    assert session.scoring.round_scorer is sentinel  # exact identity preserved
    assert session.scoring.scorer_round_formula == "original"
    assert (session._cycle_dir / "scoring_steer.json").exists()  # left for operator


def test_no_steer_file_is_noop(tmp_path: Path) -> None:
    from promptpotter.application.scoring.scoring_steer import apply_steer_file

    session = _make_session(tmp_path)
    sentinel = object()
    session.scoring.round_scorer = sentinel

    assert apply_steer_file(session, round_num=0, on_phase=None) is None
    assert session.scoring.round_scorer is sentinel
