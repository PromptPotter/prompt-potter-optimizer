"""Tests for schema mutation parsing, resolution, and helpers."""

import pytest

from api.models.pipeline_schema import StepOutputSchema
from api.models.schema_mutation import (
    baseline_schema_from_step,
    parse_mutation_tuples,
    resolve_mutation,
    resolve_schema_variants,
)


# ---------------------------------------------------------------------------
# Shared baseline fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def baseline_schema():
    return {
        "type": "object",
        "properties": {
            "entity_name": {"type": "string", "description": "Canonical name"},
            "core_concept": {"type": "string", "description": "Main concept"},
            "notes": {"type": "string", "description": "Freeform notes"},
            "material_classification": {"type": "string", "description": "Material class"},
            "ranked_candidates": {
                "type": "object",
                "properties": {
                    "key_match_factors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key factors",
                    },
                    "spec_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Spec gaps",
                    },
                },
                "required": ["key_match_factors", "spec_gaps"],
            },
        },
        "required": ["entity_name", "core_concept", "notes", "material_classification"],
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParsing:
    def test_parse_remove_tuple(self):
        v = parse_mutation_tuples([("-", "notes")])
        assert len(v.mutations) == 1
        m = v.mutations[0]
        assert m.op == "remove"
        assert m.path == "notes"

    def test_parse_remove_nested(self):
        v = parse_mutation_tuples([("-", "ranked_candidates.key_match_factors")])
        m = v.mutations[0]
        assert m.op == "remove"
        assert m.path == "ranked_candidates.key_match_factors"

    def test_parse_add_tuple(self):
        v = parse_mutation_tuples([("+", "significance", "string", True, "Domain relevance")])
        m = v.mutations[0]
        assert m.op == "add"
        assert m.path == "significance"
        assert m.field_type == "string"
        assert m.required is True
        assert m.description == "Domain relevance"

    def test_parse_add_optional(self):
        v = parse_mutation_tuples([("+", "significance", "string", False, "desc")])
        m = v.mutations[0]
        assert m.required is False

    def test_parse_replace_tuple(self):
        v = parse_mutation_tuples([("~", "notes", "industry", "string", True, "Industry class")])
        m = v.mutations[0]
        assert m.op == "replace"
        assert m.path == "notes"
        assert m.new_field == "industry"
        assert m.field_type == "string"
        assert m.required is True
        assert m.description == "Industry class"

    def test_parse_bad_tuple_raises(self):
        with pytest.raises(ValueError):
            parse_mutation_tuples([("X", "field")])
        with pytest.raises(ValueError):
            parse_mutation_tuples([("-",)])
        with pytest.raises(ValueError):
            parse_mutation_tuples([("+", "f", "string")])
        with pytest.raises(ValueError):
            parse_mutation_tuples([("~", "old", "new", "string", True)])


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestResolution:
    def test_resolve_remove_top_level(self, baseline_schema):
        v = parse_mutation_tuples([("-", "notes")])
        result = resolve_mutation(baseline_schema, v)
        assert "notes" not in result["properties"]
        assert "notes" not in result["required"]
        assert "entity_name" in result["properties"]

    def test_resolve_remove_nested(self, baseline_schema):
        v = parse_mutation_tuples([("-", "ranked_candidates.key_match_factors")])
        result = resolve_mutation(baseline_schema, v)
        rc_props = result["properties"]["ranked_candidates"]["properties"]
        assert "key_match_factors" not in rc_props
        assert "spec_gaps" in rc_props
        rc_req = result["properties"]["ranked_candidates"]["required"]
        assert "key_match_factors" not in rc_req

    def test_resolve_add_required(self, baseline_schema):
        v = parse_mutation_tuples([("+", "industry", "string", True, "Industry class")])
        result = resolve_mutation(baseline_schema, v)
        assert "industry" in result["properties"]
        assert result["properties"]["industry"] == {
            "type": "string", "description": "Industry class",
        }
        assert "industry" in result["required"]

    def test_resolve_add_optional(self, baseline_schema):
        v = parse_mutation_tuples([("+", "industry", "string", False, "Industry class")])
        result = resolve_mutation(baseline_schema, v)
        assert "industry" in result["properties"]
        assert "industry" not in result["required"]

    def test_resolve_add_nested(self, baseline_schema):
        v = parse_mutation_tuples([
            ("+", "ranked_candidates.confidence", "number", False, "Confidence 0-1"),
        ])
        result = resolve_mutation(baseline_schema, v)
        rc_props = result["properties"]["ranked_candidates"]["properties"]
        assert "confidence" in rc_props
        assert rc_props["confidence"] == {"type": "number", "description": "Confidence 0-1"}
        rc_req = result["properties"]["ranked_candidates"]["required"]
        assert "confidence" not in rc_req

    def test_resolve_replace_required(self, baseline_schema):
        v = parse_mutation_tuples([
            ("~", "notes", "industry_sector", "string", True, "Industry sector"),
        ])
        result = resolve_mutation(baseline_schema, v)
        assert "notes" not in result["properties"]
        assert "notes" not in result["required"]
        assert "industry_sector" in result["properties"]
        assert "industry_sector" in result["required"]

    def test_resolve_replace_optional(self, baseline_schema):
        v = parse_mutation_tuples([
            ("~", "notes", "industry_sector", "string", False, "Industry sector"),
        ])
        result = resolve_mutation(baseline_schema, v)
        assert "notes" not in result["properties"]
        assert "notes" not in result["required"]
        assert "industry_sector" in result["properties"]
        assert "industry_sector" not in result["required"]

    def test_resolve_types(self, baseline_schema):
        types = {
            "string": {"type": "string"},
            "array": {"type": "array", "items": {"type": "string"}},
            "integer": {"type": "integer"},
            "number": {"type": "number"},
            "boolean": {"type": "boolean"},
            "object": {"type": "object"},
        }
        for type_name, expected_base in types.items():
            v = parse_mutation_tuples([("+", f"test_{type_name}", type_name, False, "desc")])
            result = resolve_mutation(baseline_schema, v)
            prop = result["properties"][f"test_{type_name}"]
            assert prop["type"] == expected_base["type"]
            if "items" in expected_base:
                assert prop["items"] == expected_base["items"]

    def test_baseline_at_index_0(self, baseline_schema):
        variants = [
            parse_mutation_tuples([("-", "notes")]),
            parse_mutation_tuples([("+", "x", "string", True, "desc")]),
        ]
        result = resolve_schema_variants(baseline_schema, variants)
        assert len(result) == 3
        assert result[0] == baseline_schema
        assert "notes" not in result[1]["properties"]
        assert "x" in result[2]["properties"]

    def test_remove_nonexistent_graceful(self, baseline_schema):
        v = parse_mutation_tuples([("-", "does_not_exist")])
        result = resolve_mutation(baseline_schema, v)
        assert result["properties"].keys() == baseline_schema["properties"].keys()

    def test_baseline_not_mutated(self, baseline_schema):
        v = parse_mutation_tuples([("-", "notes")])
        resolve_mutation(baseline_schema, v)
        assert "notes" in baseline_schema["properties"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_baseline_schema_from_step_prefers_json_schema(self):
        js = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        sos = StepOutputSchema(fields=["a", "b"], json_schema=js)
        result = baseline_schema_from_step(sos)
        assert result == js

    def test_baseline_schema_from_step_fallback(self):
        sos = StepOutputSchema(
            fields=["x", "y"],
            field_descriptions={"x": "Field X"},
        )
        result = baseline_schema_from_step(sos)
        assert result["type"] == "object"
        assert "x" in result["properties"]
        assert result["properties"]["x"]["description"] == "Field X"
        assert "y" in result["properties"]
        assert result["required"] == ["x", "y"]

    def test_render_label(self):
        v = parse_mutation_tuples([
            ("-", "notes"),
            ("-", "material_classification"),
        ])
        label = v.render_label()
        assert "('-', 'notes')" in label
        assert "('-', 'material_classification')" in label

        v2 = parse_mutation_tuples([("+", "industry", "string", True, "desc")])
        label2 = v2.render_label()
        assert "('+', 'industry', 'string', True, 'desc')" in label2

        v3 = parse_mutation_tuples([("+", "x", "array", False, "my desc")])
        label3 = v3.render_label()
        assert "('+', 'x', 'array', False, 'my desc')" in label3

        v4 = parse_mutation_tuples([("~", "notes", "sector", "string", True, "Industry")])
        label4 = v4.render_label()
        assert "('~', 'notes', 'sector', 'string', True, 'Industry')" in label4

    def test_render_label_nested(self):
        v = parse_mutation_tuples([("-", "ranked_candidates.key_match_factors")])
        assert "('-', 'ranked_candidates.key_match_factors')" in v.render_label()
