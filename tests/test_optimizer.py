"""Optimizer loop invariants — L1 detector, L2/L3 output validators, PoBB
elimination, layout validators, sweep payload round-trip.

Seven named invariants:
  1. L1 ``detect_invariants``: a candidate is a non-empty unique mutation
     of the parent OSP, else a ValidationFailure attaches → synth-0
     downstream. Idempotent under repeat calls; pipeline_params_override
     counts as mutation.
  2. L2 output validators (``L2_DIRECTIVE_LENGTH_FLOOR``,
     ``L2_DIRECTIVE_VERBATIM_REPEAT``) flag the V1 failure shapes with
     ``nurse_target='l3'``; ``run_l2_output_validators`` aggregates.
  3. L3 output validators (``L3_PLAN_LENGTH_FLOOR``,
     ``L3_PLAN_VERBATIM_REPEAT``) flag the plan-side V1 failures.
  4. ``validate_l1_layout`` flips ``is_valid`` only on hard failures
     (mandatory missing / unknown name / dup within slot); the
     ``layout_unchanged_from_prior`` soft check fires without flipping
     ``is_valid``.
  5. Bayesian PoBB: ``posterior_best_probabilities`` sums to 1.0; clear
     leaders collapse to ~1.0; uniform regimes diffuse to ~1/K. ``PoBBCheck``
     respects ``n_min`` floor and ``lock_in_n_min`` lock-in gate;
     ``lock_in=1.0`` disables the lock-in branch.
  6. ``SweepPayload`` round-trips through ``OptSearchPoint``: brief +
     l1_layout dict survive ``model_dump`` → reload; mandatory layout
     placeholders enforced; extra keys rejected at parse.
  7. L2 ``action`` channel: ``probe_round`` round-trips through
     ``_parse_l2``; garbage values default to ``normal_round``;
     ``_apply_l2`` sets ``cycle.probe_next_round`` + records a
     ``PROBE_ROUND_COMMITMENT`` decision keyed on the action.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from promptpotter.application.optimization.escalation import (
    _apply_l2,
    _parse_l2,
    apply_sweep_payload_to_osp,
)
from promptpotter.application.optimization.l1_validators import detect_invariants
from promptpotter.application.optimization.l2_validators import (
    L2_DIRECTIVE_LENGTH_FLOOR,
    L2_DIRECTIVE_VERBATIM_REPEAT,
    L3_PLAN_LENGTH_FLOOR,
    L3_PLAN_VERBATIM_REPEAT,
    run_l2_output_validators,
    run_l3_output_validators,
)
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout, validate_l1_layout
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.run_records import DecisionKind, SweepPayload

scipy = pytest.importorskip("scipy")  # transitively required by other math helpers

from promptpotter.application.optimization.elimination import (  # noqa: E402
    PoBBCheck,
    PoBBConfig,
)
from promptpotter.domain.analysis import EscalationTarget  # noqa: E402
from promptpotter.shared.statistics import (  # noqa: E402
    pobb_should_stop,
    posterior_best_probabilities,
)

# ===========================================================================
# L1 detect_invariants
# ===========================================================================


def _parent() -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.")


def _child(parent: OptSearchPoint, **changes) -> CandidateProposal:
    return CandidateProposal(osp=parent.mutate(**changes))


def test_no_op_clone_attaches_validation_failure():
    parent = _parent()
    proposals = [_child(parent), _child(parent, persona="Expert ranker")]

    stats = detect_invariants(proposals, parent)

    no_op_reasons = [vf.reason for vf in proposals[0].osp.validation_failures]
    assert "no_op_variant" in no_op_reasons
    assert proposals[1].osp.validation_failures == []
    assert stats.l1_n_no_op == 1
    assert stats.l1_n_duplicate == 0
    assert stats.l1_yield == 0.5


def test_duplicate_signature_attaches_validation_failure():
    parent = _parent()
    proposals = [
        _child(parent, persona="Specialist"),
        _child(parent, persona="Specialist"),  # same signature → duplicate
        _child(parent, instruction="Rank with care."),
    ]

    stats = detect_invariants(proposals, parent)

    assert proposals[0].osp.validation_failures == []
    dup_reasons = [vf.reason for vf in proposals[1].osp.validation_failures]
    assert "duplicate_variant" in dup_reasons
    assert proposals[2].osp.validation_failures == []
    assert stats.l1_n_duplicate == 1
    assert stats.l1_n_no_op == 0
    assert stats.l1_yield == 2 / 3


# ===========================================================================
# L2 output validators (V1)
# ===========================================================================


def _osp(**kwargs) -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.", **kwargs)


def test_directive_length_floor_fires_on_short_directive():
    out = L2_DIRECTIVE_LENGTH_FLOOR.run({"directive": "be better"}, opt_sp=_osp())
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l3"


def test_directive_verbatim_repeat_fires():
    osp = _osp(l2_brief="Use ONLY model X for now.")
    out = L2_DIRECTIVE_VERBATIM_REPEAT.run({"directive": "Use ONLY model X for now."}, opt_sp=osp)
    assert out is not None
    assert out.score == 0.0
    assert "Use ONLY model X" in out.evidence["directive"]


def test_run_l2_output_validators_aggregates():
    osp = _osp(l2_brief="bad short")
    outcomes = run_l2_output_validators({"directive": "bad short"}, osp)
    ids = {o.validator_id for o in outcomes}
    assert "l2_directive_length_floor" in ids
    assert "l2_directive_verbatim_repeat" in ids


# ===========================================================================
# L3 output validators (V1)
# ===========================================================================


def test_plan_length_floor_fires_on_short_plan():
    out = L3_PLAN_LENGTH_FLOOR.run({"plan": "do better"}, opt_sp=_osp())
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l3"


def test_plan_verbatim_repeat_fires():
    osp = _osp(plan="Maintain current strategy and explore persona axis carefully.")
    out = L3_PLAN_VERBATIM_REPEAT.run(
        {"plan": "Maintain current strategy and explore persona axis carefully."}, opt_sp=osp
    )
    assert out is not None
    assert out.score == 0.0


def test_run_l3_output_validators_aggregates():
    osp = _osp(plan="short")
    outcomes = run_l3_output_validators({"plan": "short"}, osp)
    ids = {o.validator_id for o in outcomes}
    assert "l3_plan_length_floor" in ids
    assert "l3_plan_verbatim_repeat" in ids


# ===========================================================================
# L1 layout validators
# ===========================================================================


def test_layout_missing_mandatory_placeholder_is_hard_failure():
    """Mandatory placeholders must appear somewhere across all slots."""
    bad = L1Layout(task_intent=["l2_directive"], problem_description=["rendered_prompt"])
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_missing_mandatory" in ids


def test_layout_unknown_placeholder_is_hard_failure():
    """Placeholder names must be in L1_POSSIBLE."""
    bad = L1Layout(
        task_intent=["l2_directive", "made_up_signal"],
        problem_description=["rendered_prompt", "pipeline_axes", "plan"],
    )
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_unknown_placeholder" in ids


def test_layout_duplicate_within_slot_is_hard_failure():
    """No slot may list the same placeholder twice."""
    bad = L1Layout(
        task_intent=["l2_directive", "l2_directive"],
        problem_description=["rendered_prompt", "pipeline_axes", "plan"],
    )
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_dups_within_slot" in ids


def test_layout_unchanged_from_prior_is_soft():
    """Soft signal: layout proposed but identical to prior — flag, don't block."""
    layout = default_l1_layout()
    result = validate_l1_layout(layout, prior_layout=layout)
    assert result.is_valid is True  # soft fires WITHOUT flipping is_valid
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_unchanged_from_prior" in ids


# ===========================================================================
# Bayesian Posterior-of-Being-Best (PoBB)
# ===========================================================================


def test_posterior_best_probabilities_sums_to_one():
    rng = np.random.default_rng(42)
    histories = {
        "a": [0.6, 0.7, 0.8, 0.6, 0.7],
        "b": [0.5, 0.5, 0.5, 0.5, 0.5],
        "c": [0.4, 0.3, 0.5, 0.3, 0.4],
    }
    probs = posterior_best_probabilities(histories, rng=rng)
    assert set(probs) == {"a", "b", "c"}
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_high_signal_collapses_to_clear_winner():
    """Clear-leader regime: P(loser is best) → 0; P(winner is best) → 1."""
    rng = np.random.default_rng(0)
    histories = {
        "winner": [1.0] * 8,
        "loser": [0.0] * 8,
    }
    probs = posterior_best_probabilities(histories, n_samples=2000, rng=rng)
    assert probs["winner"] >= 0.99
    assert probs["loser"] <= 0.01


def test_pobb_should_stop_threshold():
    assert pobb_should_stop(0.04, 0.05) is True
    assert pobb_should_stop(0.05, 0.05) is False  # strict < threshold
    assert pobb_should_stop(0.50, 0.05) is False


def test_pobb_check_n_min_floor():
    """Below n_min queries, no signal is emitted regardless of separation."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_queries=10)
    check.register_completed([1.0] * 10, candidate_id="winner")
    check.set_current("loser")
    # 3 queries < n_min=4 — too early to fire even though signal is huge.
    sig = check.check([{"fitness": 0.0}] * 3, candidate_idx=1, n_total_candidates=2)
    assert sig is None


def test_pobb_check_high_signal_stops_inferior():
    """Loser candidate vs strong prior fires within ≤5 queries at ε=0.05."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_queries=20)
    check.register_completed([1.0] * 20, candidate_id="winner")
    check.set_current("loser")
    sig = check.check([{"fitness": 0.0}] * 5, candidate_idx=1, n_total_candidates=2)
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "winner"
    assert cr["p_best"] < 0.05
    assert "p_best_snapshot" in cr


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8), n_queries=20
    )
    # Prior: weak candidate. Current: clear leader.
    check.register_completed([0.0] * 20, candidate_id="weak_prior")
    check.set_current("strong_current")
    sig = check.check([{"fitness": 1.0}] * 8, candidate_idx=1, n_total_candidates=3)
    assert sig is not None
    assert sig.target == EscalationTarget.LEADER_LOCKED
    cr = sig.check_result
    assert cr["leader_id"] == "strong_current"
    assert cr["p_best"] >= 0.95
    assert cr["queries_scored"] == 8
    # Two candidates remain unscored (idx=1 of 3).
    assert sig.candidates_skipped == 1


# ===========================================================================
# SweepPayload → OSP round-trip
# ===========================================================================


def _full_layout_dict() -> dict[str, list[str]]:
    """Layout with all mandatory placeholders — passes hard validators."""
    return default_l1_layout().model_dump()


def test_sweep_payload_roundtrips_through_opt_search_point() -> None:
    layout_dict = _full_layout_dict()
    payload = SweepPayload(
        reason="canonical case",
        brief="reason step-by-step before answering",
        l1_layout=layout_dict,
    )

    osp = OptSearchPoint.from_prompt_fields({"persona": "p", "task_intent": "t"})
    apply_sweep_payload_to_osp(osp, payload)

    assert osp.l2_brief == payload.brief
    assert osp.l1_layout.model_dump() == layout_dict

    dump = osp.model_dump()
    reloaded = OptSearchPoint(**dump)

    assert reloaded.l2_brief == payload.brief
    assert reloaded.l1_layout.model_dump() == layout_dict


def test_sweep_payload_rejects_layout_missing_mandatory_placeholder() -> None:
    """Hard validator: every L1_MANDATORY placeholder must appear somewhere."""
    bad_layout = {
        "task_intent": ["l2_directive"],
        "problem_description": ["rendered_prompt"],  # missing pipeline_axes + plan
    }
    payload = SweepPayload(brief="x", l1_layout=bad_layout)
    osp = OptSearchPoint.from_prompt_fields({"persona": "p"})
    with pytest.raises(ValueError, match="hard validators"):
        apply_sweep_payload_to_osp(osp, payload)


# ===========================================================================
# L2 action channel — probe-round wire field + commitment decision
# ===========================================================================


def _l2_cycle_stub() -> types.SimpleNamespace:
    """Minimal Cycle attributes _apply_l2 reads."""
    from promptpotter.application.optimization.cycle import EscalationState

    return types.SimpleNamespace(
        opt_sp=_osp(),
        escalation=EscalationState(),
        pending_decisions=[],
        probe_next_round=False,
        last_l2_axis="",
        tracking=types.SimpleNamespace(best_accuracy=0.0, best_composite_fitness=0.0),
    )


def test_parse_l2_round_trips_probe_action_and_axis():
    raw = {
        "directive": "Probe persona axis using the warned subset.",
        "action": "probe_round",
        "axis_targeted": "persona",
        "rationale": "test",
    }
    result = _parse_l2(raw, _osp(), prompt="<prompt>")
    assert result.action == "probe_round"
    assert result.axis_targeted == "persona"


def test_parse_l2_invalid_action_defaults_to_normal_round():
    raw = {"directive": "x" * 200, "action": "garbage", "rationale": "test"}
    result = _parse_l2(raw, _osp(), prompt="<prompt>")
    assert result.action == "normal_round"


def test_apply_l2_probe_action_sets_cycle_state_and_records_commitment():
    cycle = _l2_cycle_stub()
    raw = {
        "directive": "Probe persona axis using the warned subset.",
        "action": "probe_round",
        "axis_targeted": "persona",
        "rationale": "test",
    }
    result = _parse_l2(raw, cycle.opt_sp, prompt="<prompt>")
    _apply_l2(cycle, result, round_num=3)

    assert cycle.probe_next_round is True
    assert cycle.last_l2_axis == "persona"
    last = cycle.pending_decisions[-1]
    assert last.kind == DecisionKind.PROBE_ROUND_COMMITMENT
    assert last.outcome is True
    assert last.data["action"] == "probe_round"
    assert last.data["axis_targeted"] == "persona"


def test_apply_l2_normal_action_records_commitment_without_probe_state():
    cycle = _l2_cycle_stub()
    raw = {"directive": "x" * 200, "action": "normal_round", "rationale": "test"}
    result = _parse_l2(raw, cycle.opt_sp, prompt="<prompt>")
    _apply_l2(cycle, result, round_num=3)

    assert cycle.probe_next_round is False
    last = cycle.pending_decisions[-1]
    assert last.kind == DecisionKind.PROBE_ROUND_COMMITMENT
    assert last.outcome is False
    assert last.data["action"] == "normal_round"
