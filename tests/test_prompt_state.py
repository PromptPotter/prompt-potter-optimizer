"""Tests for PromptState model."""
from api.models.prompt_state import FewShotExample, PromptState, diff


def test_create_and_derive():
    parent = PromptState(
        instruction="Classify: {{text}}",
        optimizer_params={"temperature": 0.7},
    )
    assert parent.parent_id is None

    child = parent.derive(
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
    state = PromptState(
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


def test_diff():
    a = PromptState(instruction="Version A", optimizer_params={"k": 1})
    b = PromptState(instruction="Version B", optimizer_params={"k": 2})
    result = diff(a, b)
    assert result.generate_changed is True
    assert result.context_changed is True  # optimizer_params changed
    assert any(fc.field == "instruction" for fc in result.field_changes)
    assert result.optimizer_params_changed == {"k": {"old": 1, "new": 2}}


def test_diff_no_redundant_few_shot_field_change():
    """few_shot changes tracked only in dedicated fields, not in field_changes."""
    a = PromptState(instruction="same")
    b = PromptState(
        instruction="same",
        few_shot_examples=[FewShotExample(input="x", output="y")],
    )
    result = diff(a, b)
    assert result.generate_changed is True
    assert result.few_shot_added == [FewShotExample(input="x", output="y")]
    assert not any(fc.field == "few_shot_examples" for fc in result.field_changes)


def test_diff_no_redundant_parameters_field_change():
    """parameters changes tracked only in dedicated fields, not in field_changes."""
    a = PromptState(instruction="same", optimizer_params={"k": 1})
    b = PromptState(instruction="same", optimizer_params={"k": 2, "new_key": 3})
    result = diff(a, b)
    assert result.context_changed is True
    assert result.optimizer_params_changed == {"k": {"old": 1, "new": 2}}
    assert result.optimizer_params_added == {"new_key": 3}
    assert not any(fc.field == "optimizer_params" for fc in result.field_changes)
