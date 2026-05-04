"""Sweep payload → OSP → trial JSON round-trip.

Guards the M10 trace-persistence contract: a ``SweepPayload`` written
via ``apply_sweep_payload_to_osp`` lands on the ``OptSearchPoint``
fields the cycle's existing ``model_dump`` checkpoint code persists,
and ``OptSearchPoint(**dump)`` reconstructs the same L1-surface state
on resume.

The user has previously validated the JobSearchPoint trace; OSP traces
share the same checkpoint path but had not been independently checked.
This test pins the OSP fields used by ``compile_prompt_vars`` (L1-generate
section overrides) so a future refactor that demotes one to a non-persisted
attribute fails loudly.
"""

from __future__ import annotations

from promptpotter.application.optimization.cycle import apply_sweep_payload_to_osp
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.run_records import SweepPayload


def test_sweep_payload_roundtrips_through_opt_search_point() -> None:
    payload = SweepPayload(
        reason="canonical case",
        directive="reason step-by-step before answering",
        l1_section_overrides={"axes_l1": False},
        l1_section_overrides_text={"task_context": "hard reasoning framing"},
        l1_template_override="{{l2_directive}}\n\nCustom body.",
    )

    osp = OptSearchPoint.from_prompt_fields({"persona": "p", "task_intent": "t"})
    apply_sweep_payload_to_osp(osp, payload)

    # The fields land where compile_prompt_vars reads them (L1-generate overrides).
    assert osp.l2_directive == payload.directive
    assert osp.l1_section_overrides == {"axes_l1": False}
    assert osp.l1_section_overrides_text == {"task_context": "hard reasoning framing"}
    assert osp.l1_template_override == payload.l1_template_override

    # The same model_dump path the trial writer uses → OptSearchPoint(**dump).
    # If any of these four fields stops being a persisted Pydantic field,
    # this reconstruction loses state and the assertions below fail.
    dump = osp.model_dump()
    reloaded = OptSearchPoint(**dump)

    assert reloaded.l2_directive == payload.directive
    assert reloaded.l1_section_overrides == {"axes_l1": False}
    assert reloaded.l1_section_overrides_text == {"task_context": "hard reasoning framing"}
    assert reloaded.l1_template_override == payload.l1_template_override


def test_sweep_payload_merges_with_existing_overrides() -> None:
    """Apply mirrors L2RefineStrategy.apply_side_effects: dict deltas merge,
    str scalars assign. A second payload extends the first's section dicts
    (not replace) so a sweep over a non-fresh OSP doesn't drop prior keys."""
    osp = OptSearchPoint.from_prompt_fields({"persona": "p"})
    osp.l1_section_overrides = {"plan": True}
    osp.l1_section_overrides_text = {"l2_directive": "original"}

    payload = SweepPayload(
        l1_section_overrides={"axes_l1": False},
        l1_section_overrides_text={"task_context": "added"},
    )
    apply_sweep_payload_to_osp(osp, payload)

    assert osp.l1_section_overrides == {"plan": True, "axes_l1": False}
    assert osp.l1_section_overrides_text == {
        "l2_directive": "original",
        "task_context": "added",
    }


def test_sweep_payload_extra_keys_rejected() -> None:
    """``extra='forbid'`` so operator JSON typos fail at parse, not silently."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SweepPayload.model_validate({"directive": "x", "typo_field": "boom"})
