"""Tests for SearchPoint model."""
import pytest

from api.models.prompt_state import PromptState
from api.models.search_point import SearchPoint
from api.services.prompt_eval import eval_content_hash


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construct_with_defaults():
    ps = PromptState(instruction="Rank by relevance.")
    sp = SearchPoint(prompt_state=ps)
    assert sp.prompt_state is ps
    assert sp.model == ""
    assert sp.temperature == 0.0
    assert sp.pipeline_params is None


def test_construct_with_all_fields():
    ps = PromptState(instruction="Rank by relevance.")
    sp = SearchPoint(
        prompt_state=ps,
        model="llama-3",
        temperature=0.5,
        pipeline_params={"steps": ["llm_ranking"]},
    )
    assert sp.model == "llama-3"
    assert sp.temperature == 0.5
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}


# ---------------------------------------------------------------------------
# Frozen (immutable)
# ---------------------------------------------------------------------------


def test_frozen():
    ps = PromptState(instruction="test")
    sp = SearchPoint(prompt_state=ps)
    with pytest.raises(Exception):
        sp.model = "new-model"


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


def test_render_delegates():
    ps = PromptState(
        persona="Expert",
        instruction="Rank candidates.",
    )
    sp = SearchPoint(prompt_state=ps)
    assert sp.render() == ps.render()
    assert "Expert" in sp.render()
    assert "Rank candidates." in sp.render()


# ---------------------------------------------------------------------------
# content_hash()
# ---------------------------------------------------------------------------


def test_content_hash_matches_eval_content_hash():
    ps = PromptState(instruction="Rank by relevance.")
    sp = SearchPoint(prompt_state=ps, model="llama-3", temperature=0.5)
    eval_data = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
        {"query": "ibuprofen", "ground_truth": "Ibuprofen"},
    ]
    expected = eval_content_hash(ps.render(), eval_data, "llama-3", 0.5)
    assert sp.content_hash(eval_data) == expected


def test_content_hash_matches_eval_content_hash_with_pipeline_params():
    ps = PromptState(instruction="Rank by relevance.")
    pp = {"steps": ["llm_ranking"], "ranking_temperature": 0.5}
    sp = SearchPoint(prompt_state=ps, model="llama-3", temperature=0.5, pipeline_params=pp)
    eval_data = [
        {"query": "aspirin", "ground_truth": "Aspirin"},
    ]
    expected = eval_content_hash(ps.render(), eval_data, "llama-3", 0.5, pp)
    assert sp.content_hash(eval_data) == expected


def test_content_hash_differs_with_model():
    ps = PromptState(instruction="Rank by relevance.")
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = SearchPoint(prompt_state=ps, model="model-a")
    sp_b = SearchPoint(prompt_state=ps, model="model-b")
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)


def test_content_hash_differs_with_temperature():
    ps = PromptState(instruction="Rank by relevance.")
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = SearchPoint(prompt_state=ps, temperature=0.0)
    sp_b = SearchPoint(prompt_state=ps, temperature=0.7)
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)


def test_content_hash_differs_with_pipeline_params():
    ps = PromptState(instruction="Rank by relevance.")
    eval_data = [{"query": "q", "ground_truth": "a"}]
    sp_a = SearchPoint(prompt_state=ps, pipeline_params={"steps": ["llm_ranking"]})
    sp_b = SearchPoint(prompt_state=ps, pipeline_params={"steps": ["fuzzy_matching"]})
    sp_none = SearchPoint(prompt_state=ps)
    assert sp_a.content_hash(eval_data) != sp_b.content_hash(eval_data)
    assert sp_a.content_hash(eval_data) != sp_none.content_hash(eval_data)


# ---------------------------------------------------------------------------
# derive()
# ---------------------------------------------------------------------------


def test_derive_prompt_state_field():
    """PromptState fields route to prompt_state.derive()."""
    ps = PromptState(instruction="original", persona="")
    sp = SearchPoint(prompt_state=ps, model="llama-3", temperature=0.5)
    sp2 = sp.derive(instruction="modified")
    assert sp2.prompt_state.instruction == "modified"
    assert sp2.prompt_state.parent_id == ps.id
    assert sp2.model == "llama-3"
    assert sp2.temperature == 0.5


def test_derive_search_point_field():
    """SearchPoint-level fields are updated directly."""
    ps = PromptState(instruction="original")
    sp = SearchPoint(prompt_state=ps, model="llama-3", temperature=0.5)
    sp2 = sp.derive(model="gpt-4", temperature=0.9)
    assert sp2.model == "gpt-4"
    assert sp2.temperature == 0.9
    assert sp2.prompt_state.instruction == "original"
    assert sp2.prompt_state.id == ps.id  # same PromptState, not derived


def test_derive_mixed_fields():
    """Both PromptState and SearchPoint fields in one derive call."""
    ps = PromptState(instruction="original", persona="Expert")
    sp = SearchPoint(prompt_state=ps, model="llama-3", temperature=0.5)
    sp2 = sp.derive(instruction="modified", model="gpt-4")
    assert sp2.prompt_state.instruction == "modified"
    assert sp2.prompt_state.parent_id == ps.id
    assert sp2.model == "gpt-4"
    assert sp2.temperature == 0.5  # unchanged


def test_derive_prompt_state_replacement():
    """Passing prompt_state directly replaces it."""
    ps1 = PromptState(instruction="first")
    ps2 = PromptState(instruction="second")
    sp = SearchPoint(prompt_state=ps1, model="llama-3")
    sp2 = sp.derive(prompt_state=ps2)
    assert sp2.prompt_state.id == ps2.id
    assert sp2.prompt_state.instruction == "second"
    assert sp2.model == "llama-3"


def test_derive_pipeline_params():
    sp = SearchPoint(
        prompt_state=PromptState(instruction="test"),
        pipeline_params={"steps": ["llm_ranking"]},
    )
    sp2 = sp.derive(pipeline_params={"steps": ["fuzzy_matching"]})
    assert sp2.pipeline_params == {"steps": ["fuzzy_matching"]}
    assert sp.pipeline_params == {"steps": ["llm_ranking"]}  # original unchanged


def test_derive_returns_new_frozen_instance():
    sp = SearchPoint(prompt_state=PromptState(instruction="test"))
    sp2 = sp.derive(model="new-model")
    assert sp is not sp2
    with pytest.raises(Exception):
        sp2.model = "another"
