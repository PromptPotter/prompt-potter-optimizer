"""Tests for JobSearchPoint model."""
import pytest

from api.models.opt_search_point import OptSearchPoint
from api.models.search_point import JobSearchPoint
from api.models.hashing import eval_content_hash


def _make_jsp(instruction: str = "Rank by relevance.", **kwargs) -> JobSearchPoint:
    """Helper: build a JobSearchPoint from an instruction string."""
    osp = OptSearchPoint(instruction=instruction)
    return osp.to_job_search_point(
        model=kwargs.get("model", ""),
        temperature=kwargs.get("temperature", 0.0),
        base_pipeline_params=kwargs.get("pipeline_params"),
    )


def test_construct_with_defaults():
    sp = JobSearchPoint()
    assert sp.model == ""
    assert sp.temperature == 0.0
    assert sp.pipeline_params is None


def test_construct_with_all_fields():
    sp = JobSearchPoint(
        model="llama-3",
        temperature=0.5,
        pipeline_params={"llm_ranking": {"prompt": "Rank by relevance."}},
    )
    assert sp.model == "llama-3"
    assert sp.temperature == 0.5
    assert sp.render() == "Rank by relevance."


def test_frozen():
    sp = JobSearchPoint()
    with pytest.raises(Exception):
        sp.model = "new-model"


def test_render_extracts_prompt():
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "Expert\n\nRank candidates."}},
    )
    assert "Expert" in sp.render()
    assert "Rank candidates." in sp.render()


def test_content_hash_matches_eval_content_hash():
    sp = _make_jsp("Rank by relevance.", model="llama-3", temperature=0.5)
    eval_data = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
    ]
    expected = eval_content_hash(sp.render(), eval_data, "llama-3", 0.5, sp.pipeline_params)
    assert sp.content_hash(eval_data) == expected


def test_content_hash_matches_eval_content_hash_with_pipeline_params():
    osp = OptSearchPoint(instruction="Rank by relevance.")
    pp = {"llm_ranking": {"prompt": osp.render()}, "ranking_temperature": 0.5}
    sp = JobSearchPoint(model="llama-3", temperature=0.5, pipeline_params=pp)
    eval_data = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
    ]
    expected = eval_content_hash(sp.render(), eval_data, "llama-3", 0.5, pp)
    assert sp.content_hash(eval_data) == expected


def test_content_hash_differs_with_model():
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = _make_jsp(model="model-a")
    sp_b = _make_jsp(model="model-b")
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)


def test_content_hash_differs_with_temperature():
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = _make_jsp(temperature=0.0)
    sp_b = _make_jsp(temperature=0.7)
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)


def test_content_hash_includes_pipeline_params():
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = JobSearchPoint(pipeline_params={"steps": ["llm_ranking"]})
    sp_b = JobSearchPoint(pipeline_params={"steps": ["fuzzy_matching"]})
    sp_none = JobSearchPoint()
    # Different steps -> different hash
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)
    # steps pp differs from no pp
    assert sp_a.content_hash(eval_data) != sp_none.content_hash(eval_data)


def test_content_hash_differs_with_non_steps_pipeline_params():
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = JobSearchPoint(pipeline_params={"ranking_temperature": 0.5})
    sp_b = JobSearchPoint(pipeline_params={"ranking_temperature": 0.9})
    sp_none = JobSearchPoint()
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)
    assert sp_a.content_hash(eval_data) != sp_none.content_hash(eval_data)


def test_derive_model_and_temperature():
    sp = _make_jsp(model="llama-3", temperature=0.5)
    sp2 = sp.derive(model="gpt-4", temperature=0.9)
    assert sp2.model == "gpt-4"
    assert sp2.temperature == 0.9
    # Original prompt preserved
    assert sp2.render() == sp.render()


def test_derive_pipeline_params():
    sp = JobSearchPoint(
        pipeline_params={"steps": ["llm_ranking"]},
    )
    sp2 = sp.derive(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp2.pipeline_params == {"steps": ["fuzzy_matching"]}
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}  # original unchanged


def test_derive_returns_new_frozen_instance():
    sp = JobSearchPoint()
    sp2 = sp.derive(model="new-model")
    assert sp is not sp2
    with pytest.raises(Exception):
        sp2.model = "another"


# ---------------------------------------------------------------------------
# prompt_fields — variant derivation without OptSearchPoint
# ---------------------------------------------------------------------------


def test_prompt_fields_render():
    """JSP with prompt_fields renders from them, not from pipeline_params."""
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "stale rendered text"}},
        prompt_fields={"persona": "You are an expert.", "instruction": "Rank items."},
    )
    rendered = sp.render()
    assert "You are an expert." in rendered
    assert "Rank items." in rendered
    assert "stale rendered text" not in rendered


def test_prompt_fields_render_fallback():
    """JSP without prompt_fields renders from pipeline_params as before."""
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": "Hello world."}},
    )
    assert sp.render() == "Hello world."
    assert sp.prompt_fields is None


def test_prompt_fields_render_preserves_field_order():
    """Fields are assembled in PROMPT_STRING_FIELDS order."""
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": ""}},
        prompt_fields={
            "instruction": "Second",
            "persona": "First",
        },
    )
    rendered = sp.render()
    assert rendered.index("First") < rendered.index("Second")


def test_prompt_fields_render_includes_few_shot_block():
    """Pre-rendered few_shot_block is appended after string fields."""
    sp = JobSearchPoint(
        pipeline_params={"llm_ranking": {"prompt": ""}},
        prompt_fields={
            "instruction": "Rank items.",
            "few_shot_block": "Input: x\nOutput: y",
        },
    )
    rendered = sp.render()
    assert "Rank items." in rendered
    assert "Input: x\nOutput: y" in rendered
    # few_shot_block comes after instruction
    assert rendered.index("Rank items.") < rendered.index("Input: x")


def test_derive_with_prompt_fields():
    """derive(prompt_fields=...) merges fields and re-renders into pipeline_params."""
    sp = _make_jsp("Rank by relevance.")
    assert sp.prompt_fields is not None
    assert sp.prompt_fields["instruction"] == "Rank by relevance."

    sp2 = sp.derive(prompt_fields={"instruction": "Sort by name."})
    assert sp2.prompt_fields["instruction"] == "Sort by name."
    assert "Sort by name." in sp2.render()
    assert "Sort by name." in sp2.pipeline_params["llm_ranking"]["prompt"]
    # Original unchanged
    assert sp.prompt_fields["instruction"] == "Rank by relevance."


def test_derive_prompt_fields_preserves_other_fields():
    """Partial prompt_fields update keeps unmentioned fields."""
    osp = OptSearchPoint(persona="Expert", instruction="Rank items.")
    sp = osp.to_job_search_point()
    sp2 = sp.derive(prompt_fields={"instruction": "Sort items."})
    assert sp2.prompt_fields["persona"] == "Expert"
    assert sp2.prompt_fields["instruction"] == "Sort items."


def test_derive_prompt_fields_sp_hash_consistency():
    """derive(prompt_fields=...) produces the same sp_hash as a fresh projection."""
    osp = OptSearchPoint(persona="Expert", instruction="Rank items.")
    sp = osp.to_job_search_point(model="m", temperature=0.5)

    # Derive with a field change
    sp_derived = sp.derive(prompt_fields={"instruction": "Sort items."})

    # Fresh projection with the same change
    osp2 = osp.derive_candidate(instruction="Sort items.")
    sp_fresh = osp2.to_job_search_point(model="m", temperature=0.5)

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
    sp = osp.to_job_search_point()
    assert sp.prompt_fields is not None
    assert sp.prompt_fields["persona"] == "Expert"
    assert sp.prompt_fields["thinking_style"] == "Step by step"
    assert sp.prompt_fields["instruction"] == "Rank items."


def test_to_job_search_point_includes_few_shot_block():
    """Few-shot examples are pre-rendered into prompt_fields."""
    from api.models.opt_search_point import FewShotExample
    osp = OptSearchPoint(
        instruction="Rank.",
        few_shot_examples=[FewShotExample(input="a", output="b")],
    )
    sp = osp.to_job_search_point()
    assert "few_shot_block" in sp.prompt_fields
    assert "Input: a" in sp.prompt_fields["few_shot_block"]


def test_derive_without_prompt_fields_carries_forward():
    """derive(pipeline_params=...) preserves existing prompt_fields."""
    sp = _make_jsp("Rank by relevance.")
    sp2 = sp.derive(pipeline_params={**sp.pipeline_params, "extra": 42})
    assert sp2.prompt_fields == sp.prompt_fields
    assert sp2.pipeline_params["extra"] == 42
