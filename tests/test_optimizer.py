"""Optimizer loop invariants — L1 detector, L2/L3 output validators, PoBB, layout, sweep round-trip."""

from __future__ import annotations

import types

import numpy as np
import pytest

from promptpotter.application.optimization.dispatch.schemas import (
    L2ContextOutput,
    L3PlanOutput,
)
from promptpotter.application.optimization.escalation import apply_fork_payload_to_osp
from promptpotter.application.optimization.escalation.firing import (
    _apply_l2,
    _apply_l3,
    _parse_l2,
    _parse_l3,
)
from promptpotter.application.optimization.validators.l1_strict import detect_invariants
from promptpotter.application.optimization.validators.l2_output import (
    L2_DUPLICATE_INSERT,
    L2_TASK_CONTEXT_PARAPHRASE_REPEAT,
    L2_TASK_CONTEXT_VERBATIM_REPEAT,
    run_l2_output_validators,
)
from promptpotter.application.optimization.validators.l3_output import (
    L3_PLAN_LENGTH_FLOOR,
    L3_PLAN_VERBATIM_REPEAT,
    run_l3_output_validators,
)
from promptpotter.domain.l1_layout import L1Layout, default_l1_layout, validate_l1_layout
from promptpotter.domain.opt_search_point import OptSearchPoint, WoundChannels
from promptpotter.domain.results import CandidateProposal, CandidateScore
from promptpotter.domain.run_records import ForkPayload, ForkTrigger, ResumeCheckpointKind
from promptpotter.domain.search_point import TaskDecomposition

scipy = pytest.importorskip("scipy")  # transitively required by other math helpers

from promptpotter.application.optimization.l1.score.winner import (  # noqa: E402
    is_leader_eligible,
)
from promptpotter.application.optimization.pobb.elimination import (  # noqa: E402
    PoBBCheck,
    PoBBConfig,
)
from promptpotter.domain.escalation_signals import EscalationTarget  # noqa: E402
from promptpotter.shared.statistics import posterior_best_probabilities  # noqa: E402


def _parent() -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.")


def _child(parent: OptSearchPoint, **changes) -> CandidateProposal:
    return CandidateProposal(osp=parent.mutate(**changes))


def test_no_op_clone_attaches_validation_failure():
    parent = _parent()
    proposals = [_child(parent), _child(parent, persona="Expert ranker")]

    stats = detect_invariants(proposals, parent)

    no_op_reasons = [vf.reason for vf in proposals[0].osp.wounds.validation_failures]
    assert "no_op_variant" in no_op_reasons
    assert proposals[1].osp.wounds.validation_failures == []
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

    assert proposals[0].osp.wounds.validation_failures == []
    dup_reasons = [vf.reason for vf in proposals[1].osp.wounds.validation_failures]
    assert "duplicate_variant" in dup_reasons
    assert proposals[2].osp.wounds.validation_failures == []
    assert stats.l1_n_duplicate == 1
    assert stats.l1_n_no_op == 0
    assert stats.l1_yield == 2 / 3


def test_parse_population_attaches_forbidden_axis_failure_to_osp():
    from promptpotter.application.optimization.l1.population import parse_population
    from promptpotter.domain.pipeline_schema import (
        NodePromptMeta,
        PipelineNode,
        PipelineSchema,
    )

    schema = PipelineSchema(
        name="test",
        available_models=["openai/gpt-oss-120b"],
        nodes=[PipelineNode(name="llm_only", prompt_meta=NodePromptMeta())],
    )
    parent = _parent()
    proposal = CandidateProposal(
        osp=parent.mutate(persona="Strict"),
        pipeline_params_override={"llm_only": {"model": "anything-at-all"}},
    )

    osp_list, _ = parse_population([proposal], pipeline_params=None, schema=schema)

    failures = osp_list[0].wounds.validation_failures
    assert len(failures) == 1
    assert failures[0].reason == "forbidden_axis"
    assert failures[0].axis == "llm_only.model"


def _osp(**kwargs) -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.", **kwargs)


def test_task_context_verbatim_repeat_fires_when_proposed_merge_is_no_op():
    out = L2_TASK_CONTEXT_VERBATIM_REPEAT.run(
        {"task_context_proposed": {"domain": "biotech"}, "task_context_applied": None},
        opt_sp=_osp(),
    )
    assert out is not None
    assert out.passed is False
    assert out.score == 0.0
    assert out.nurse_target == "l3"
    assert out.evidence["proposed_keys"] == ["domain"]


def test_run_l2_output_validators_aggregates_task_context_repeat():
    outcomes = run_l2_output_validators(
        {"task_context_proposed": {"domain": "x"}, "task_context_applied": None},
        _osp(),
    )
    ids = {o.validator_id for o in outcomes}
    assert "l2_task_context_verbatim_repeat" in ids


def test_duplicate_insert_fires_when_proposed_lines_already_in_prior_framing():
    osp = _osp(
        task_context=TaskDecomposition(
            domain="math",
            key_challenges=(
                "Step 1: read the problem\nStep 2: compute carefully\nStep 3: verify the result"
            ),
        )
    )
    proposed = {
        "key_challenges": (
            "Step 1: read the problem\n"
            "Step 2: compute carefully\n"
            "Step 3: verify the result\n"
            "Step 4: emit the answer"  # genuinely new — merge succeeds
        ),
    }
    merged = osp.task_context.merge(proposed)
    out = L2_DUPLICATE_INSERT.run(
        {"task_context_proposed": proposed, "task_context_applied": merged},
        opt_sp=osp,
    )
    assert out is not None
    assert out.passed is False
    assert out.score == 0.0
    assert out.nurse_target == "l3"
    assert out.evidence["duplicate_lines"] == 3
    assert out.evidence["fields"] == ["key_challenges"]


def test_duplicate_insert_quiet_below_threshold():
    """Below the 3-line floor ⇒ no fire."""
    osp = _osp(task_context=TaskDecomposition(key_challenges="line A\nline B"))
    out = L2_DUPLICATE_INSERT.run(
        {
            "task_context_proposed": {"key_challenges": "line A\nline B\nline C"},
            "task_context_applied": osp.task_context.merge(
                {"key_challenges": "line A\nline B\nline C"}
            ),
        },
        opt_sp=osp,
    )
    assert out is None


def test_paraphrase_repeat_fires_on_word_set_overlap_above_threshold():
    """Paraphrase sharing ≥50% of words with prior framing ⇒ fire."""
    prior = (
        "Problem misreading (2/N): targeting L1 axis 'instruction' to add "
        "a mandatory problem-restate and constraint-check step before solving."
    )
    paraphrase = (
        "Problem misreading (2/N): targeting L1 axis 'instruction' to add "
        "an explicit problem restatement followed by a constraint extraction "
        "and self-check step before any solving begins."
    )
    osp = _osp(task_context=TaskDecomposition(key_challenges=prior))
    out = L2_TASK_CONTEXT_PARAPHRASE_REPEAT.run(
        {
            "task_context_proposed": {"key_challenges": paraphrase},
            "task_context_applied": osp.task_context.merge({"key_challenges": paraphrase}),
        },
        opt_sp=osp,
    )
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l3"
    assert out.evidence["field"] == "key_challenges"
    assert out.evidence["jaccard"] >= 0.5


def test_paraphrase_repeat_quiet_on_genuine_refinement():
    prior = "Problem misreading on geometry: failing coordinate setup."
    refinement = (
        "Combinatorial enumeration failing: candidates skip casework on "
        "the 27-cell grid; needs explicit decomposition by row pattern."
    )
    osp = _osp(task_context=TaskDecomposition(key_challenges=prior))
    out = L2_TASK_CONTEXT_PARAPHRASE_REPEAT.run(
        {
            "task_context_proposed": {"key_challenges": refinement},
            "task_context_applied": osp.task_context.merge({"key_challenges": refinement}),
        },
        opt_sp=osp,
    )
    assert out is None


def test_l1_config_not_in_runtime_failures_fires_on_reproposal():
    """L1 proposing a (param, value) matching a known runtime failure ⇒ ValidationFailure."""
    from promptpotter.application.optimization.validators.l1_strict import (
        L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    )
    from promptpotter.domain.escalation_signals import RuntimeFailure

    osp = _osp()
    osp.wounds.runtime_failures = [
        RuntimeFailure(
            source="degradation_check",
            dominant_warning="llm_only:empty_response",
            warning_types={"llm_only:empty_response": 6},
            degraded_rate=1.0,
            degraded_count=6,
            total_scored=6,
            observed_config={"max_tokens": 1800, "temperature": 0.7},
            first_seen_round=1,
        )
    ]
    out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run(
        {"llm_only": {"max_tokens": 1800}},
        opt_sp=osp,
    )
    assert out is not None
    assert out.passed is False
    assert out.nurse_target == "l2"
    failures = out.evidence["failures"]
    assert len(failures) == 1
    assert failures[0].axis == "llm_only.max_tokens"
    assert failures[0].reason == "reproposes_known_failing_config"


def test_l1_config_not_in_runtime_failures_quiet_on_novel_config():
    """Novel (param, value) ⇒ no fire."""
    from promptpotter.application.optimization.validators.l1_strict import (
        L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    )
    from promptpotter.domain.escalation_signals import RuntimeFailure

    osp = _osp()
    osp.wounds.runtime_failures = [
        RuntimeFailure(
            source="degradation_check",
            dominant_warning="llm_only:empty_response",
            warning_types={"llm_only:empty_response": 3},
            degraded_rate=1.0,
            degraded_count=3,
            total_scored=3,
            observed_config={"max_tokens": 1800},
            first_seen_round=1,
        )
    ]
    out = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.run(
        {"llm_only": {"max_tokens": 2400}},
        opt_sp=osp,
    )
    assert out is None


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


def test_layout_missing_mandatory_placeholder_is_hard_failure():
    bad = L1Layout(task_intent=["task_context"], problem_description=["rendered_prompt"])
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_missing_mandatory" in ids


def test_layout_unknown_placeholder_is_hard_failure():
    bad = L1Layout(
        task_intent=["task_context", "made_up_signal"],
        problem_description=["rendered_prompt", "pipeline_param_catalogue", "plan"],
    )
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_unknown_placeholder" in ids


def test_layout_duplicate_within_slot_is_hard_failure():
    bad = L1Layout(
        task_intent=["task_context", "task_context"],
        problem_description=["rendered_prompt", "pipeline_param_catalogue", "plan"],
    )
    result = validate_l1_layout(bad)
    assert result.is_valid is False
    ids = {o.validator_id for o in result.outcomes}
    assert "l1_layout_dups_within_slot" in ids


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
    rng = np.random.default_rng(0)
    histories = {
        "winner": [1.0] * 8,
        "loser": [0.0] * 8,
    }
    probs = posterior_best_probabilities(histories, n_samples=2000, rng=rng)
    assert probs["winner"] >= 0.99
    assert probs["loser"] <= 0.01


def _measurements(scores: list[float], sample_ids: list[int] | None = None) -> list[dict]:
    """Minimal QueryMeasurement-shaped dicts for PoBB paired tests."""
    ids = sample_ids if sample_ids is not None else list(range(len(scores)))
    return [{"sample_id": sid, "fitness": float(s)} for sid, s in zip(ids, scores, strict=True)]


_DUMMY_SP = types.SimpleNamespace()


def test_pobb_check_gates_elimination_on_posterior_and_separability():
    """PoBB.check() must clear BOTH posterior AND separability gates."""
    # Part 1 — separated arms, posterior gate fires
    check_sep = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
    check_sep.register_completed(_measurements([1.0] * 20), candidate_id="winner", sp=_DUMMY_SP)
    check_sep.set_current("loser")
    sig = check_sep.check(_measurements([0.0] * 5), candidate_idx=1, n_total_candidates=2)
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "winner"
    assert cr["p_best"] < 0.05
    assert "p_best_snapshot" in cr
    assert cr["epsilon"] == pytest.approx(0.05)

    # Part 2 — overlapping CIs, floor refuses elimination
    check_noise = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.20), n_samples=20)
    leader_scores = [1.0, 0.0, 1.0, 0.0] + [1.0] * 9 + [0.0] * 7
    check_noise.register_completed(
        _measurements(leader_scores, sample_ids=list(range(20))),
        candidate_id="leader",
        sp=_DUMMY_SP,
    )
    # Disarm the dominance gate (cand_max = 0 + 16 = 16 ≥ leader's 11).
    check_noise.set_sample_universe(list(range(20)))
    check_noise.set_current("equivalent_candidate")
    sig_noise = check_noise.check(
        _measurements([0.0, 0.0, 0.0, 0.0], sample_ids=[0, 1, 2, 3]),
        candidate_idx=1,
        n_total_candidates=6,
    )
    assert sig_noise is None, (
        "Wilson floor must refuse elimination at 0/4 vs 2/4 — CIs overlap, "
        "gap is binomial noise. Got eliminated."
    )


def test_pobb_dominance_aborts_when_catch_up_impossible():
    """Arithmetic abort: cand_max_hits < seed_hits ⇒ eliminate before the posterior runs."""
    check = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
    seed_scores = [1.0] * 10 + [0.0] * 10
    check.register_completed(
        _measurements(seed_scores, sample_ids=list(range(20))),
        candidate_id="origin",
        sp=_DUMMY_SP,
    )
    check.set_sample_universe(list(range(20)))
    check.set_current("doomed")
    sig = check.check(
        _measurements([0.0] * 11, sample_ids=list(range(11))),
        candidate_idx=1,
        n_total_candidates=3,
    )
    assert sig is not None
    assert sig.check_name == "elimination"
    cr = sig.check_result
    assert cr["leader_id"] == "origin"
    dom = cr["dominance"]
    assert dom["cand_hits"] == 0
    assert dom["cand_max_hits"] == 9
    assert dom["seed_total_hits"] == 10
    assert dom["budget"] == 20


def test_pobb_locks_in_dominant_leader():
    """Current candidate dominating prior past lock_in_n_min fires LEADER_LOCKED."""
    check = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05, lock_in=0.95, lock_in_n_min=8), n_samples=20
    )
    check.register_completed(_measurements([0.0] * 20), candidate_id="weak_prior", sp=_DUMMY_SP)
    check.set_current("strong_current")
    sig = check.check(_measurements([1.0] * 8), candidate_idx=1, n_total_candidates=3)
    assert sig is not None
    assert sig.target == EscalationTarget.LEADER_LOCKED
    cr = sig.check_result
    assert cr["leader_id"] == "weak_prior"
    assert cr["p_best"] >= 0.95
    assert cr["queries_scored"] == 8
    # Two candidates remain unscored (idx=1 of 3).
    assert sig.candidates_skipped == 1


@pytest.mark.asyncio
async def test_paired_pobb_breaks_lucky_prefix_leader_trap():
    """Lucky-prefix 100% leader must NOT auto-eliminate a candidate measured on hard samples.

    Lucky-prefix pathology in 20 lines: round 1 leader-locked at 8/8 on
    samples {0..7} (easy prefix from the hard-sample sorter). Round 2
    candidate sees the sorter's HARD subset {9, 12, 13, 14, 8} — samples
    the leader was never scored on. Under unpaired PoBB the leader's
    [1]*8 vector would flatten the candidate's [0]*5 to p_best ≈ 0 and
    the candidate would be unfairly eliminated — the optimizer can
    never escape the false-100% floor.

    Paired PoBB fixes this by (a) excluding priors that don't cover the
    candidate's sample set when no backfill is wired, and (b) backfilling
    the leader on missing samples when a backfill_fn IS wired. Both branches
    must restore fair comparison.
    """
    leader_samples = [0, 1, 2, 3, 4, 5, 6, 7]
    candidate_samples = [9, 12, 13, 14, 8]  # disjoint from leader; AIME's hard sorter order.

    # --- Branch (a): no backfill_fn ⇒ incomplete prior is excluded, no elimination.
    check_no_backfill = PoBBCheck(PoBBConfig(n_min=4, epsilon=0.05), n_samples=20)
    check_no_backfill.register_completed(
        _measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    check_no_backfill.set_current("R2_challenger")
    sig = check_no_backfill.check(
        _measurements([0.0] * 5, sample_ids=candidate_samples),
        candidate_idx=0,
        n_total_candidates=6,
    )
    assert sig is None, (
        "lucky-prefix leader incomplete on candidate's hard samples must NOT cause "
        "elimination — old unpaired code would have unfairly fired here"
    )

    # --- Branch (b): backfill_fn returns honest leader scores on the hard set ⇒ paired
    # comparison shows the leader is not actually 100% (it misses 4/5), and the
    # candidate (0/5) is no longer overwhelmingly dominated — no elimination.
    backfill_calls: list[tuple[int, ...]] = []

    async def _stub_backfill(_sp, samples):
        backfill_calls.append(tuple(s.id for s in samples))
        # Leader misses 4/5 hard samples; only #13 is hit. Mirrors origin's
        # behavior on the same samples in the real cycle.
        truth = {9: 0.0, 12: 0.0, 13: 1.0, 14: 0.0, 8: 0.0}
        return [{"sample_id": s.id, "fitness": truth[s.id]} for s in samples]

    check_paired = PoBBCheck(
        PoBBConfig(n_min=4, epsilon=0.05),
        n_samples=20,
        backfill_fn=_stub_backfill,
    )
    check_paired.register_completed(
        _measurements([1.0] * 8, sample_ids=leader_samples),
        candidate_id="R1_lucky_winner",
        sp=_DUMMY_SP,
    )
    for sid in candidate_samples:
        await check_paired.backfill_for_sample(types.SimpleNamespace(id=sid))

    assert backfill_calls == [(9,), (12,), (13,), (14,), (8,)]
    leader_paired = check_paired.priors_by_sample["R1_lucky_winner"]
    assert all(str(sid) in leader_paired for sid in candidate_samples)

    check_paired.set_current("R2_challenger")
    sig = check_paired.check(
        _measurements([0.0] * 5, sample_ids=candidate_samples),
        candidate_idx=0,
        n_total_candidates=6,
    )
    # Leader honest mean 1/5; candidate 0/5 — gap small ⇒ p_best must stay ≥ ε=0.05.
    if sig is not None:
        assert sig.check_result["p_best"] >= 0.05


def _cs(
    *,
    candidate_id: str,
    accuracy: float,
    escalation_aborted: bool = False,
    elimination_stopped: bool = False,
    degradation_context: dict | None = None,
) -> CandidateScore:
    return CandidateScore(
        candidate_id=candidate_id,
        label=candidate_id,
        changes_description="",
        accuracy=accuracy,
        composite_fitness=accuracy,
        hits=int(accuracy * 20),
        total=20,
        evaluators={},
        escalation_aborted=escalation_aborted,
        elimination_stopped=elimination_stopped,
        degradation_context=degradation_context or {},
    )


def test_leader_eligibility_excludes_fatal_degradation_and_keeps_pobb_eliminated():
    """Winner-selection: fatal degradation disqualifies; PoBB elimination does not."""
    fatal = _cs(
        candidate_id="C1.3",
        accuracy=0.8333,
        escalation_aborted=True,
        elimination_stopped=True,
        degradation_context={"fatal": True, "dominant_warning": "llm_only:empty_response"},
    )
    pobb_eliminated = _cs(
        candidate_id="C1.2",
        accuracy=0.40,
        escalation_aborted=True,
        elimination_stopped=True,
        degradation_context={},
    )
    clean_loser = _cs(candidate_id="C1.4", accuracy=0.45)

    assert not is_leader_eligible(fatal)
    assert is_leader_eligible(pobb_eliminated)
    assert is_leader_eligible(clean_loser)


def _full_layout_dict() -> dict[str, list[str]]:
    return default_l1_layout().model_dump()


def _operator_sweep_payload(**kwargs: object) -> ForkPayload:
    return ForkPayload(
        trigger=ForkTrigger.OPERATOR_SWEEP,
        reason=str(kwargs.pop("reason", "canonical case")),
        issued_by=str(kwargs.pop("issued_by", "test-operator")),
        **kwargs,  # type: ignore[arg-type]
    )


def test_fork_payload_roundtrips_through_opt_search_point() -> None:
    layout_dict = _full_layout_dict()
    payload = _operator_sweep_payload(l1_layout=layout_dict)

    osp = OptSearchPoint.from_prompt_fields({"persona": "p", "task_intent": "t"})
    apply_fork_payload_to_osp(osp, payload)

    assert osp.l1_layout.model_dump() == layout_dict

    dump = osp.model_dump()
    reloaded = OptSearchPoint(**dump)

    assert reloaded.l1_layout.model_dump() == layout_dict


def test_fork_payload_rejects_layout_missing_mandatory_placeholder() -> None:
    """Every L1_MANDATORY placeholder must appear somewhere."""
    bad_layout = {
        "task_intent": ["task_context"],
        "problem_description": ["rendered_prompt"],
    }
    payload = _operator_sweep_payload(l1_layout=bad_layout)
    osp = OptSearchPoint.from_prompt_fields({"persona": "p"})
    with pytest.raises(ValueError, match="hard validators"):
        apply_fork_payload_to_osp(osp, payload)


def _l2_cycle_stub() -> types.SimpleNamespace:
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
    raw = L2ContextOutput(action="probe_round", axis_targeted="persona", rationale="test")
    result = _parse_l2(raw, _osp(), prompt="<prompt>")
    assert result.action == "probe_round"
    assert result.axis_targeted == "persona"


def test_apply_l2_probe_action_sets_cycle_state_and_records_commitment():
    cycle = _l2_cycle_stub()
    raw = L2ContextOutput(action="probe_round", axis_targeted="persona", rationale="test")
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
    raw = L2ContextOutput(action="normal_round", rationale="test")
    result = _parse_l2(raw, cycle.opt_sp, prompt="<prompt>")
    _apply_l2(cycle, result, round_num=3)

    assert cycle.probe_next_round is False
    last = cycle.pending_decisions[-1]
    assert last.kind == ResumeCheckpointKind.PROBE_ROUND_COMMITMENT
    assert last.outcome is False
    assert last.data["action"] == "normal_round"
    assert last.data["task_context_changed"] is False


def test_l3_to_l2_note_is_in_signals_but_not_l1_possible():
    """The L3→L2 note channel is L2-only; must not appear in ``L1_POSSIBLE``."""
    from promptpotter.application.optimization.dispatch.hub import INJECTIONS
    from promptpotter.domain.l1_layout import L1_POSSIBLE

    assert "l3_to_l2_note" in INJECTIONS
    assert "l3_to_l2_note" not in L1_POSSIBLE


def test_l3_note_is_memory_field_and_forwarded_via_copy_memory_to():
    """``l3_note`` rides ``MEMORY_FIELDS`` so it survives the L2-fire OSP swap."""
    assert "wounds" in OptSearchPoint.MEMORY_FIELDS

    osp = _osp(wounds=WoundChannels(l3_note="constraint X discovered, steer L2 around it"))
    target = _osp()
    osp.copy_memory_to(target)
    assert target.wounds.l3_note == osp.wounds.l3_note


def test_parse_l3_reads_note_from_raw_and_apply_replaces_on_osp():
    """L3 fire wholesale-replaces ``cycle.opt_sp.l3_note`` — even with an
    empty/missing ``note``, prior content is wiped. That's the contract:
    each L3 fire produces a complete (or null) note."""
    from promptpotter.application.optimization.escalation.state import EscalationState

    osp = _osp(plan="prior plan", wounds=WoundChannels(l3_note="prior note"))
    raw = L3PlanOutput(plan="x" * 200, note="new sticky pointer", rationale="test")
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
    assert cycle.opt_sp.wounds.l3_note == "prior note"  # copy_memory_to forwarded
    _apply_l3(cycle, result, round_num=5)
    assert cycle.opt_sp.wounds.l3_note == "new sticky pointer"  # _apply_l3 replaced

    # And: missing note → wipe.
    raw_no_note = L3PlanOutput(plan="y" * 200, rationale="test")
    result2 = _parse_l3(
        raw_no_note,
        _osp(plan="p", wounds=WoundChannels(l3_note="something")),
        prompt="<prompt>",
    )
    assert result2.l3_note == ""


# Typed INJECTIONS + load-time template validation: every prompt loads;
# unknown slot raises KeyError.


def test_optimizer_prompts_load_with_no_unresolvable_slots():
    from promptpotter.application.optimization.dispatch.llm_call import (
        list_optimizer_prompts,
        load_optimizer_prompt,
    )

    names = list_optimizer_prompts()
    assert names, "expected at least one optimizer prompt registered"
    for name in names:
        load_optimizer_prompt(name)  # raises KeyError on unknown slot


def test_validate_template_raises_on_unknown_slot():
    from promptpotter.application.optimization.dispatch.hub import validate_template
    from promptpotter.domain.opt_search_point import PromptTemplate

    bad = PromptTemplate(task_intent="see {{not_a_signal}}")
    with pytest.raises(KeyError, match="not_a_signal"):
        validate_template("l1_critique", bad)


# axis_memory signal: AxisIndex.digest() wired into prompts via dispatch_hub.INJECTIONS.


def _empty_cycle_slice():
    from promptpotter.application.optimization.dispatch.hub import CycleSlice

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
    from promptpotter.application.optimization.dispatch.hub import (
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
        digest=RoundDigest(diagnostics=None, critique=None),
        axes=fake_axes,
    )
    out = DispatchHub.render("axis_memory", bundle)
    assert out.startswith("AXIS MEMORY")
    assert "axis rankings: persona (effect=0.234, strong)" in out
    assert "persistent failures: 3 chronic failures" in out


def test_axis_memory_empty_when_axes_or_digest_absent():
    from promptpotter.application.optimization.dispatch.hub import (
        DispatchHub,
        InjectionBundle,
        RoundDigest,
    )

    base_kwargs = {
        "opt_sp": OptSearchPoint(),
        "pipeline_schema": None,
        "cycle_slice": _empty_cycle_slice(),
        "digest": RoundDigest(diagnostics=None, critique=None),
    }

    no_axes = InjectionBundle(**base_kwargs, axes=None)
    assert DispatchHub.render("axis_memory", no_axes) == ""

    empty_digest = InjectionBundle(
        **base_kwargs,
        axes=types.SimpleNamespace(digest=lambda: None),
    )
    assert DispatchHub.render("axis_memory", empty_digest) == ""


def test_axis_memory_listed_in_l1_possible_and_default_layout():
    from promptpotter.domain.l1_layout import L1_POSSIBLE, default_l1_layout

    assert "axis_memory" in L1_POSSIBLE
    assert "axis_memory" in default_l1_layout().problem_description


# Dispatch prompt-budget allocator: sheds lowest-tier-first; MANDATORY tier never shed.


def test_dispatch_budget_caps_injections_and_sheds_by_tier(monkeypatch):
    """char_cap truncates; OPTIONAL sheds before CORE; MANDATORY never sheds; unhealable ⇒ PROMPT_BUDGET halt."""
    import pytest

    from promptpotter.application.optimization.dispatch.hub import (
        DispatchHub,
        InjectionBundle,
        InjectionRenderError,
        RoundDigest,
    )
    from promptpotter.application.optimization.dispatch.hub.bundle import (
        OPTIMIZER_PROMPT_CHAR_BUDGET as BUDGET,
    )
    from promptpotter.application.optimization.dispatch.hub.bundle import (
        InjectionKind,
        InjectionTier,
        _Injection,
    )
    from promptpotter.application.optimization.dispatch.hub.facade import _apply_budget
    from promptpotter.application.optimization.dispatch.hub.injections.registry import INJECTIONS
    from promptpotter.domain.phases import StopLoop, StopReason

    def _bundle(**kw):
        return InjectionBundle(
            opt_sp=kw.pop("opt_sp", OptSearchPoint()),
            pipeline_schema=None,
            cycle_slice=_empty_cycle_slice(),
            digest=RoundDigest(diagnostics=None, critique=None),
            axes=None,
            **kw,
        )

    plan_cap = INJECTIONS["plan"].char_cap
    assert plan_cap is not None
    capped = DispatchHub.render("plan", _bundle(opt_sp=OptSearchPoint(plan="x" * 5000)))
    assert len(capped) <= plan_cap + 1
    assert capped.endswith("…")

    unit = BUDGET // 4

    # Shedding the lone OPTIONAL injection is enough — CORE + MANDATORY kept.
    out = _apply_budget(
        unit,
        {
            "rendered_prompt": "M" * unit,
            "diagnostics": "C" * unit,
            "axis_memory": "O" * (2 * unit),
        },
    )
    assert out["rendered_prompt"] and out["diagnostics"]
    assert out["axis_memory"] == ""
    assert unit + sum(len(v) for v in out.values()) <= BUDGET

    # OPTIONAL alone insufficient → CORE sheds too; MANDATORY survives.
    out2 = _apply_budget(
        unit,
        {
            "rendered_prompt": "M" * (2 * unit),
            "diagnostics": "C" * (2 * unit),
            "axis_memory": "O" * unit,
        },
    )
    assert out2["rendered_prompt"]
    assert out2["axis_memory"] == "" and out2["diagnostics"] == ""
    assert unit + sum(len(v) for v in out2.values()) <= BUDGET

    # Unhealable: MANDATORY alone > budget ⇒ halt with PROMPT_BUDGET.
    with pytest.raises(StopLoop) as halt:
        _apply_budget(BUDGET, {"rendered_prompt": "M" * BUDGET})
    assert halt.value.reason is StopReason.PROMPT_BUDGET

    def _boom(_b):
        raise AttributeError("renamed data-model field")

    monkeypatch.setitem(
        INJECTIONS,
        "plan",
        _Injection(
            "plan", InjectionKind.TRACE, _boom, "", tier=InjectionTier.MANDATORY, char_cap=1200
        ),
    )
    with pytest.raises(InjectionRenderError):
        DispatchHub.render("plan", _bundle())


async def test_optimizer_call_deadline_retries_once_then_raises(monkeypatch):
    """``_chat_under_deadline`` retries one transient timeout, raises TimeoutError on the second."""
    import asyncio

    import pytest

    from promptpotter.application.optimization.dispatch.llm_call import call as llm_call_mod

    monkeypatch.setattr(llm_call_mod, "OPTIMIZER_CALL_DEADLINE_S", 0.05)

    class _Client:
        def __init__(self, hang_calls: int) -> None:
            self.hang_calls = hang_calls
            self.calls = 0

        async def chat(self, **_kw):
            self.calls += 1
            if self.calls <= self.hang_calls:
                await asyncio.sleep(10)
            return "ok"

    recovers = _Client(hang_calls=1)
    result = await llm_call_mod._chat_under_deadline(recovers, node_label="l1_generate")
    assert result == "ok" and recovers.calls == 2

    never = _Client(hang_calls=2)
    with pytest.raises(TimeoutError):
        await llm_call_mod._chat_under_deadline(never, node_label="l1_generate")
    assert never.calls == 2


# Escalation rules engine: default rule set reproduces observe_round's FSM branches.


def test_default_round_rules_reproduce_observe_round_fsm():
    """DEFAULT_ROUND_RULES matches observe_round's branches."""
    from promptpotter.application.optimization.escalation import (
        EscalationInputs,
        decide_escalation,
    )
    from promptpotter.application.optimization.escalation.state import NextAction

    perfect = EscalationInputs(
        improved=True,
        current_accuracy=1.0,
        l1_stall_count=0,
        l1_patience=3,
    )
    assert decide_escalation(perfect).next_action == NextAction.STOP_PERFECT

    continuing = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
    )
    assert decide_escalation(continuing).next_action == NextAction.CONTINUE

    fire = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=3,
        l1_patience=3,
    )
    assert decide_escalation(fire).next_action == NextAction.FIRE_L2


def test_l2_axis_yield_drought_rule_fires_when_signal_supports_it():
    """Yield-drought preempts l1_continue when AxisIndex shows zero productive axes."""
    from promptpotter.application.optimization.escalation import (
        EscalationInputs,
        decide_escalation,
    )
    from promptpotter.application.optimization.escalation.state import NextAction

    drought = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        axes_with_positive_yield=0,
    )
    assert decide_escalation(drought).next_action == NextAction.FIRE_L2

    productive = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        axes_with_positive_yield=2,
    )
    assert decide_escalation(productive).next_action == NextAction.CONTINUE

    no_evidence = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=3,
        axes_with_positive_yield=None,
    )
    assert decide_escalation(no_evidence).next_action == NextAction.CONTINUE


def test_l1_patience_zero_fires_l2_every_round():
    """l1_patience=0 fires L2 every round; perfect_accuracy still preempts."""
    from promptpotter.application.optimization.escalation import (
        EscalationInputs,
        decide_escalation,
    )
    from promptpotter.application.optimization.escalation.state import NextAction

    improving = EscalationInputs(
        improved=True,
        current_accuracy=0.5,
        l1_stall_count=0,
        l1_patience=0,
    )
    assert decide_escalation(improving).next_action == NextAction.FIRE_L2

    stalling = EscalationInputs(
        improved=False,
        current_accuracy=0.5,
        l1_stall_count=1,
        l1_patience=0,
    )
    assert decide_escalation(stalling).next_action == NextAction.FIRE_L2

    perfect = EscalationInputs(
        improved=True,
        current_accuracy=1.0,
        l1_stall_count=0,
        l1_patience=0,
    )
    assert decide_escalation(perfect).next_action == NextAction.STOP_PERFECT


# JobSearchPoint / OptSearchPoint shape + L2-layout copy_memory contract.

import pydantic  # noqa: E402

from promptpotter.application.optimization.round_analysis import (  # noqa: E402
    compute_round_diagnostics,
)
from promptpotter.domain.opt_search_point import FewShotExample  # noqa: E402
from promptpotter.domain.pipeline_schema import (  # noqa: E402
    NodePromptMeta,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.domain.results import RoundResult  # noqa: E402
from promptpotter.domain.sample import Sample  # noqa: E402
from promptpotter.domain.search_point import JobSearchPoint  # noqa: E402
from promptpotter.shared.hashing import content_hash  # noqa: E402


def _schema_with_prompt_node(name: str = "llm_ranking") -> PipelineSchema:
    return PipelineSchema(nodes=[PipelineNode(name=name, prompt_meta=NodePromptMeta())])


def test_jsp_construct_frozen_and_render_reads_pipeline_params():
    sp = JobSearchPoint(pipeline_params={"llm_ranking": {"prompt": "Rank by relevance."}})
    assert sp.render() == "Rank by relevance."
    with pytest.raises(pydantic.ValidationError):
        sp.pipeline_params = {"new": "val"}


def test_jsp_content_hash_distinguishes_pipeline_params():
    dataset = [Sample(id=1, query="q", ground_truth="a")]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp_a.content_hash(dataset) != sp_b.content_hash(dataset)
    assert sp_a.content_hash(dataset) == content_hash(sp_a.render(), dataset, sp_a.pipeline_params)


def test_jsp_derive_returns_new_frozen_instance():
    sp = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp2 = sp.derive(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp2.pipeline_params == {"steps": ["fuzzy_matching"]}
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}
    with pytest.raises(pydantic.ValidationError):
        sp2.pipeline_params = {"steps": ["c"]}


def test_to_job_search_point_projects_prompt_fields_and_few_shot_block():
    osp = OptSearchPoint(
        persona="Expert",
        instruction="Rank items.",
        few_shot_examples=[FewShotExample(input="a", output="b")],
    )
    sp = osp.to_job_search_point(schema=_schema_with_prompt_node())
    assert sp.prompt_fields["persona"] == "Expert"
    assert sp.prompt_fields["instruction"] == "Rank items."
    assert "Input: a" in sp.prompt_fields["few_shot_block"]
    assert OptSearchPoint(instruction="X").to_job_search_point().prompt_fields is None


def test_render_does_not_leak_l3_plan_into_target_prompt():
    """L3's plan reaches L1/L2/L3 prompts via ``_r_plan`` only — never via ``OptSearchPoint.render``."""
    sentinel = "REVISED_OPTIMIZATION_FRAMEWORK_PLAN_SENTINEL"
    osp = OptSearchPoint(persona="Expert", instruction="Solve.", plan=sentinel)
    assert sentinel not in osp.render()


def test_copy_memory_carries_l1_layout_into_adoption_target():
    custom = L1Layout(
        persona=["plan"],
        task_intent=["task_context"],
        problem_description=["rendered_prompt", "pipeline_param_catalogue"],
    )
    parent = OptSearchPoint(l1_layout=custom)
    child = OptSearchPoint()
    parent.copy_memory_to(child)
    assert child.l1_layout.persona == ["plan"]
    assert child.l1_layout.task_intent == ["task_context"]
    assert child.l1_layout.problem_description == ["rendered_prompt", "pipeline_param_catalogue"]
    child.l1_layout.persona.append("diagnostics")
    assert "diagnostics" not in parent.l1_layout.persona


# compute_round_diagnostics — pure function over scoring data.


def _round(round_num: int, accuracy: float, results: list[dict]) -> RoundResult:
    return RoundResult(
        round=round_num,
        label=f"round_{round_num}",
        accuracy=accuracy,
        composite_fitness=accuracy,
        hits=sum(1 for r in results if r.get("hit")),
        total=len(results),
        improved=False,
        prompt_fields={},
        results=results,
        candidates_scored=1,
    )


def _hit(query: str, gt: str) -> dict:
    return {"query": query, "ground_truth": gt, "hit": True, "predicted": gt}


def _miss(query: str, gt: str) -> dict:
    return {"query": query, "ground_truth": gt, "hit": False, "predicted": "?"}


def test_diag_rank_lookup_no_schema_lands_everything_not_found():
    results = [_hit("q1", "a"), _miss("q2", "b"), _miss("q3", "c")]
    rr = _round(0, 0.33, results)
    diag = compute_round_diagnostics(rr, [rr], pipeline_schema=None)

    assert diag.n_valid == 3
    assert diag.rank_buckets["1"] == 0
    assert diag.rank_buckets["not_found"] == 3
    assert diag.top_k_accuracy[1] == 0.0
    assert diag.top_k_accuracy[10] == 0.0


def test_diag_trajectory_classification_picks_up_plateau():
    rounds = [
        _round(0, 0.50, [_hit("q1", "a")]),
        _round(1, 0.51, [_hit("q1", "a")]),
        _round(2, 0.51, [_hit("q1", "a")]),
        _round(3, 0.51, [_hit("q1", "a")]),
    ]
    diag = compute_round_diagnostics(rounds[-1], rounds, pipeline_schema=None)
    assert diag.trajectory in {"plateau", "ceiling"}


def test_diag_probe_outcome_gated_by_probe_just_completed_flag():
    results = [_hit("q1", "a"), _hit("q2", "b"), _miss("q3", "c"), _miss("q4", "d")]
    rr = _round(0, 0.5, results)

    diag = compute_round_diagnostics(rr, [rr], pipeline_schema=None)
    assert diag.probe_outcome is None

    diag_probe = compute_round_diagnostics(
        rr,
        [rr],
        pipeline_schema=None,
        probe_just_completed=True,
        axis_tested="persona",
        prior_full_accuracy=0.7,
    )
    assert diag_probe.probe_outcome is not None
    assert diag_probe.probe_outcome.axis_tested == "persona"
    assert diag_probe.probe_outcome.target_subset_size == 4
    assert diag_probe.probe_outcome.hit_rate == 0.5
    assert diag_probe.probe_outcome.delta_vs_full == pytest.approx(-0.2)


def test_merge_into_cumulative_preserves_prior_on_untouched_samples():
    """Subset-measured winner must not shrink the cumulative pool — prior samples are preserved."""
    from promptpotter.application.optimization.cycle import _merge_into_cumulative

    prior = [{"sample_id": i, "fitness": 1.0 if i < 10 else 0.0, "hit": i < 10} for i in range(20)]
    winner_hits = {10, 12, 15}
    winner = [
        {"sample_id": sid, "fitness": 1.0 if sid in winner_hits else 0.0, "hit": sid in winner_hits}
        for sid in range(10, 18)
    ]
    merged = _merge_into_cumulative(prior, winner)
    by_sid = {r["sample_id"]: r for r in merged}

    assert set(by_sid.keys()) == set(range(20))
    assert by_sid[10]["hit"] is True
    assert by_sid[11]["hit"] is False
    assert by_sid[19]["hit"] is False
    assert all(by_sid[i]["hit"] is True for i in range(10))
    assert _merge_into_cumulative(prior, []) == prior


def test_l2_behavior_checks_score_conformant_vs_stub_fires():
    """Conformant L2 fire passes every behaviour check; stub fails them; no-fire ⇒ no results."""
    from promptpotter.application.optimization.validators.l1_behavior import CheckContext
    from promptpotter.application.optimization.validators.l2_behavior import run_all_l2_checks

    def _round(response: dict) -> dict:
        return {"nodes": {"l2_context": {"output": {"response": response}}}}

    ctx = CheckContext(
        round_num=3,
        opt_search_point={"task_context": {"key_challenges": "the prior framing"}},
    )

    conformant = run_all_l2_checks(
        _round(
            {
                "rationale": "Axis instruction stalled 3 rounds; sample #14 regressed — refocus.",
                "axis_targeted": "instruction",
                "task_context": {"key_challenges": "a genuinely new framing of negation depth"},
            }
        ),
        ctx,
    )
    assert len(conformant) == 4
    assert all(c.passed for c in conformant)

    stub = run_all_l2_checks(
        _round({"rationale": "ok", "axis_targeted": "", "task_context": {}}),
        ctx,
    )
    failed = {c.check_id for c in stub if not c.passed}
    assert {"l2_rationale_substantive", "l2_evidence_anchored"} <= failed

    assert run_all_l2_checks({"nodes": {"l1_generate": {}}}, ctx) == []
