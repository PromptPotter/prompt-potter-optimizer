"""L1 detector: candidate ≡ non-empty unique mutation, else ValidationFailure → synth-0."""

from promptpotter.application.optimization.nodes.l1_measure import detect_invariants
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal


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
    assert stats.n_no_op == 1
    assert stats.n_duplicate == 0
    assert stats.yield_ == 0.5


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
    assert stats.n_duplicate == 1
    assert stats.n_no_op == 0
    assert stats.yield_ == 2 / 3


def test_idempotent_under_repeat_calls():
    parent = _parent()
    proposals = [_child(parent), _child(parent, persona="P")]

    s1 = detect_invariants(proposals, parent)
    s2 = detect_invariants(proposals, parent)

    no_op_count = sum(
        1 for vf in proposals[0].osp.validation_failures if vf.reason == "no_op_variant"
    )
    assert no_op_count == 1
    assert s1 == s2


def test_node_overrides_count_as_mutation():
    parent = _parent()
    proposals = [
        CandidateProposal(osp=parent.mutate(), node_overrides={"llm": {"temperature": 0.0}})
    ]

    stats = detect_invariants(proposals, parent)

    assert proposals[0].osp.validation_failures == []
    assert stats.n_no_op == 0
    assert stats.yield_ == 1.0
