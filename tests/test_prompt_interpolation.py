"""Tests for prompt template variable interpolation."""

from promptpotter.application.scoring.sample_measurement import (
    extract_template_variables,
    interpolate_pipeline_params,
    interpolate_prompt,
)


class TestExtractTemplateVariables:
    def test_no_variables(self):
        assert extract_template_variables("plain text") == []

    def test_single_variable(self):
        assert extract_template_variables("Hello {{name}}") == ["name"]

    def test_multiple_variables(self):
        result = extract_template_variables("{{a}} and {{b}} and {{a}}")
        assert result == ["a", "b"]  # sorted, deduplicated

    def test_query_variable(self):
        assert extract_template_variables("Solve: {{query}}") == ["query"]


class TestInterpolatePrompt:
    def test_basic_substitution(self):
        result = interpolate_prompt("Hello {{name}}", {"name": "world"})
        assert result == "Hello world"

    def test_query_substitution(self):
        result = interpolate_prompt("Solve this: {{query}}", {"query": "What is 2+2?"})
        assert result == "Solve this: What is 2+2?"

    def test_multiple_variables(self):
        result = interpolate_prompt(
            "{{context}}\n\nQuestion: {{query}}",
            {"context": "Some passage", "query": "What happened?"},
        )
        assert result == "Some passage\n\nQuestion: What happened?"

    def test_no_templates_returns_unchanged(self):
        text = "Plain prompt without templates"
        result = interpolate_prompt(text, {"query": "ignored"})
        assert result == text

    def test_missing_variable_left_as_is(self):
        result = interpolate_prompt("Hello {{missing}}", {})
        assert result == "Hello {{missing}}"

    def test_ground_truth_excluded(self):
        result = interpolate_prompt("Answer is {{ground_truth}}", {"ground_truth": "42"})
        assert result == "Answer is {{ground_truth}}"

    def test_score_excluded(self):
        result = interpolate_prompt("Score: {{score}}", {"score": "0.9"})
        assert result == "Score: {{score}}"

    def test_numeric_value_stringified(self):
        result = interpolate_prompt("Max: {{max_tokens}}", {"max_tokens": 1024})
        assert result == "Max: 1024"

    def test_mixed_available_and_excluded(self):
        result = interpolate_prompt(
            "Q: {{query}} A: {{ground_truth}}",
            {"query": "2+2?", "ground_truth": "4"},
        )
        assert result == "Q: 2+2? A: {{ground_truth}}"


class TestInterpolatePipelineParams:
    def test_no_templates_returns_same_object(self):
        pp = {"steps": ["llm_only"], "llm_only": {"prompt": "plain", "temp": 0.0}}
        result = interpolate_pipeline_params(pp, {"query": "test"})
        assert result is pp  # same object — no copy needed

    def test_interpolates_prompt_in_node(self):
        pp = {
            "steps": ["llm_only"],
            "llm_only": {"prompt": "Solve: {{query}}", "temperature": 0.0},
        }
        qd = {"query": "What is 6*7?", "ground_truth": "42"}
        result = interpolate_pipeline_params(pp, qd)

        assert result["llm_only"]["prompt"] == "Solve: What is 6*7?"
        assert result["llm_only"]["temperature"] == 0.0
        # Original not mutated
        assert pp["llm_only"]["prompt"] == "Solve: {{query}}"

    def test_preserves_non_prompt_keys(self):
        pp = {
            "steps": ["node_a"],
            "node_a": {"prompt": "{{query}}", "model": "gpt-4", "max_tokens": 100},
        }
        result = interpolate_pipeline_params(pp, {"query": "hello"})
        assert result["node_a"]["model"] == "gpt-4"
        assert result["node_a"]["max_tokens"] == 100
        assert result["steps"] == ["node_a"]

    def test_multiple_nodes(self):
        pp = {
            "steps": ["a", "b"],
            "a": {"prompt": "Context: {{context}}", "temp": 0.0},
            "b": {"prompt": "plain prompt", "temp": 0.5},
        }
        qd = {"query": "q", "context": "some context", "ground_truth": "gt"}
        result = interpolate_pipeline_params(pp, qd)

        assert result["a"]["prompt"] == "Context: some context"
        assert result["b"]["prompt"] == "plain prompt"  # untouched

    def test_extra_dataset_fields(self):
        """Datasets can carry arbitrary extra fields for interpolation."""
        pp = {
            "steps": ["llm"],
            "llm": {"prompt": "{{passage}}\n\nQ: {{query}}"},
        }
        qd = {
            "query": "Who won?",
            "ground_truth": "Team A",
            "passage": "Team A beat Team B 3-1.",
        }
        result = interpolate_pipeline_params(pp, qd)
        assert result["llm"]["prompt"] == "Team A beat Team B 3-1.\n\nQ: Who won?"
