"""L2 output validators — Loop 4 (L3 nurses L2 on validator failure)."""

from promptpotter.application.optimization.formatting import format_l2_output_failures_for_l3
from promptpotter.application.optimization.l2_validators import (
    L2_CATALOGUE_REDUNDANCY,
    L2_CROSS_FIELD_DUPLICATION,
    L2_VERBATIM_SELF_REPEAT,
    run_l2_output_validators,
)
from promptpotter.domain.opt_search_point import OptSearchPoint


def _osp(**kwargs) -> OptSearchPoint:
    return OptSearchPoint(persona="Expert", instruction="Rank items.", **kwargs)


def test_cross_field_duplication_fires_on_repeated_block():
    block = "step one\nstep two\nstep three"
    out = L2_CROSS_FIELD_DUPLICATION.run(
        {
            "directive": f"intro\n{block}\nclose",
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


def test_cross_field_duplication_clean_when_unique():
    out = L2_CROSS_FIELD_DUPLICATION.run(
        {
            "directive": "use only model X for now.",
            "template_override": "completely different content here.",
            "text_overrides": {"axes_l1": "third unrelated text."},
        },
        opt_sp=_osp(),
    )
    assert out is None


def test_verbatim_self_repeat_fires_when_directive_matches_prev():
    osp = _osp(l2_directive="Use ONLY model X for now.")
    out = L2_VERBATIM_SELF_REPEAT.run(
        {"directive": "Use ONLY model X for now.", "text_overrides": {}},
        opt_sp=osp,
    )
    assert out is not None
    assert out.nurse_target == "l3"
    assert "Use ONLY model X" in out.evidence["directive"]


def test_verbatim_self_repeat_clean_when_directive_differs():
    osp = _osp(l2_directive="Use ONLY model X.")
    out = L2_VERBATIM_SELF_REPEAT.run(
        {"directive": "Switch to model Y instead.", "text_overrides": {}},
        opt_sp=osp,
    )
    assert out is None


def test_verbatim_self_repeat_clean_when_no_prev_directive():
    osp = _osp()  # l2_directive default ""
    out = L2_VERBATIM_SELF_REPEAT.run(
        {"directive": "Anything new.", "text_overrides": {}},
        opt_sp=osp,
    )
    assert out is None


def test_catalogue_redundancy_fires_on_no_op_text_override():
    osp = _osp(l1_section_overrides_text={"axes_l1": "do not propose model X"})
    out = L2_CATALOGUE_REDUNDANCY.run(
        {
            "directive": "irrelevant",
            "text_overrides": {"axes_l1": "do not propose model X"},
        },
        opt_sp=osp,
    )
    assert out is not None
    assert any(r["section"] == "axes_l1" for r in out.evidence["redundant_overrides"])


def test_catalogue_redundancy_clean_on_novel_override():
    osp = _osp(l1_section_overrides_text={"axes_l1": "old guidance"})
    out = L2_CATALOGUE_REDUNDANCY.run(
        {"directive": "x", "text_overrides": {"axes_l1": "fresh guidance"}},
        opt_sp=osp,
    )
    assert out is None


def test_run_l2_output_validators_aggregates():
    block = "alpha\nbeta\ngamma"
    osp = _osp(
        l2_directive="repeat me",
        l1_section_overrides_text={"axes_l1": "no-op"},
    )
    outcomes = run_l2_output_validators(
        {
            "directive": "repeat me",
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
    osp = _osp(l2_directive="repeat me")
    outcomes = run_l2_output_validators(
        {"directive": "repeat me", "text_overrides": {}}, opt_sp=osp
    )
    rendered = format_l2_output_failures_for_l3(outcomes)
    assert "L2 OUTPUT FAILURES" in rendered
    assert "l2_verbatim_self_repeat" in rendered
    assert "gradual" in rendered.lower()


def test_format_for_l3_empty_collapses():
    assert format_l2_output_failures_for_l3([]) == ""
    assert format_l2_output_failures_for_l3(None) == ""
