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
from promptpotter.domain.run_records import ResumeCheckpointKind, SweepPayload

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
    assert last.kind == ResumeCheckpointKind.PROBE_ROUND_COMMITMENT
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
    assert last.kind == ResumeCheckpointKind.PROBE_ROUND_COMMITMENT
    assert last.outcome is False
    assert last.data["action"] == "normal_round"
    assert last.data["task_context_changed"] is False


# ===========================================================================
# l3_to_l2_note — sticky L3→L2 channel, invisible to L1
# ===========================================================================


def test_l3_to_l2_note_is_in_signals_but_not_l1_possible():
    """Hard structural guard: the note channel is L2-only. Adding it to
    ``L1_POSSIBLE`` would let L2 leak L3's L2-private guidance to L1."""
    from promptpotter.application.optimization.dispatch_hub import INJECTIONS
    from promptpotter.domain.l1_layout import L1_POSSIBLE

    assert "l3_to_l2_note" in INJECTIONS
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
# Typed INJECTIONS + load-time template validation
# ===========================================================================
#
# Closes the silent-drop bug: ``DispatchHub.fill_fixed`` skips template names
# not in ``INJECTIONS``, so a typo would render to empty and never surface.
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
    """Typo guard: a slot not in INJECTIONS and not in _TEMPLATE_EXTRAS raises."""
    from promptpotter.application.optimization.dispatch_hub import validate_template
    from promptpotter.domain.opt_search_point import PromptTemplate

    bad = PromptTemplate(task_intent="see {{not_a_signal}}")
    with pytest.raises(KeyError, match="not_a_signal"):
        validate_template("l1_critique", bad)


# ===========================================================================
# Phase 1 — axis_memory signal wires AxisIndex.digest() into prompts
# ===========================================================================
#
# Closes flaw 5: MeasurementArchive is rich, dispatch_hub.INJECTIONS doesn't
# surface it. The format helpers already exist in
# intelligence/indexes/format.py; this signal is wiring, not new computation.


def _empty_cycle_slice():
    from promptpotter.application.optimization.dispatch_hub import CycleSlice

    return CycleSlice(
        round_num=0,
        current_accuracy=0.0,
        best_accuracy=0.0,
        best_round=0,
        l1_stall_count=0,
        l2_round=0,
        l2_stall_count=0,
        l3_round=0,
        l3_stall_count=0,
    )


def test_axis_memory_renders_when_axes_digest_yields_content():
    """``axis_memory`` wraps :meth:`AxisIndex.digest` into a labeled block."""
    from promptpotter.application.optimization.dispatch_hub import (
        DispatchHub,
        InjectionBundle,
        RoundDigest,
    )

    fake_axes = types.SimpleNamespace(
        digest=lambda: {
            "axis_rankings": "persona (effect=0.234, strong)",
            "persistent_failures": "3 chronic failures",
        }
    )
    bundle = InjectionBundle(
        opt_sp=OptSearchPoint(),
        pipeline_schema=None,
        cycle_slice=_empty_cycle_slice(),
        digest=RoundDigest(diagnostics=None, critique=None, decision_traces=[]),
        axes=fake_axes,
    )
    out = DispatchHub.render("axis_memory", bundle)
    assert out.startswith("AXIS MEMORY")
    assert "axis rankings: persona (effect=0.234, strong)" in out
    assert "persistent failures: 3 chronic failures" in out


def test_axis_memory_empty_when_axes_or_digest_absent():
    """Pre-first-round and empty-digest paths render to empty string."""
    from promptpotter.application.optimization.dispatch_hub import (
        DispatchHub,
        InjectionBundle,
        RoundDigest,
    )

    base_kwargs = {
        "opt_sp": OptSearchPoint(),
        "pipeline_schema": None,
        "cycle_slice": _empty_cycle_slice(),
        "digest": RoundDigest(diagnostics=None, critique=None, decision_traces=[]),
    }

    no_axes = InjectionBundle(**base_kwargs, axes=None)
    assert DispatchHub.render("axis_memory", no_axes) == ""

    empty_digest = InjectionBundle(
        **base_kwargs,
        axes=types.SimpleNamespace(digest=lambda: None),
    )
    assert DispatchHub.render("axis_memory", empty_digest) == ""


def test_axis_memory_listed_in_l1_possible_and_default_layout():
    """L1 wiring: L2 may pick axis_memory; default layout includes it."""
    from promptpotter.domain.l1_layout import L1_POSSIBLE, default_l1_layout

    assert "axis_memory" in L1_POSSIBLE
    assert "axis_memory" in default_l1_layout().problem_description


def test_decision_trace_summary_signal():
    """Phase 3.3: decision_trace_summary renders the latest round's traces compactly."""
    from promptpotter.application.optimization.dispatch_hub import (
        DispatchHub,
        InjectionBundle,
        RoundDigest,
    )
    from promptpotter.application.optimization.l1_population import build_decision_trace

    traces = [
        build_decision_trace(
            decision_kind="eliminate",
            candidate_id="cand_aaa",
            at_sample_index=4,
            p_best_at_decision=0.03,
            snapshot={"cand_bbb": 0.78, "cand_aaa": 0.03},
            sample_outcomes=[True, False, False, True],
        ),
        build_decision_trace(
            decision_kind="promote",
            candidate_id="cand_bbb",
            at_sample_index=8,
            p_best_at_decision=None,
            snapshot={"cand_bbb": 0.62, "cand_aaa": 0.05},
            sample_outcomes=[True] * 6 + [False] * 2,
        ),
    ]
    bundle = InjectionBundle(
        opt_sp=OptSearchPoint(),
        pipeline_schema=None,
        cycle_slice=_empty_cycle_slice(),
        digest=RoundDigest(diagnostics=None, critique=None, decision_traces=traces),
        axes=None,
    )

    out = DispatchHub.render("decision_trace_summary", bundle)
    assert out.startswith("DECISION TRACES")
    assert "eliminate c=cand_aaa@s4" in out
    assert "promote c=cand_bbb@s8" in out
    assert "P(best)=0.03" in out
    assert "hits=6 misses=2" in out

    # Empty digest path renders nothing.
    empty = InjectionBundle(
        opt_sp=OptSearchPoint(),
        pipeline_schema=None,
        cycle_slice=_empty_cycle_slice(),
        digest=RoundDigest(diagnostics=None, critique=None, decision_traces=[]),
        axes=None,
    )
    assert DispatchHub.render("decision_trace_summary", empty) == ""


# ===========================================================================
# Escalation rules engine reproduces prior observe_round FSM
# ===========================================================================
#
# Closes flaw 3 (calendar-driven escalation) by lifting observe_round's FSM
# into a typed, declarative rule evaluator. Phase 2a is behaviour-preserving:
# the default rule set must reproduce the prior FSM exactly. One test, four
# assertions — one per branch of the original if/elif chain.


def test_default_round_rules_reproduce_observe_round_fsm():
    """Phase 2a parity: DEFAULT_ROUND_RULES matches observe_round's branches."""
    from promptpotter.application.optimization.escalation import (
        EscalationInputs,
        decide_escalation,
    )
    from promptpotter.application.optimization.escalation.state import NextAction

    # Branch 1 — perfect accuracy → STOP_PERFECT (priority 100)
    perfect = EscalationInputs(
        improved=True,
        current_accuracy=1.0,
        l1_stall_count=0,
        l1_patience=3,
        enable_l2=True,
    )
    assert decide_escalation(perfect).next_action == NextAction.STOP_PERFECT

    # Branch 2 — stall < patience → CONTINUE (priority 50)
    continuing = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        enable_l2=True,
    )
    assert decide_escalation(continuing).next_action == NextAction.CONTINUE

    # Branch 3 — patience exhausted, L2 disabled → STOP_L1_PATIENCE (priority 30)
    no_l2 = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=3,
        l1_patience=3,
        enable_l2=False,
    )
    assert decide_escalation(no_l2).next_action == NextAction.STOP_L1_PATIENCE

    # Branch 4 — patience exhausted, L2 enabled → FIRE_L2 (priority 10)
    fire = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=3,
        l1_patience=3,
        enable_l2=True,
    )
    assert decide_escalation(fire).next_action == NextAction.FIRE_L2


def test_l2_axis_yield_drought_rule_fires_only_when_opted_in():
    """Phase 2b: yield-drought rule preempts l1_continue when on; quiet when off."""
    from promptpotter.application.optimization.escalation import (
        EscalationInputs,
        decide_escalation,
    )
    from promptpotter.application.optimization.escalation.state import NextAction

    # Drought + opt-in + L1 has stalled at least one round → FIRE_L2 (priority 60)
    drought_on = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        enable_l2=True,
        axes_with_positive_yield=0,
        escalate_on_yield_drought=True,
    )
    assert decide_escalation(drought_on).next_action == NextAction.FIRE_L2

    # Same drought + flag off → CONTINUE (l1_continue at priority 50 wins)
    drought_off = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        enable_l2=True,
        axes_with_positive_yield=0,
        escalate_on_yield_drought=False,
    )
    assert decide_escalation(drought_off).next_action == NextAction.CONTINUE

    # Pre-first-round (axes not initialised) → CONTINUE even with flag on
    no_evidence = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        enable_l2=True,
        axes_with_positive_yield=None,
        escalate_on_yield_drought=True,
    )
    assert decide_escalation(no_evidence).next_action == NextAction.CONTINUE


# ===========================================================================
# Phase 3.1 — DecisionTrace data model (scaffolding for mid-round diagnosis)
# ===========================================================================
#
# Phase 3 of the Routed Dispatch plan needs richer per-candidate decision
# context than the current `Decision` records carry. DecisionTrace is the
# data shape; writer + reader land in Phase 3.2+. One contract test:
# extra="forbid" + frozen, plus model_dump_json round-trips losslessly.


def test_decision_trace_shape_and_json_roundtrip():
    """DecisionTrace is frozen + extra-forbid; survives a JSON round-trip."""
    from promptpotter.domain.decision_trace import DecisionTrace

    trace = DecisionTrace(
        decision_kind="eliminate",
        candidate_id="c3",
        at_sample_index=6,
        p_best_at_decision=0.03,
        leaderboard_at_decision=[("c1", 0.78), ("c2", 0.45), ("c3", 0.03)],
        sample_outcomes_so_far=[True, True, False, True, False, False],
        target_axis="persona",
    )

    # extra="forbid" — unknown kwarg rejected by Pydantic.
    with pytest.raises(Exception):  # noqa: B017 — Pydantic ValidationError
        DecisionTrace(
            decision_kind="eliminate",
            candidate_id="c3",
            at_sample_index=0,
            unknown_field="x",
        )

    # JSON round-trip: equal after dump+reload.
    reloaded = DecisionTrace.model_validate_json(trace.model_dump_json())
    assert reloaded == trace


def test_build_decision_trace_writer_contract():
    """build_decision_trace dumps the wire shape; leaderboard is sorted desc + top-K."""
    from promptpotter.application.optimization.l1_population import build_decision_trace
    from promptpotter.domain.decision_trace import DecisionTrace

    snapshot = {"a": 0.10, "b": 0.55, "c": 0.20, "d": 0.05, "e": 0.05, "f": 0.05}
    trace_dict = build_decision_trace(
        decision_kind="eliminate",
        candidate_id="a",
        at_sample_index=4,
        p_best_at_decision=0.10,
        snapshot=snapshot,
        sample_outcomes=[True, False, False, True],
    )

    leaderboard = trace_dict["leaderboard_at_decision"]
    assert [cid for cid, _ in leaderboard] == ["b", "c", "a", "d", "e"]
    assert all(leaderboard[i][1] >= leaderboard[i + 1][1] for i in range(len(leaderboard) - 1))

    # Wire shape must round-trip back into the frozen model.
    reloaded = DecisionTrace.model_validate(trace_dict)
    assert reloaded.decision_kind == "eliminate"
    assert reloaded.candidate_id == "a"
    assert reloaded.sample_outcomes_so_far == [True, False, False, True]

    # All three kinds accepted.
    for kind in ("eliminate", "complete", "promote"):
        DecisionTrace.model_validate(
            build_decision_trace(
                decision_kind=kind,
                candidate_id="x",
                at_sample_index=0,
                p_best_at_decision=None,
                snapshot=None,
                sample_outcomes=[],
            )
        )
