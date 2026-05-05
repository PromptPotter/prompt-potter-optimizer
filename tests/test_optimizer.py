"""Optimizer loop invariants — L1 detector, L2 output validators, PoBB
elimination, sweep payload round-trip.

Four named invariants:
  1. L1 ``detect_invariants``: a candidate is a non-empty unique mutation
     of the parent OSP, else a ValidationFailure attaches → synth-0
     downstream. Idempotent under repeat calls; pipeline_params_override
     counts as mutation.
  2. L2 output validators (``L2_CROSS_FIELD_DUPLICATION``,
     ``L2_VERBATIM_SELF_REPEAT``, ``L2_CATALOGUE_REDUNDANCY``) flag the
     three failure shapes with ``nurse_target='l3'``;
     ``run_l2_output_validators`` aggregates and the L3 formatter renders
     validator ids + 'gradual' action so L3 sees the failure type.
  3. Bayesian PoBB: ``posterior_best_probabilities`` sums to 1.0; clear
     leaders collapse to ~1.0; uniform regimes diffuse to ~1/K. ``PoBBCheck``
     respects ``n_min`` floor and ``lock_in_n_min`` lock-in gate;
     ``lock_in=1.0`` disables the lock-in branch.
  4. ``SweepPayload`` round-trips through ``OptSearchPoint``: brief +
     section overrides + template override survive ``model_dump`` → reload;
     dict deltas merge (don't replace); extra keys rejected at parse.
"""

from __future__ import annotations

import numpy as np
import pytest

from promptpotter.application.optimization.escalation import apply_sweep_payload_to_osp
from promptpotter.application.optimization.formatting import format_l2_output_failures_for_l3
from promptpotter.application.optimization.l1_validators import detect_invariants
from promptpotter.application.optimization.l2_validators import (
    L2_CATALOGUE_REDUNDANCY,
    L2_CROSS_FIELD_DUPLICATION,
    L2_VERBATIM_SELF_REPEAT,
    run_l2_output_validators,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.run_records import SweepPayload

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
# L2 output validators
# ===========================================================================


def _osp(**kwargs) -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.", **kwargs)


def test_cross_field_duplication_fires_on_repeated_block():
    block = "step one\nstep two\nstep three"
    out = L2_CROSS_FIELD_DUPLICATION.run(
        {
            "brief": f"intro\n{block}\nclose",
            "template_override": f"prelude\n{block}\nepilogue",
            "text_overrides": {},
        },
        opt_sp=_osp(),
    )
    assert out is not None
    assert out.passed is False
    assert out.score == 0.0
    assert out.nurse_target == "l3"
    duplicates = out.evidence["duplicates"]
    assert any(block == d["block"] for d in duplicates)


def test_verbatim_self_repeat_fires_when_brief_matches_prev():
    osp = _osp(l2_brief="Use ONLY model X for now.")
    out = L2_VERBATIM_SELF_REPEAT.run(
        {"brief": "Use ONLY model X for now.", "text_overrides": {}},
        opt_sp=osp,
    )
    assert out is not None
    assert out.nurse_target == "l3"
    assert "Use ONLY model X" in out.evidence["brief"]


def test_catalogue_redundancy_fires_on_no_op_text_override():
    osp = _osp(l1_section_overrides_text={"axes_l1": "do not propose model X"})
    out = L2_CATALOGUE_REDUNDANCY.run(
        {
            "brief": "irrelevant",
            "text_overrides": {"axes_l1": "do not propose model X"},
        },
        opt_sp=osp,
    )
    assert out is not None
    assert any(r["section"] == "axes_l1" for r in out.evidence["redundant_overrides"])


def test_run_l2_output_validators_aggregates():
    block = "alpha\nbeta\ngamma"
    osp = _osp(
        l2_brief="repeat me",
        l1_section_overrides_text={"axes_l1": "no-op"},
    )
    outcomes = run_l2_output_validators(
        {
            "brief": "repeat me",
            "template_override": f"hi\n{block}\nbye",
            "text_overrides": {
                "axes_l1": "no-op",
                "axes_l2": f"{block}\nfooter",
            },
        },
        opt_sp=osp,
    )
    ids = {o.validator_id for o in outcomes}
    assert "l2_cross_field_duplication" in ids
    assert "l2_verbatim_self_repeat" in ids
    assert "l2_catalogue_redundancy" in ids


def test_format_for_l3_renders_validator_ids_and_action():
    osp = _osp(l2_brief="repeat me")
    outcomes = run_l2_output_validators({"brief": "repeat me", "text_overrides": {}}, opt_sp=osp)
    rendered = format_l2_output_failures_for_l3(outcomes)
    assert "L2 OUTPUT FAILURES" in rendered
    assert "l2_verbatim_self_repeat" in rendered
    assert "gradual" in rendered.lower()


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


def test_sweep_payload_roundtrips_through_opt_search_point() -> None:
    payload = SweepPayload(
        reason="canonical case",
        brief="reason step-by-step before answering",
        l1_section_overrides={"axes_l1": False},
        l1_section_overrides_text={"task_context": "hard reasoning framing"},
        l1_template_override="{{l2_brief}}\n\nCustom body.",
    )

    osp = OptSearchPoint.from_prompt_fields({"persona": "p", "task_intent": "t"})
    apply_sweep_payload_to_osp(osp, payload)

    # The fields land where compile_prompt_vars reads them (L1-generate overrides).
    assert osp.l2_brief == payload.brief
    assert osp.l1_section_overrides == {"axes_l1": False}
    assert osp.l1_section_overrides_text == {"task_context": "hard reasoning framing"}
    assert osp.l1_template_override == payload.l1_template_override

    # The same model_dump path the round_data writer uses → OptSearchPoint(**dump).
    # If any of these four fields stops being a persisted Pydantic field,
    # this reconstruction loses state and the assertions below fail.
    dump = osp.model_dump()
    reloaded = OptSearchPoint(**dump)

    assert reloaded.l2_brief == payload.brief
    assert reloaded.l1_section_overrides == {"axes_l1": False}
    assert reloaded.l1_section_overrides_text == {"task_context": "hard reasoning framing"}
    assert reloaded.l1_template_override == payload.l1_template_override


def test_sweep_payload_merges_with_existing_overrides() -> None:
    """Apply mirrors L2RefineStrategy.apply_side_effects: dict deltas merge,
    str scalars assign. A second payload extends the first's section dicts
    (not replace) so a sweep over a non-fresh OSP doesn't drop prior keys."""
    osp = OptSearchPoint.from_prompt_fields({"persona": "p"})
    osp.l1_section_overrides = {"plan": True}
    osp.l1_section_overrides_text = {"l2_brief": "original"}

    payload = SweepPayload(
        l1_section_overrides={"axes_l1": False},
        l1_section_overrides_text={"task_context": "added"},
    )
    apply_sweep_payload_to_osp(osp, payload)

    assert osp.l1_section_overrides == {"plan": True, "axes_l1": False}
    assert osp.l1_section_overrides_text == {
        "l2_brief": "original",
        "task_context": "added",
    }
