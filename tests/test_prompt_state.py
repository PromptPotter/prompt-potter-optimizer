"""Tests for PromptState model and diff function."""
import json

import pytest
from pydantic import ValidationError

from api.models.prompt_state import (
    LAYER_FIELDS,
    FieldChange,
    FewShotExample,
    OptimizationDefaults,
    PromptState,
    diff,
)


def _baseline(**overrides):
    """Helper to create a baseline PromptState with sensible defaults."""
    defaults = {"instruction": "Classify the sentiment of: {{text}}"}
    defaults.update(overrides)
    return PromptState(**defaults)


class TestPromptStateCreation:
    def test_create_baseline(self):
        state = _baseline()
        assert state.instruction == "Classify the sentiment of: {{text}}"
        assert state.parent_id is None
        assert state.changes_description is None
        assert state.few_shot_examples == []
        assert state.parameters == {}
        assert state.persona == ""
        assert state.task_intent == ""
        assert state.problem_description == ""
        assert state.thinking_style == ""
        assert state.answer_format == ""
        assert state.context == ""
        assert state.plan == ""

    def test_frozen(self):
        state = _baseline()
        with pytest.raises(ValidationError):
            state.instruction = "new text"

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

    def test_all_layer_fields(self):
        state = PromptState(
            persona="You are a helpful assistant.",
            task_intent="Classify sentiment",
            problem_description="Given a text, determine positive/negative.",
            instruction="Classify: {{text}}",
            thinking_style="Step by step",
            answer_format="Return JSON",
            context="Dataset: movie reviews",
            parameters={"temperature": 0.5},
            plan="Focus on edge cases first",
        )
        assert state.persona == "You are a helpful assistant."
        assert state.context == "Dataset: movie reviews"
        assert state.plan == "Focus on edge cases first"


class TestDerive:
    def test_derive_child(self):
        parent = _baseline(parameters={"temperature": 0.7})
        child = parent.derive(
            instruction="Improved: {{text}}",
            changes_description="Rewrote prompt instruction",
        )
        assert child.parent_id == parent.id
        assert child.id != parent.id
        assert child.instruction == "Improved: {{text}}"
        assert child.parameters == {"temperature": 0.7}  # inherited
        assert child.changes_description == "Rewrote prompt instruction"

    def test_derive_layer2_fields(self):
        parent = _baseline(context="original context")
        child = parent.derive(
            context="updated context with critiques",
            parameters={"temperature": 0.9},
        )
        assert child.context == "updated context with critiques"
        assert child.parameters == {"temperature": 0.9}
        assert child.instruction == parent.instruction  # inherited

    def test_derive_layer3_fields(self):
        parent = _baseline(plan="original plan")
        child = parent.derive(plan="revised strategy: focus on recall")
        assert child.plan == "revised strategy: focus on recall"
        assert child.parent_id == parent.id

    def test_derive_across_layers(self):
        parent = PromptState(
            instruction="base",
            context="ctx",
            plan="plan",
        )
        child = parent.derive(
            instruction="new instruction",
            context="new context",
            plan="new plan",
        )
        assert child.instruction == "new instruction"
        assert child.context == "new context"
        assert child.plan == "new plan"


class TestDiff:
    def test_diff_no_changes(self):
        a = _baseline(parameters={"temperature": 0.7})
        b = _baseline(
            instruction=a.instruction,
            parameters={"temperature": 0.7},
            few_shot_examples=list(a.few_shot_examples),
        )
        result = diff(a, b)
        assert result.generate_changed is False
        assert result.context_changed is False
        assert result.plan_changed is False
        assert result.field_changes == []
        assert result.few_shot_added == []
        assert result.few_shot_removed == []
        assert result.parameters_added == {}
        assert result.parameters_removed == {}
        assert result.parameters_changed == {}

    def test_diff_instruction(self):
        a = _baseline(instruction="Version A")
        b = _baseline(instruction="Version B")
        result = diff(a, b)
        assert result.generate_changed is True
        assert result.context_changed is False
        assert result.plan_changed is False
        assert any(fc.field == "instruction" for fc in result.field_changes)
        assert result.rendered_diff is not None
        assert "Version A" in result.rendered_diff
        assert "Version B" in result.rendered_diff

    def test_diff_parameters(self):
        a = _baseline(parameters={"temperature": 0.7, "old_key": "x"})
        b = _baseline(parameters={"temperature": 0.9, "new_key": "y"})
        result = diff(a, b)
        assert result.context_changed is True
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
        assert result.generate_changed is True
        assert len(result.few_shot_added) == 1
        assert result.few_shot_added[0].input == "bye"
        assert len(result.few_shot_removed) == 1
        assert result.few_shot_removed[0].input == "hi"

    def test_diff_layer_flags(self):
        a = PromptState(instruction="a", context="ctx1", plan="plan1")
        b = PromptState(instruction="b", context="ctx2", plan="plan2")
        result = diff(a, b)
        assert result.generate_changed is True
        assert result.context_changed is True
        assert result.plan_changed is True
        layers = {fc.layer for fc in result.field_changes}
        assert "generate" in layers
        assert "refine_context" in layers
        assert "modify_plan" in layers

    def test_diff_field_change_structure(self):
        a = _baseline(persona="old persona")
        b = _baseline(persona="new persona")
        result = diff(a, b)
        fc = [c for c in result.field_changes if c.field == "persona"][0]
        assert fc.layer == "generate"
        assert fc.old == "old persona"
        assert fc.new == "new persona"


class TestRender:
    def test_render_empty(self):
        state = PromptState()
        assert state.render() == ""

    def test_render_single_field(self):
        state = PromptState(instruction="Do the thing")
        assert state.render() == "Do the thing"

    def test_render_all_fields(self):
        state = PromptState(
            persona="You are an expert.",
            task_intent="Classify items",
            problem_description="Given input, classify it.",
            instruction="Classify: {{text}}",
            thinking_style="Think step by step.",
            answer_format="Return JSON.",
        )
        rendered = state.render()
        assert "You are an expert." in rendered
        assert "Classify items" in rendered
        assert "Return JSON." in rendered
        # Fields separated by double newlines
        parts = rendered.split("\n\n")
        assert len(parts) == 6

    def test_render_skips_empties(self):
        state = PromptState(
            persona="Expert",
            instruction="Do it",
        )
        rendered = state.render()
        parts = rendered.split("\n\n")
        assert len(parts) == 2
        assert parts[0] == "Expert"
        assert parts[1] == "Do it"

    def test_render_with_few_shot(self):
        state = PromptState(
            instruction="Classify:",
            few_shot_examples=[
                FewShotExample(input="hello", output="greeting"),
                FewShotExample(input="bye", output="farewell", explanation="parting"),
            ],
        )
        rendered = state.render()
        assert "Input: hello\nOutput: greeting" in rendered
        assert "Input: bye\nOutput: farewell" in rendered
        assert "Explanation: parting" in rendered

    def test_render_excludes_layer2_and_layer3(self):
        state = PromptState(
            instruction="Do it",
            context="This should not appear in render",
            plan="Neither should this",
            parameters={"temperature": 0.5},
        )
        rendered = state.render()
        assert rendered == "Do it"
        assert "context" not in rendered.lower()
        assert "plan" not in rendered.lower()


class TestOptimizationDefaults:
    def test_defaults(self):
        od = OptimizationDefaults()
        assert od.n_variants == 5
        assert od.creativity == 0.7
        assert od.selection_strategy == "best_of_n"
        assert od.improvement_threshold == 0.01
        assert od.max_context_refinements == 3
        assert od.max_plan_modifications == 2
        assert od.max_iterations == 5
        assert od.target_score is None
        assert od.seed is None

    def test_custom_config(self):
        od = OptimizationDefaults(
            n_variants=10,
            creativity=0.9,
            selection_strategy="tournament",
            max_iterations=20,
            target_score=0.95,
            seed=42,
        )
        assert od.n_variants == 10
        assert od.creativity == 0.9
        assert od.selection_strategy == "tournament"
        assert od.max_iterations == 20
        assert od.target_score == 0.95
        assert od.seed == 42

    def test_serialization_round_trip(self):
        od = OptimizationDefaults(n_variants=3, seed=123)
        restored = OptimizationDefaults.model_validate_json(od.model_dump_json())
        assert restored == od


class TestLayerFields:
    def test_layer_keys(self):
        assert set(LAYER_FIELDS.keys()) == {"generate", "refine_context", "modify_plan"}

    def test_generate_fields(self):
        expected = {
            "persona", "task_intent", "problem_description",
            "instruction", "thinking_style", "answer_format",
            "few_shot_examples",
        }
        assert set(LAYER_FIELDS["generate"]) == expected

    def test_refine_context_fields(self):
        assert set(LAYER_FIELDS["refine_context"]) == {"context", "parameters"}

    def test_modify_plan_fields(self):
        assert set(LAYER_FIELDS["modify_plan"]) == {"plan"}

    def test_all_layer_fields_exist_on_model(self):
        model_fields = set(PromptState.model_fields.keys())
        for layer, fields in LAYER_FIELDS.items():
            for field in fields:
                assert field in model_fields, (
                    f"LAYER_FIELDS['{layer}'] contains '{field}' "
                    f"which is not a PromptState field"
                )


class TestSerialization:
    def test_json_round_trip(self):
        state = _baseline(
            parameters={"temperature": 0.7},
            few_shot_examples=[FewShotExample(input="a", output="b")],
            persona="Expert",
            context="some context",
            plan="the plan",
        )
        json_str = state.model_dump_json()
        restored = PromptState.model_validate_json(json_str)
        assert restored == state
        assert restored.id == state.id
        assert restored.created_at == state.created_at
        assert restored.persona == "Expert"
        assert restored.context == "some context"
        assert restored.plan == "the plan"

    def test_field_change_serialization(self):
        fc = FieldChange(field="instruction", layer="generate", old="a", new="b")
        data = json.loads(fc.model_dump_json())
        assert data["field"] == "instruction"
        assert data["layer"] == "generate"
