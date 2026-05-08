"""Optimizer loop invariants — L1 detector, L2/L3 output validators, PoBB
elimination, layout validators, sweep payload round-trip.

Seven named invariants:
  1. L1 ``detect_invariants``: a candidate is a non-empty unique mutation
     of the parent OSP, else a ValidationFailure attaches → synth-0
     downstream. Idempotent under repeat calls; pipeline_params_override
     counts as mutation.
  2. L2 output validator ``L2_TASK_CONTEXT_VERBATIM_REPEAT`` fires when a
     non-empty proposed task_context refinement merges to a no-op
     against the prior OSP framing; ``run_l2_output_validators``
     aggregates.
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
  6. ``SweepPayload`` round-trips through ``OptSearchPoint``: ``l1_layout``
     dict survives ``model_dump`` → reload; mandatory layout placeholders
     enforced; extra keys rejected at parse.
  7. L2 ``action`` channel: ``probe_round`` round-trips through
     ``_parse_l2``; garbage values default to ``normal_round``;
     ``_apply_l2`` sets ``cycle.probe_next_round`` + records a
     ``PROBE_ROUND_COMMITMENT`` decision keyed on the action.
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from promptpotter.application.optimization.escalation import apply_sweep_payload_to_osp
from promptpotter.application.optimization.escalation.firing import (
    _apply_l2,
    _apply_l3,
    _parse_l2,
    _parse_l3,
)
from promptpotter.application.optimization.l1_validators import detect_invariants
from promptpotter.application.optimization.l2_validators import (
    L2_TASK_CONTEXT_VERBATIM_REPEAT,
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


def test_task_context_verbatim_repeat_fires_when_proposed_merge_is_no_op():
    """Non-empty L2 proposal that merges to None ⇒ verbatim-repeat outcome."""
    out = L2_TASK_CONTEXT_VERBATIM_REPEAT.run(
        {"task_context_proposed": {"domain": "biotech"}, "task_context_applied": None},
        opt_sp=_osp(),
    )
    assert out is not None
    assert out.passed is False
    assert out.score == 0.0
    assert out.nurse_target == "l3"
    assert out.evidence["proposed_keys"] == ["domain"]


def test_task_context_verbatim_repeat_skips_when_merge_applied():
    """A real applied refinement does not fire the validator."""
    sentinel = object()  # any non-None payload satisfies "applied"
    out = L2_TASK_CONTEXT_VERBATIM_REPEAT.run(
        {"task_context_proposed": {"domain": "biotech"}, "task_context_applied": sentinel},
        opt_sp=_osp(),
    )
    assert out is None


def test_task_context_verbatim_repeat_skips_when_no_proposal():
    """No proposed refinement at all ⇒ no verbatim-repeat fire."""
    out = L2_TASK_CONTEXT_VERBATIM_REPEAT.run(
        {"task_context_proposed": None, "task_context_applied": None},
        opt_sp=_osp(),
    )
    assert out is None


def test_run_l2_output_validators_aggregates_task_context_repeat():
    outcomes = run_l2_output_validators(
        {"task_context_proposed": {"domain": "x"}, "task_context_applied": None},
        _osp(),
    )
    ids = {o.validator_id for o in outcomes}
    assert "l2_task_context_verbatim_repeat" in ids


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
    bad = L1Layout(task_intent=["task_context"], problem_description=["rendered_prompt"])
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_missing_mandatory" in ids


def test_layout_unknown_placeholder_is_hard_failure():
    """Placeholder names must be in L1_POSSIBLE."""
    bad = L1Layout(
        task_intent=["task_context", "made_up_signal"],
        problem_description=["rendered_prompt", "pipeline_param_catalogue", "plan"],
    )
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_unknown_placeholder" in ids


def test_layout_duplicate_within_slot_is_hard_failure():
    """No slot may list the same placeholder twice."""
    bad = L1Layout(
        task_intent=["task_context", "task_context"],
        problem_description=["rendered_prompt", "pipeline_param_catalogue", "plan"],
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
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=10)
    check.register_completed([1.0] * 10, candidate_id="winner")
    check.set_current("loser")
    # 3 queries < n_min=4 — too early to fire even though signal is huge.
    sig = check.check([{"fitness": 0.0}] * 3, candidate_idx=1, n_total_candidates=2)
    assert sig is None


def test_pobb_check_high_signal_stops_inferior():
    """Loser candidate vs strong prior fires within ≤5 queries at ε=0.05."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
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
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8), n_samples=20
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
    payload = SweepPayload(reason="canonical case", l1_layout=layout_dict)

    osp = OptSearchPoint.from_prompt_fields({"persona": "p", "task_intent": "t"})
    apply_sweep_payload_to_osp(osp, payload)

    assert osp.l1_layout.model_dump() == layout_dict

    dump = osp.model_dump()
    reloaded = OptSearchPoint(**dump)

    assert reloaded.l1_layout.model_dump() == layout_dict


def test_sweep_payload_rejects_layout_missing_mandatory_placeholder() -> None:
    """Hard validator: every L1_MANDATORY placeholder must appear somewhere."""
    bad_layout = {
        "task_intent": ["task_context"],
        "problem_description": ["rendered_prompt"],  # missing pipeline_param_catalogue + plan
    }
    payload = SweepPayload(l1_layout=bad_layout)
    osp = OptSearchPoint.from_prompt_fields({"persona": "p"})
    with pytest.raises(ValueError, match="hard validators"):
        apply_sweep_payload_to_osp(osp, payload)


# ===========================================================================
# L2 action channel — probe-round wire field + commitment decision
# ===========================================================================


def _l2_cycle_stub() -> types.SimpleNamespace:
    """Minimal Cycle attributes _apply_l2 reads."""
    from promptpotter.application.optimization.escalation.state import EscalationState

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
        "action": "probe_round",
        "axis_targeted": "persona",
        "rationale": "test",
    }
    result = _parse_l2(raw, _osp(), prompt="<prompt>")
    assert result.action == "probe_round"
    assert result.axis_targeted == "persona"


def test_parse_l2_invalid_action_defaults_to_normal_round():
    raw = {"action": "garbage", "rationale": "test"}
    result = _parse_l2(raw, _osp(), prompt="<prompt>")
    assert result.action == "normal_round"


def test_apply_l2_probe_action_sets_cycle_state_and_records_commitment():
    cycle = _l2_cycle_stub()
    raw = {
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
    raw = {"action": "normal_round", "rationale": "test"}
    result = _parse_l2(raw, cycle.opt_sp, prompt="<prompt>")
    _apply_l2(cycle, result, round_num=3)

    assert cycle.probe_next_round is False
    last = cycle.pending_decisions[-1]
    assert last.kind == DecisionKind.PROBE_ROUND_COMMITMENT
    assert last.outcome is False
    assert last.data["action"] == "normal_round"
    assert last.data["task_context_changed"] is False


# ===========================================================================
# l3_to_l2_note — sticky L3→L2 channel, invisible to L1
# ===========================================================================


def test_l3_to_l2_note_is_in_signals_but_not_l1_possible():
    """Hard structural guard: the note channel is L2-only. Adding it to
    ``L1_POSSIBLE`` would let L2 leak L3's L2-private guidance to L1."""
    from promptpotter.application.optimization.dispatch_hub import SIGNALS
    from promptpotter.domain.l1_layout import L1_POSSIBLE

    assert "l3_to_l2_note" in SIGNALS
    assert "l3_to_l2_note" not in L1_POSSIBLE


def test_l3_note_is_memory_field_and_forwarded_via_copy_memory_to():
    """Stickiness across L2-fire OSP swaps: ``l3_note`` is in
    ``MEMORY_FIELDS`` so :meth:`copy_memory_to` forwards it onto the
    fresh OSP that ``_run_transition`` mints."""
    assert "l3_note" in OptSearchPoint.MEMORY_FIELDS

    osp = _osp(l3_note="constraint X discovered, steer L2 around it")
    target = _osp()
    osp.copy_memory_to(target)
    assert target.l3_note == osp.l3_note


def test_parse_l3_reads_note_from_raw_and_apply_replaces_on_osp():
    """L3 fire wholesale-replaces ``cycle.opt_sp.l3_note`` — even with an
    empty/missing ``note``, prior content is wiped. That's the contract:
    each L3 fire produces a complete (or null) note."""
    from promptpotter.application.optimization.escalation.state import EscalationState

    osp = _osp(plan="prior plan", l3_note="prior note")
    raw = {"plan": "x" * 200, "note": "new sticky pointer", "rationale": "test"}
    result = _parse_l3(raw, osp, prompt="<prompt>")
    assert result.l3_note == "new sticky pointer"

    # Mirror the order ``_run_transition`` applies: copy memory onto a
    # fresh OSP (carries the prior note), then apply L3 (overwrites).
    cycle = types.SimpleNamespace(
        opt_sp=result.opt_search_point,
        escalation=EscalationState(),
        tracking=types.SimpleNamespace(best_accuracy=0.0, best_composite_fitness=0.0),
    )
    osp.copy_memory_to(cycle.opt_sp)
    assert cycle.opt_sp.l3_note == "prior note"  # copy_memory_to forwarded
    _apply_l3(cycle, result, round_num=5)
    assert cycle.opt_sp.l3_note == "new sticky pointer"  # _apply_l3 replaced

    # And: missing note → wipe.
    raw_no_note = {"plan": "y" * 200, "rationale": "test"}
    result2 = _parse_l3(raw_no_note, _osp(plan="p", l3_note="something"), prompt="<prompt>")
    assert result2.l3_note == ""


# ===========================================================================
# Phase 0 — typed SIGNALS + load-time template validation
# ===========================================================================
#
# Closes the silent-drop bug: ``DispatchHub.fill_fixed`` skips template names
# not in ``SIGNALS``, so a typo would render to empty and never surface.
# ``validate_template`` raises at load time. Two tiny invariants:
#   1. every shipping optimizer prompt loads without raising (positive case);
#   2. a deliberate unknown slot raises ``KeyError`` (validator actually works).


def test_optimizer_prompts_load_with_no_unresolvable_slots():
    """Every shipping optimizer prompt's ``{{slot}}`` references resolve."""
    from promptpotter.application.optimization.llm_call import (
        list_optimizer_prompts,
        load_optimizer_prompt,
    )

    names = list_optimizer_prompts()
    assert names, "expected at least one optimizer prompt registered"
    for name in names:
        load_optimizer_prompt(name)  # raises KeyError on unknown slot


def test_validate_template_raises_on_unknown_slot():
    """Typo guard: a slot not in SIGNALS and not in _TEMPLATE_EXTRAS raises."""
    from promptpotter.application.optimization.dispatch_hub import validate_template
    from promptpotter.domain.opt_search_point import PromptTemplate

    bad = PromptTemplate(task_intent="see {{not_a_signal}}")
    with pytest.raises(KeyError, match="not_a_signal"):
        validate_template("l1_critique", bad)
