"""View-factory round-trip invariant — the named contract behind the
two parallel rendering paths.

The unification refactor in ``presentation/views/`` collapses two parallel
renderers (``phase_events.py`` for live ANSI, ``log_md.py`` for disk-derived
markdown) onto one View dataclass set fed by two factories. The correctness
guarantee is that both factories produce the same View for the same logical
round.

This file pins that guarantee on ``RoundCompleteView`` — the one phase event
whose payload survives to disk in ``trial_NNNN.json``. Other phase events
(refine / probe / plan / escalation) are live-only and have no disk
counterpart, so the round-trip test does not apply to them.
"""

from __future__ import annotations

from dataclasses import replace

from promptpotter.domain.phases import PhaseEvent
from promptpotter.presentation.views.view_factories import (
    from_disk_round,
    from_phase_event,
)
from promptpotter.presentation.views.view_models import RoundCompleteView
from promptpotter.shared.statistics import wilson_ci


def _candidate_score_dict(
    *,
    candidate_id: str,
    accuracy: float,
    composite: float,
    hits: int,
    total: int,
    aborted: bool = False,
) -> dict:
    """Mirror what ``CandidateScore.to_dict`` writes into ``trial.candidate_scores``."""
    ci_lo, ci_hi = wilson_ci(hits, total)
    return {
        "candidate_id": candidate_id,
        "changes_description": "",
        "pipeline_params_override": None,
        "accuracy": accuracy,
        "composite": composite,
        "hits": hits,
        "total": total,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "evaluators": {"accuracy": accuracy},
        "escalation_aborted": aborted,
        "elimination_stopped": False,
        "scored_queries": total,
        "expected_queries": total,
        "invalid": False,
        "resumed_from_cache": False,
        "validation_failures": [],
        "runtime_failures": [],
        "elimination_context": {},
    }


def test_round_complete_view_roundtrip() -> None:
    """``from_phase_event(e) == from_disk(write_then_load(e))`` on
    ``RoundCompleteView`` — the named correctness invariant of the unification."""
    baseline_acc = 0.30
    winner_acc = 0.55
    winner_hits = 11
    winner_total = 20
    winner_composite = 0.60

    # Three candidates; the second one wins (highest composite).
    candidate_scores = [
        {
            "label": "C1",
            "accuracy": 0.40,
            "composite": 0.45,
            "hits": 8,
            "total": 20,
            "escalation_aborted": False,
        },
        {
            "label": "C2",
            "accuracy": winner_acc,
            "composite": winner_composite,
            "hits": winner_hits,
            "total": winner_total,
            "escalation_aborted": False,
        },
        {
            "label": "C3",
            "accuracy": 0.20,
            "composite": 0.25,
            "hits": 4,
            "total": 20,
            "escalation_aborted": False,
        },
    ]

    # Live path: build a phase event identical to what l1.py emits at
    # L1_SCORE:exit, run from_phase_event with a fresh ctx.
    ctx = {
        "round_num": 0,
        "baseline_accuracy": baseline_acc,
        "baseline_composite": 0.40,
        "composite_formula": "0.7*acc + 0.3*recall",
        "composite_formula_short": "0.7*A + 0.3*R",
    }
    event = PhaseEvent(
        phase="l1_score",
        event="exit",
        round=0,
        data={
            "winner_label": "C2",
            "winner_accuracy": winner_acc,
            "winner_composite": winner_composite,
            "winner_evaluators": {"accuracy": winner_acc},
            "improved": True,
            "candidate_scores": candidate_scores,
        },
    )
    live_view = from_phase_event(event, ctx)
    assert isinstance(live_view, RoundCompleteView)

    # Disk path: build a trial dict shaped like ``RoundResult.model_dump()``
    # for the same round, then run from_disk_round.
    trial = {
        "trial_id": "round_0",
        "round": 0,
        "label": "round_0",
        "accuracy": winner_acc,
        "composite": winner_composite,
        "hits": winner_hits,
        "total": winner_total,
        "improved": True,
        "p_value": live_view.p_value,
        "baseline_accuracy": baseline_acc,
        "evaluators": {"accuracy": winner_acc},
        "candidate_scores": [
            _candidate_score_dict(
                candidate_id=f"cand_{i}",
                accuracy=cs["accuracy"],
                composite=cs["composite"],
                hits=cs["hits"],
                total=cs["total"],
            )
            for i, cs in enumerate(candidate_scores)
        ],
        "opt_search_point": {"l1_critique_text": ""},
    }
    disk_view = from_disk_round(
        trial,
        composite_formula=ctx["composite_formula"],
        composite_formula_short=ctx["composite_formula_short"],
        baseline_composite=ctx["baseline_composite"],
    )

    # The live view carries an in-memory ``next_action`` flag that the
    # runner uses for control flow but never persists. Strip it from
    # the live view so the comparison covers only the persisted contract.
    live_view = replace(live_view, next_action="")

    assert live_view == disk_view


def test_round_complete_view_no_improvement() -> None:
    """Round trip stays consistent when no candidate beats baseline."""
    baseline_acc = 0.50
    candidate_scores = [
        {
            "label": "C1",
            "accuracy": 0.40,
            "composite": 0.42,
            "hits": 8,
            "total": 20,
            "escalation_aborted": False,
        },
    ]
    ctx = {"round_num": 0, "baseline_accuracy": baseline_acc}
    event = PhaseEvent(
        phase="l1_score",
        event="exit",
        round=0,
        data={
            "winner_label": "C1",
            "winner_accuracy": 0.40,
            "winner_composite": 0.42,
            "winner_evaluators": {},
            "improved": False,
            "candidate_scores": candidate_scores,
        },
    )
    live_view = from_phase_event(event, ctx)
    assert isinstance(live_view, RoundCompleteView)

    trial = {
        "round": 0,
        "accuracy": 0.40,
        "composite": 0.42,
        "hits": 8,
        "total": 20,
        "improved": False,
        "p_value": None,
        "baseline_accuracy": baseline_acc,
        "evaluators": {},
        "candidate_scores": [
            _candidate_score_dict(
                candidate_id="cand_0",
                accuracy=0.40,
                composite=0.42,
                hits=8,
                total=20,
            )
        ],
        "opt_search_point": {"l1_critique_text": ""},
    }
    disk_view = from_disk_round(trial)

    live_view = replace(live_view, next_action="")

    # No improvement → delta is 0, p_value is None, both ends agree.
    assert live_view.improved is False
    assert live_view.delta == 0.0
    assert live_view.p_value is None
    assert live_view == disk_view
