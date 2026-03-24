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
