"""Tests for PromptState model."""
import pytest

from api.models.prompt_state import (
    LAYER_FIELDS,
    FewShotExample,
    PromptState,
    diff,
)


def test_create_and_derive():
    parent = PromptState(
        instruction="Classify: {{text}}",
        parameters={"temperature": 0.7},
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
    assert child.parameters == {"temperature": 0.7}  # inherited


def test_render():
    state = PromptState(
        persona="You are an expert.",
        instruction="Classify: {{text}}",
        thinking_style="Think step by step.",
        context="Should not appear",
        plan="Should not appear",
    )
    rendered = state.render()
    assert "You are an expert." in rendered
    assert "Classify:" in rendered
    assert "Think step by step." in rendered
    assert "context" not in rendered.lower()
    assert "plan" not in rendered.lower()


def test_diff():
    a = PromptState(instruction="Version A", context="ctx1", parameters={"k": 1})
    b = PromptState(instruction="Version B", context="ctx2", parameters={"k": 2})
    result = diff(a, b)
    assert result.generate_changed is True
    assert result.context_changed is True
    assert any(fc.field == "instruction" for fc in result.field_changes)
    assert result.parameters_changed == {"k": {"old": 1, "new": 2}}
