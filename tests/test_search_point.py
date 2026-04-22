"""Tests for JobSearchPoint model."""

import pydantic
import pytest

from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import NodePromptMeta, PipelineNode, PipelineSchema
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.hashing import content_hash


def _schema_with_prompt_node(name: str = "llm_ranking") -> PipelineSchema:
    """Build a minimal PipelineSchema with a single prompt-bearing node."""
    node = PipelineNode(name=name, prompt_meta=NodePromptMeta())
    return PipelineSchema(nodes=[node])


def _make_jsp(instruction: str = "Rank by relevance.", **kwargs) -> JobSearchPoint:
    """Helper: build a JobSearchPoint from an instruction string."""
    osp = OptSearchPoint(instruction=instruction)
    return osp.to_job_search_point(
        base_pipeline_params=kwargs.get("pipeline_params"),
    )


def test_construct_and_frozen():
    sp = JobSearchPoint()
    assert sp.pipeline_params is None
    sp2 = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "Rank by relevance."}},
    )
    assert sp2.render() == "Rank by relevance."
    with pytest.raises(pydantic.ValidationError):
        sp.pipeline_params = {"new": "val"}


def test_content_hash_matches_content_hash():
    # Without extra pipeline params
    sp = _make_jsp("Rank by relevance.")
    dataset = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
    ]
    assert sp.content_hash(dataset) == content_hash(sp.render(), dataset, sp.pipeline_params)

    # With extra pipeline params
    osp = OptSearchPoint(instruction="Rank by relevance.")
    pp = {"llm_ranking": {"prompt": osp.render()}, "ranking_temperature": 0.5}
    sp2 = JobSearchPoint(pipeline_params=pp)
    data2 = [{"query": "aspirin", "ground_truth": "Aspirin"}]
    assert sp2.content_hash(data2) == content_hash(sp2.render(), data2, pp)


def test_content_hash_differs_with_pipeline_params():
    dataset = [{"query": "q", "ground_truth": "a"}]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    sp_none = JobSearchPoint()
    assert sp_a.content_hash(dataset) != sp_b.content_hash(dataset)
    assert sp_a.content_hash(dataset) != sp_none.content_hash(dataset)
    # Non-steps params also differ
    sp_t1 = JobSearchPoint(pipeline_params={"ranking_temperature": 0.5})
    sp_t2 = JobSearchPoint(pipeline_params={"ranking_temperature": 0.9})
    assert sp_t1.content_hash(dataset) != sp_t2.content_hash(dataset)


def test_derive_pipeline_params():
    sp = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp2 = sp.derive(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp2.pipeline_params == {"steps": ["fuzzy_matching"]}
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}  # original unchanged
    assert sp is not sp2
    with pytest.raises(pydantic.ValidationError):
        sp2.pipeline_params = {"steps": ["c"]}


def test_to_job_search_point_populates_prompt_fields():
    """OptSearchPoint.to_job_search_point() populates prompt_fields."""
    osp = OptSearchPoint(
        persona="Expert",
        thinking_style="Step by step",
        instruction="Rank items.",
    )
    sp = osp.to_job_search_point(schema=_schema_with_prompt_node())
    assert sp.prompt_fields is not None
    assert sp.prompt_fields["persona"] == "Expert"
    assert sp.prompt_fields["thinking_style"] == "Step by step"
    assert sp.prompt_fields["instruction"] == "Rank items."


def test_to_job_search_point_no_prompt_node():
    """Without prompt_node, prompt_fields should be None."""
    osp = OptSearchPoint(instruction="Rank items.")
    sp = osp.to_job_search_point()
    assert sp.prompt_fields is None


def test_to_job_search_point_includes_few_shot_block():
    """Few-shot examples are pre-rendered into prompt_fields."""
    from promptpotter.domain.opt_search_point import FewShotExample

    osp = OptSearchPoint(
        instruction="Rank.",
        few_shot_examples=[FewShotExample(input="a", output="b")],
    )
    sp = osp.to_job_search_point(schema=_schema_with_prompt_node())
    assert "few_shot_block" in sp.prompt_fields
    assert "Input: a" in sp.prompt_fields["few_shot_block"]


def test_clear_volatile_drops_full_steering_window():
    """All three steering fields share one lifecycle — they clear together.

    Regression guard for the R2 L1 meta-prompt TPM blow-up: if
    ``critique_text`` / ``thinking_styles`` stop being cleared on
    improvement, they bleed into the next round's L1 inbox (mutex'd
    against ``l2_directive`` in the L1 layer but still rendered when
    L2 did not fire), pushing requests over provider TPM caps.
    """
    osp = OptSearchPoint(instruction="Rank.")
    osp.memory.l2_directive = "steer away from temperature=1.0"
    osp.memory.critique_text = "rank-1 recall is the bottleneck"
    osp.memory.thinking_styles = ["decompose", "compare", "verify"]

    osp.memory.clear_volatile()

    assert osp.memory.l2_directive == ""
    assert osp.memory.critique_text == ""
    assert osp.memory.thinking_styles == []
