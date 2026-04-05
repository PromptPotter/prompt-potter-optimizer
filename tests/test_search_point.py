"""Tests for JobSearchPoint model."""
import pydantic
import pytest

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.search_point import JobSearchPoint
from promptpotter.shared.hashing import eval_content_hash


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


def test_content_hash_matches_eval_content_hash():
    # Without extra pipeline params
    sp = _make_jsp("Rank by relevance.")
    dataset = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
    ]
    assert sp.content_hash(dataset) == eval_content_hash(sp.render(), dataset, sp.pipeline_params)

    # With extra pipeline params
    osp = OptSearchPoint(instruction="Rank by relevance.")
    pp = {"llm_ranking": {"prompt": osp.render()}, "ranking_temperature": 0.5}
    sp2 = JobSearchPoint(pipeline_params=pp)
    data2 = [{"query": "aspirin", "ground_truth": "Aspirin"}]
    assert sp2.content_hash(data2) == eval_content_hash(sp2.render(), data2, pp)


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


# ---------------------------------------------------------------------------
# prompt_fields — variant derivation without OptSearchPoint
# ---------------------------------------------------------------------------


def test_prompt_fields_render():
    """JSP with prompt_fields renders from them; without falls back to pipeline_params."""
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "stale rendered text"}},
        prompt_fields={"persona": "You are an expert.", "instruction": "Rank items."},
    )
    rendered = sp.render()
    assert "You are an expert." in rendered
    assert "Rank items." in rendered
    assert "stale rendered text" not in rendered

    # Fallback: no prompt_fields → pipeline_params
    sp2 = JobSearchPoint(pipeline_params={"llm_ranking": {"prompt": "Hello world."}})
    assert sp2.render() == "Hello world."
    assert sp2.prompt_fields is None


def test_prompt_fields_render_ordering_and_few_shot():
    """Fields assembled in PROMPT_STRING_FIELDS order; few_shot_block appended after."""
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": ""}},
        prompt_fields={"instruction": "Second", "persona": "First"},
    )
    assert sp.render().index("First") < sp.render().index("Second")

    sp2 = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": ""}},
        prompt_fields={"instruction": "Rank items.", "few_shot_block": "Input: x\nOutput: y"},
    )
    rendered = sp2.render()
    assert rendered.index("Rank items.") < rendered.index("Input: x")


def test_derive_with_prompt_fields():
    """derive(prompt_fields=...) merges fields, re-renders, preserves others."""
    osp = OptSearchPoint(persona="Expert", instruction="Rank by relevance.")
    sp = osp.to_job_search_point(prompt_node="llm_ranking")

    sp2 = sp.derive(prompt_fields={"instruction": "Sort by name."})
    assert sp2.prompt_fields["instruction"] == "Sort by name."
    assert sp2.prompt_fields["persona"] == "Expert"  # preserved
    assert "Sort by name." in sp2.render()
    assert sp.prompt_fields["instruction"] == "Rank by relevance."  # original unchanged


def test_derive_prompt_fields_sp_hash_consistency():
    """derive(prompt_fields=...) produces the same sp_hash as a fresh projection."""
    osp = OptSearchPoint(persona="Expert", instruction="Rank items.")
    sp = osp.to_job_search_point(prompt_node="llm_ranking")

    # Derive with a field change
    sp_derived = sp.derive(prompt_fields={"instruction": "Sort items."})

    # Fresh projection with the same change
    osp2 = osp.derive_candidate(instruction="Sort items.")
    sp_fresh = osp2.to_job_search_point(prompt_node="llm_ranking")

    assert sp_derived.sp_hash() == sp_fresh.sp_hash()
    assert sp_derived.render() == sp_fresh.render()


def test_prompt_fields_not_in_sp_hash():
    """JSP with and without prompt_fields but same rendered prompt → same sp_hash."""
    pp = {"llm_ranking": {"prompt": "Expert\n\nRank items."}}
    sp_with = JobSearchPoint(
        pipeline_params=pp,
        prompt_fields={"persona": "Expert", "instruction": "Rank items."},
    )
    sp_without = JobSearchPoint(pipeline_params=pp)
    assert sp_with.sp_hash() == sp_without.sp_hash()


def test_to_job_search_point_populates_prompt_fields():
    """OptSearchPoint.to_job_search_point() populates prompt_fields."""
    osp = OptSearchPoint(
        persona="Expert",
        thinking_style="Step by step",
        instruction="Rank items.",
    )
    sp = osp.to_job_search_point(prompt_node="llm_ranking")
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
    from promptpotter.models.opt_search_point import FewShotExample
    osp = OptSearchPoint(
        instruction="Rank.",
        few_shot_examples=[FewShotExample(input="a", output="b")],
    )
    sp = osp.to_job_search_point(prompt_node="llm_ranking")
    assert "few_shot_block" in sp.prompt_fields
    assert "Input: a" in sp.prompt_fields["few_shot_block"]


def test_derive_without_prompt_fields_carries_forward():
    """derive(pipeline_params=...) preserves existing prompt_fields."""
    sp = _make_jsp("Rank by relevance.")
    sp2 = sp.derive(pipeline_params={**sp.pipeline_params, "extra": 42})
    assert sp2.prompt_fields == sp.prompt_fields
    assert sp2.pipeline_params["extra"] == 42
