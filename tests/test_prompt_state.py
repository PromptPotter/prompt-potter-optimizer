"""Tests for OptSearchPoint model."""
from api.models.opt_search_point import FewShotExample, OptSearchPoint


def test_create_and_derive():
    parent = OptSearchPoint(
        instruction="Classify: {{text}}",
        optimizer_params={"temperature": 0.7},
    )
    assert parent.parent_id is None

    child = parent.derive_candidate(
        instruction="Improved: {{text}}",
        persona="Expert",
        changes_description="Rewrote instruction",
    )
    assert child.parent_id == parent.id
    assert child.id != parent.id
    assert child.instruction == "Improved: {{text}}"
    assert child.persona == "Expert"
    assert child.optimizer_params == {"temperature": 0.7}  # inherited


def test_render():
    state = OptSearchPoint(
        persona="You are an expert.",
        instruction="Classify: {{text}}",
        thinking_style="Think step by step.",
        plan="Should not appear",
    )
    rendered = state.render()
    assert "You are an expert." in rendered
    assert "Classify:" in rendered
    assert "Think step by step." in rendered
    assert "context" not in rendered.lower()
    assert "plan" not in rendered.lower()


def test_few_shot_in_derive():
    a = OptSearchPoint(instruction="same")
    b = a.derive_candidate(
        few_shot_examples=[FewShotExample(input="x", output="y")],
    )
    assert len(b.few_shot_examples) == 1
    assert b.few_shot_examples[0].input == "x"
    assert b.parent_id == a.id


def test_derive_preserves_optimizer_params():
    a = OptSearchPoint(instruction="same", optimizer_params={"k": 1})
    b = a.derive_candidate(optimizer_params={"k": 2, "new_key": 3})
    assert b.optimizer_params == {"k": 2, "new_key": 3}
    assert a.optimizer_params == {"k": 1}  # original unchanged
