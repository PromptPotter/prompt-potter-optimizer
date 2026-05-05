"""Frozen JobSearchPoint shape: hash distinctness, derive, OptSearchPoint projection."""

import pydantic
import pytest

from promptpotter.domain.opt_search_point import FewShotExample, OptSearchPoint
from promptpotter.domain.pipeline_schema import NodePromptMeta, PipelineNode, PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.hashing import content_hash


def _schema_with_prompt_node(name: str = "llm_ranking") -> PipelineSchema:
    return PipelineSchema(nodes=[PipelineNode(name=name, prompt_meta=NodePromptMeta())])


def test_construct_frozen_and_render_reads_pipeline_params():
    sp = JobSearchPoint(pipeline_params={"llm_ranking": {"prompt": "Rank by relevance."}})
    assert sp.render() == "Rank by relevance."
    with pytest.raises(pydantic.ValidationError):
        sp.pipeline_params = {"new": "val"}


def test_content_hash_distinguishes_pipeline_params():
    dataset = [Sample(id=1, query="q", ground_truth="a")]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp_a.content_hash(dataset) != sp_b.content_hash(dataset)
    assert sp_a.content_hash(dataset) == content_hash(sp_a.render(), dataset, sp_a.pipeline_params)


def test_derive_pipeline_params_returns_new_frozen_instance():
    sp = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp2 = sp.derive(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp2.pipeline_params == {"steps": ["fuzzy_matching"]}
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}  # original unchanged
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
    # Without a prompt-bearing node in the schema, prompt_fields collapses to None.
    assert OptSearchPoint(instruction="X").to_job_search_point().prompt_fields is None


def test_copy_memory_carries_l1_surface_overrides_into_adoption_target():
    """L2-written L1-surface state must survive L2→L3 adoption.

    Regression for the audit finding: ``l1_section_overrides``,
    ``l1_section_overrides_text`` and ``l1_template_override`` used to live
    outside ``MEMORY_FIELDS``, so ``copy_memory_to`` silently dropped them
    when L3 spawned a fresh OSP. Then ``apply_side_effects``'s in-place
    ``{**existing, **new}`` merge would silently keep stale entries from
    the OSP that was about to be defeated. Pinning here.
    """
    parent = OptSearchPoint(
        l1_section_overrides={"brief_block": False, "warning_inventory": True},
        l1_section_overrides_text={"brief_block": "use pattern X"},
        l1_template_override="reframed body with {{l2_brief}}",
    )
    child = OptSearchPoint()
    parent.copy_memory_to(child)

    assert child.l1_section_overrides == {"brief_block": False, "warning_inventory": True}
    assert child.l1_section_overrides_text == {"brief_block": "use pattern X"}
    assert child.l1_template_override == "reframed body with {{l2_brief}}"

    # Deep-copy contract — mutating the child's dicts must NOT affect the parent.
    child.l1_section_overrides["new_key"] = True
    child.l1_section_overrides_text["another"] = "v"
    assert "new_key" not in parent.l1_section_overrides
    assert "another" not in parent.l1_section_overrides_text
