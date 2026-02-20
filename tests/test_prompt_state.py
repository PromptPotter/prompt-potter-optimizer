"""Tests for PromptState model and diff function."""
import json

import pytest
from pydantic import ValidationError

from api.models.prompt_state import (
    FewShotExample,
    PromptState,
    PromptStateDiff,
    diff,
)


def _baseline(**overrides):
    """Helper to create a baseline PromptState with sensible defaults."""
    defaults = {"prompt_text": "Classify the sentiment of: {{text}}"}
    defaults.update(overrides)
    return PromptState(**defaults)


class TestPromptStateCreation:
    def test_create_baseline(self):
        state = _baseline()
        assert state.prompt_text == "Classify the sentiment of: {{text}}"
        assert state.parent_id is None
        assert state.changes_description is None
        assert state.few_shot_examples == []
        assert state.parameters == {}

    def test_frozen(self):
        state = _baseline()
        with pytest.raises(ValidationError):
            state.prompt_text = "new text"

    def test_auto_generated_fields(self):
        s1 = _baseline()
        s2 = _baseline()
        assert s1.id != s2.id
        assert len(s1.id) == 32  # uuid4 hex
        assert "T" in s1.created_at  # ISO 8601

    def test_parameters_dict(self):
        state = _baseline(
            parameters={
                "temperature": 0.7,
                "threshold": 0.85,
                "model": "gpt-4",
                "retrieval_count": 3,
            }
        )
        assert state.parameters["temperature"] == 0.7
        assert state.parameters["retrieval_count"] == 3

    def test_few_shot_examples(self):
        examples = [
            FewShotExample(input="Great!", output="positive"),
            FewShotExample(
                input="Awful", output="negative", explanation="Negative sentiment word"
            ),
        ]
        state = _baseline(few_shot_examples=examples)
        assert len(state.few_shot_examples) == 2
        assert state.few_shot_examples[1].explanation == "Negative sentiment word"


class TestDerive:
    def test_derive_child(self):
        parent = _baseline(parameters={"temperature": 0.7})
        child = parent.derive(
            prompt_text="Improved: {{text}}",
            changes_description="Rewrote prompt prefix",
        )
        assert child.parent_id == parent.id
        assert child.id != parent.id
        assert child.prompt_text == "Improved: {{text}}"
        assert child.parameters == {"temperature": 0.7}  # inherited
        assert child.changes_description == "Rewrote prompt prefix"


class TestDiff:
    def test_diff_no_changes(self):
        a = _baseline(parameters={"temperature": 0.7})
        b = _baseline(
            prompt_text=a.prompt_text,
            parameters={"temperature": 0.7},
            few_shot_examples=list(a.few_shot_examples),
        )
        result = diff(a, b)
        assert result.prompt_text_changed is False
        assert result.prompt_text_diff is None
        assert result.few_shot_added == []
        assert result.few_shot_removed == []
        assert result.parameters_added == {}
        assert result.parameters_removed == {}
        assert result.parameters_changed == {}

    def test_diff_prompt_text(self):
        a = _baseline(prompt_text="Version A")
        b = _baseline(prompt_text="Version B")
        result = diff(a, b)
        assert result.prompt_text_changed is True
        assert "Version A" in result.prompt_text_diff
        assert "Version B" in result.prompt_text_diff

    def test_diff_parameters(self):
        a = _baseline(parameters={"temperature": 0.7, "old_key": "x"})
        b = _baseline(parameters={"temperature": 0.9, "new_key": "y"})
        result = diff(a, b)
        assert result.parameters_added == {"new_key": "y"}
        assert result.parameters_removed == {"old_key": "x"}
        assert result.parameters_changed == {
            "temperature": {"old": 0.7, "new": 0.9}
        }

    def test_diff_few_shot(self):
        ex1 = FewShotExample(input="hi", output="greeting")
        ex2 = FewShotExample(input="bye", output="farewell")
        a = _baseline(few_shot_examples=[ex1])
        b = _baseline(few_shot_examples=[ex2])
        result = diff(a, b)
        assert len(result.few_shot_added) == 1
        assert result.few_shot_added[0].input == "bye"
        assert len(result.few_shot_removed) == 1
        assert result.few_shot_removed[0].input == "hi"


class TestSerialization:
    def test_json_round_trip(self):
        state = _baseline(
            parameters={"temperature": 0.7},
            few_shot_examples=[FewShotExample(input="a", output="b")],
        )
        json_str = state.model_dump_json()
        restored = PromptState.model_validate_json(json_str)
        assert restored == state
        assert restored.id == state.id
        assert restored.created_at == state.created_at
