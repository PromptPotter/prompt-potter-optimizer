"""Tests for PipelineSchema model and pipeline discovery."""

import pytest

from api.models.pipeline_schema import (
    ObservationMapping,
    PipelineSchema,
    PipelineStep,
    StepOutputSchema,
    StepPromptMeta,
)
from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA, parse_pipeline_response


# ---------------------------------------------------------------------------
# Model construction
# ---------------------------------------------------------------------------

class TestPipelineStep:
    def test_defaults(self):
        step = PipelineStep(name="foo")
        assert step.type == "tool"
        assert step.runtime == "backend"
        assert step.short_circuit is False
        assert step.param_keys == set()
        assert step.observation_name is None
        assert step.observation_mappings == []
        assert step.langfuse_type == "span"
        assert step.output_schema is None
        assert step.prompt_meta is None

    def test_with_all_fields(self):
        step = PipelineStep(
            name="web_search",
            type="tool",
            runtime="backend",
            short_circuit=False,
            param_keys={"max_sites", "num_results"},
            observation_name="web_search",
            observation_mappings=[
                ObservationMapping(pipeline_key="web_sources", output_field="sources"),
            ],
            langfuse_type="tool",
        )
        assert step.name == "web_search"
        assert len(step.observation_mappings) == 1
        assert step.observation_mappings[0].pipeline_key == "web_sources"


class TestObservationMapping:
    def test_defaults(self):
        m = ObservationMapping(pipeline_key="foo")
        assert m.output_field is None
        assert m.is_llm is False

    def test_llm_mapping(self):
        m = ObservationMapping(pipeline_key="profile", is_llm=True)
        assert m.is_llm is True


class TestPipelineSchema:
    def test_empty_schema(self):
        schema = PipelineSchema()
        assert schema.steps == []
        assert schema.step_param_keys() == {}
        assert schema.obs_extraction_map() == {}
        assert schema.langfuse_type_map() == {}
        assert schema.backend_steps() == []
        assert schema.frontend_steps() == []

    def test_minimal_schema(self):
        schema = PipelineSchema(
            name="test",
            steps=[PipelineStep(name="step1", param_keys={"a", "b"})],
        )
        assert schema.step_param_keys() == {"step1": {"a", "b"}}


# ---------------------------------------------------------------------------
# Derivation methods
# ---------------------------------------------------------------------------

class TestDerivationMethods:
    @pytest.fixture()
    def schema(self):
        return PipelineSchema(
            name="test",
            steps=[
                PipelineStep(
                    name="search",
                    runtime="backend",
                    param_keys={"max_results"},
                    observation_name="search",
                    observation_mappings=[
                        ObservationMapping(pipeline_key="results", output_field="items"),
                    ],
                    langfuse_type="tool",
                ),
                PipelineStep(
                    name="rank",
                    runtime="backend",
                    param_keys={"temperature"},
                    observation_name="rank",
                    observation_mappings=[
                        ObservationMapping(pipeline_key="ranked", is_llm=True),
                    ],
                    langfuse_type="generation",
                ),
                PipelineStep(
                    name="cache",
                    runtime="frontend",
                    langfuse_type="span",
                ),
            ],
        )

    def test_step_param_keys(self, schema):
        result = schema.step_param_keys()
        assert result == {
            "search": {"max_results"},
            "rank": {"temperature"},
        }
        # cache has no param_keys, should be excluded
        assert "cache" not in result

    def test_obs_extraction_map(self, schema):
        result = schema.obs_extraction_map()
        assert set(result.keys()) == {"search", "rank"}
        assert result["search"][0].pipeline_key == "results"
        assert result["rank"][0].is_llm is True

    def test_langfuse_type_map(self, schema):
        result = schema.langfuse_type_map()
        assert result == {
            "search": "tool",
            "rank": "generation",
            "cache": "span",
        }

    def test_backend_steps(self, schema):
        backend = schema.backend_steps()
        assert [s.name for s in backend] == ["search", "rank"]

    def test_frontend_steps(self, schema):
        frontend = schema.frontend_steps()
        assert [s.name for s in frontend] == ["cache"]


# ---------------------------------------------------------------------------
# Consistency with hardcoded constants
# ---------------------------------------------------------------------------

class TestTermNormConsistency:
    """Verify TERMNORM_DEFAULT_SCHEMA matches every hardcoded constant it replaces."""

    def test_step_param_keys_matches_pipeline_step_params(self):
        """Must match PIPELINE_STEP_PARAMS in backend_client.py:29."""
        from api.services.backend_client import PIPELINE_STEP_PARAMS

        schema_params = TERMNORM_DEFAULT_SCHEMA.step_param_keys()
        assert schema_params == PIPELINE_STEP_PARAMS

    def test_obs_extraction_map_matches_obs_extraction_map(self):
        """Must match OBS_EXTRACTION_MAP in eval_dataset.py:25."""
        from api.services.search.eval_dataset import OBS_EXTRACTION_MAP, _ObsField

        schema_map = TERMNORM_DEFAULT_SCHEMA.obs_extraction_map()

        # Same keys
        assert set(schema_map.keys()) == set(OBS_EXTRACTION_MAP.keys())

        # Same mappings per key
        for obs_name in OBS_EXTRACTION_MAP:
            original_fields: list[_ObsField] = OBS_EXTRACTION_MAP[obs_name]
            schema_fields: list[ObservationMapping] = schema_map[obs_name]
            assert len(schema_fields) == len(original_fields), (
                f"Mapping count mismatch for '{obs_name}'"
            )
            for orig, schema_f in zip(original_fields, schema_fields):
                assert schema_f.pipeline_key == orig.pipeline_key
                assert schema_f.output_field == orig.output_field
                assert schema_f.is_llm == orig.is_llm

    def test_required_step_matches_required_pipeline_key(self):
        """Must match REQUIRED_PIPELINE_KEY in eval_dataset.py:42."""
        from api.services.search.eval_dataset import REQUIRED_PIPELINE_KEY

        assert TERMNORM_DEFAULT_SCHEMA.required_step == REQUIRED_PIPELINE_KEY

    def test_template_variables_matches_required_template_vars(self):
        """Must match REQUIRED_TEMPLATE_VARS in grid_core.py:43."""
        from api.services.search.grid_core import REQUIRED_TEMPLATE_VARS

        assert TERMNORM_DEFAULT_SCHEMA.template_variables == REQUIRED_TEMPLATE_VARS

    def test_dataset_name_matches_dataset_name(self):
        """Must match DATASET_NAME in langfuse_push.py:54."""
        from api.services.constants import DATASET_NAME

        assert TERMNORM_DEFAULT_SCHEMA.dataset_name == DATASET_NAME

    def test_langfuse_type_map_matches_pipeline_nodes(self):
        """Must match the as_type mapping in pipeline_nodes.py."""
        expected = {
            "web_search": "tool",
            "entity_profiling": "generation",
            "token_matching": "retriever",
            "llm_ranking": "generation",
        }
        lf_map = TERMNORM_DEFAULT_SCHEMA.langfuse_type_map()
        for step_name, expected_type in expected.items():
            assert lf_map[step_name] == expected_type

    def test_six_steps(self):
        assert len(TERMNORM_DEFAULT_SCHEMA.steps) == 6

    def test_step_order(self):
        names = [s.name for s in TERMNORM_DEFAULT_SCHEMA.steps]
        assert names == [
            "cache_lookup",
            "fuzzy_matching",
            "web_search",
            "entity_profiling",
            "token_matching",
            "llm_ranking",
        ]

    def test_frontend_steps(self):
        frontend = TERMNORM_DEFAULT_SCHEMA.frontend_steps()
        names = [s.name for s in frontend]
        assert names == ["cache_lookup", "fuzzy_matching"]
        assert all(s.short_circuit for s in frontend)

    def test_backend_steps(self):
        backend = TERMNORM_DEFAULT_SCHEMA.backend_steps()
        names = [s.name for s in backend]
        assert names == ["web_search", "entity_profiling", "token_matching", "llm_ranking"]


# ---------------------------------------------------------------------------
# Factory: parse_pipeline_response
# ---------------------------------------------------------------------------

class TestParsePipelineResponse:
    def test_empty_response(self):
        schema = parse_pipeline_response({})
        assert schema.name == ""
        assert schema.steps == []

    def test_known_pipeline(self):
        data = {
            "config": {
                "name": "TermNorm",
                "version": "2.0",
                "description": "Updated description",
            }
        }
        schema = parse_pipeline_response(data)
        assert schema.name == "termnorm"
        assert schema.version == "2.0"
        assert schema.description == "Updated description"
        # Should have all 6 steps from the known default
        assert len(schema.steps) == 6
        # Derivation methods should still work
        assert "web_search" in schema.step_param_keys()

    def test_known_pipeline_preserves_defaults(self):
        """Known pipeline with empty version/description keeps defaults."""
        data = {"config": {"name": "termnorm"}}
        schema = parse_pipeline_response(data)
        assert schema.version == "1.0"
        assert schema.description == TERMNORM_DEFAULT_SCHEMA.description

    def test_unknown_pipeline(self):
        data = {
            "config": {
                "name": "CustomPipeline",
                "version": "0.1",
                "steps": [
                    {"name": "step_a", "config": {"param1": 10, "param2": "x"}},
                    {"name": "step_b", "config": {}},
                ],
            }
        }
        schema = parse_pipeline_response(data)
        assert schema.name == "custompipeline"
        assert len(schema.steps) == 2
        assert schema.steps[0].param_keys == {"param1", "param2"}
        assert schema.steps[1].param_keys == set()

    def test_flat_config_format(self):
        """Response where the top-level dict IS the config."""
        data = {"name": "FlatPipeline", "version": "1.0", "steps": []}
        schema = parse_pipeline_response(data)
        assert schema.name == "flatpipeline"

    def test_top_level_resolved_unknown(self):
        """New format: top-level resolved_schemas/resolved_prompts dicts."""
        data = {
            "name": "CustomNodes",
            "version": "1.0",
            "nodes": {
                "step_a": {
                    "type": "LLMGeneration",
                    "config": {
                        "model": "gpt-4",
                        "temperature": 0.5,
                        "schema_family": "my_schema",
                        "schema_version": 1,
                        "prompt_family": "my_prompt",
                        "prompt_version": 2,
                    },
                },
                "step_b": {
                    "type": "DeterministicFunction",
                    "config": {"threshold": 0.8},
                },
            },
            "resolved_schemas": {
                "my_schema/1": {
                    "family": "my_schema",
                    "version": 1,
                    "description": "Test schema",
                    "fields": ["field1", "field2"],
                    "json_schema": {
                        "properties": {
                            "field1": {"type": "string", "description": "First field"},
                            "field2": {"type": "number"},
                        },
                    },
                },
            },
            "resolved_prompts": {
                "my_prompt/2": {
                    "family": "my_prompt",
                    "version": 2,
                    "description": "A test prompt",
                    "template_variables": ["input", "context"],
                    "template": "You are a {{input}} with {{context}}",
                },
            },
        }
        schema = parse_pipeline_response(data)
        assert schema.name == "customnodes"
        assert len(schema.steps) == 2

        # step_a should have resolved metadata from top-level dicts
        step_a = schema.steps[0]
        assert step_a.name == "step_a"
        assert step_a.output_schema is not None
        assert step_a.output_schema.family == "my_schema"
        assert step_a.output_schema.fields == ["field1", "field2"]
        assert step_a.output_schema.field_descriptions == {"field1": "First field"}
        assert step_a.prompt_meta is not None
        assert step_a.prompt_meta.family == "my_prompt"
        assert step_a.prompt_meta.version == 2
        assert step_a.prompt_meta.template_variables == ["input", "context"]
        assert step_a.prompt_meta.template == "You are a {{input}} with {{context}}"

        # step_b should have no metadata
        step_b = schema.steps[1]
        assert step_b.output_schema is None
        assert step_b.prompt_meta is None

    def test_inline_resolved_fallback(self):
        """Legacy format: inline _resolved_schema/_resolved_prompt in node config."""
        data = {
            "name": "CustomNodes",
            "version": "1.0",
            "nodes": {
                "step_a": {
                    "type": "LLMGeneration",
                    "config": {
                        "model": "gpt-4",
                        "schema_family": "my_schema",
                        "_resolved_schema": {
                            "family": "my_schema",
                            "version": 1,
                            "json_schema": {
                                "properties": {
                                    "field1": {"type": "string", "description": "First"},
                                },
                            },
                            "fields": ["field1"],
                        },
                        "prompt_family": "my_prompt",
                        "_resolved_prompt": {
                            "family": "my_prompt",
                            "version": 2,
                            "template_variables": ["input"],
                            "description": "Legacy prompt",
                        },
                    },
                },
            },
        }
        schema = parse_pipeline_response(data)
        step_a = schema.steps[0]
        assert step_a.output_schema is not None
        assert step_a.output_schema.family == "my_schema"
        assert step_a.prompt_meta is not None
        assert step_a.prompt_meta.family == "my_prompt"

    def test_top_level_resolved_known_pipeline(self):
        """Known pipeline with top-level resolved dicts merges metadata."""
        data = {
            "name": "TermNorm",
            "version": "v2.0",
            "nodes": {
                "web_search": {
                    "type": "ExternalService",
                    "config": {
                        "max_sites": 7,
                        "schema_family": "search_results",
                        "schema_version": 1,
                        "prompt_family": "search_prompt",
                        "prompt_version": 1,
                    },
                },
            },
            "resolved_schemas": {
                "search_results/1": {
                    "family": "search_results",
                    "version": 1,
                    "fields": ["sources"],
                    "json_schema": {
                        "properties": {
                            "sources": {"type": "array", "description": "URLs found"},
                        },
                    },
                },
            },
            "resolved_prompts": {
                "search_prompt/1": {
                    "family": "search_prompt",
                    "version": 1,
                    "template_variables": ["query"],
                    "description": "Search prompt",
                },
            },
        }
        schema = parse_pipeline_response(data)
        # Known pipeline should still have 6 steps
        assert len(schema.steps) == 6
        # web_search step should now have the resolved schema + prompt_meta merged
        ws = next(s for s in schema.steps if s.name == "web_search")
        assert ws.output_schema is not None
        assert ws.output_schema.family == "search_results"
        assert ws.output_schema.fields == ["sources"]
        assert ws.prompt_meta is not None
        assert ws.prompt_meta.family == "search_prompt"
        assert ws.prompt_meta.template_variables == ["query"]


# ---------------------------------------------------------------------------
# New models: StepOutputSchema, StepPromptMeta
# ---------------------------------------------------------------------------

class TestStepOutputSchema:
    def test_defaults(self):
        s = StepOutputSchema()
        assert s.family == ""
        assert s.version is None
        assert s.fields == []
        assert s.field_descriptions == {}

    def test_frozen(self):
        s = StepOutputSchema(family="entity_profile", fields=["a", "b"])
        with pytest.raises(Exception):
            s.family = "changed"

    def test_full_construction(self):
        s = StepOutputSchema(
            family="entity_profile",
            version=1,
            fields=["entity_name", "core_concept"],
            field_descriptions={"entity_name": "Canonical name"},
        )
        assert s.family == "entity_profile"
        assert s.version == 1
        assert len(s.fields) == 2
        assert s.field_descriptions["entity_name"] == "Canonical name"


class TestStepPromptMeta:
    def test_defaults(self):
        m = StepPromptMeta()
        assert m.family == ""
        assert m.version is None
        assert m.template_variables == []
        assert m.description == ""
        assert m.template == ""

    def test_frozen(self):
        m = StepPromptMeta(family="entity_profiling")
        with pytest.raises(Exception):
            m.family = "changed"

    def test_full_construction(self):
        m = StepPromptMeta(
            family="llm_ranking",
            version=1,
            template_variables=["core_concept", "entity_profile_json", "matches"],
            description="Rank candidates",
            template="You are a candidate evaluation expert.",
        )
        assert m.family == "llm_ranking"
        assert len(m.template_variables) == 3
        assert m.template == "You are a candidate evaluation expert."


# ---------------------------------------------------------------------------
# Default schema metadata
# ---------------------------------------------------------------------------

class TestTermNormDefaultNoHardcodedMetadata:
    """Verify TERMNORM_DEFAULT_SCHEMA does NOT carry registry-owned metadata."""

    def test_entity_profiling_no_hardcoded_metadata(self):
        ep = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "entity_profiling")
        assert ep.output_schema is None
        assert ep.prompt_meta is None

    def test_llm_ranking_no_hardcoded_metadata(self):
        lr = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "llm_ranking")
        assert lr.prompt_meta is None

    def test_entity_profiling_keeps_structural_fields(self):
        ep = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "entity_profiling")
        assert ep.langfuse_type == "generation"
        assert ep.observation_name == "entity_profiling"
        assert len(ep.observation_mappings) == 1
        assert ep.param_keys == {
            "raw_content_limit", "profiling_temperature", "profiling_max_tokens",
        }

    def test_llm_ranking_keeps_structural_fields(self):
        lr = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "llm_ranking")
        assert lr.langfuse_type == "generation"
        assert lr.observation_name == "llm_ranking"
        assert len(lr.observation_mappings) == 1


class TestKnownPipelineMetadataFromLiveResponse:
    """Verify that live response metadata lands on correct known-pipeline steps."""

    @pytest.fixture()
    def termnorm_live_response(self):
        return {
            "name": "TermNorm",
            "version": "2.0",
            "nodes": {
                "entity_profiling": {
                    "type": "LLMGeneration",
                    "config": {
                        "schema_family": "entity_profile",
                        "schema_version": 1,
                        "prompt_family": "entity_profiling",
                        "prompt_version": 1,
                    },
                },
                "llm_ranking": {
                    "type": "LLMGeneration",
                    "config": {
                        "prompt_family": "llm_ranking",
                        "prompt_version": 1,
                    },
                },
            },
            "resolved_schemas": {
                "entity_profile/1": {
                    "family": "entity_profile",
                    "version": 1,
                    "fields": ["entity_name", "core_concept"],
                    "json_schema": {
                        "properties": {
                            "entity_name": {
                                "type": "string",
                                "description": "Canonical name",
                            },
                            "core_concept": {
                                "type": "string",
                                "description": "Conceptual essence",
                            },
                        },
                    },
                },
            },
            "resolved_prompts": {
                "entity_profiling/1": {
                    "family": "entity_profiling",
                    "version": 1,
                    "template_variables": ["query", "format_string", "combined_text"],
                    "description": "Extract entity profile",
                },
                "llm_ranking/1": {
                    "family": "llm_ranking",
                    "version": 1,
                    "template_variables": [
                        "core_concept", "entity_profile_json", "matches",
                    ],
                    "description": "Rank candidates",
                },
            },
        }

    def test_entity_profiling_gets_both(self, termnorm_live_response):
        schema = parse_pipeline_response(termnorm_live_response)
        ep = next(s for s in schema.steps if s.name == "entity_profiling")
        assert ep.output_schema is not None
        assert ep.output_schema.family == "entity_profile"
        assert ep.output_schema.fields == ["entity_name", "core_concept"]
        assert ep.output_schema.field_descriptions == {
            "entity_name": "Canonical name",
            "core_concept": "Conceptual essence",
        }
        assert ep.prompt_meta is not None
        assert ep.prompt_meta.family == "entity_profiling"
        assert ep.prompt_meta.template_variables == [
            "query", "format_string", "combined_text",
        ]

    def test_llm_ranking_gets_prompt_meta(self, termnorm_live_response):
        schema = parse_pipeline_response(termnorm_live_response)
        lr = next(s for s in schema.steps if s.name == "llm_ranking")
        assert lr.prompt_meta is not None
        assert lr.prompt_meta.family == "llm_ranking"
        assert "core_concept" in lr.prompt_meta.template_variables

    def test_other_steps_unaffected(self, termnorm_live_response):
        schema = parse_pipeline_response(termnorm_live_response)
        ws = next(s for s in schema.steps if s.name == "web_search")
        assert ws.output_schema is None
        assert ws.prompt_meta is None
        assert len(schema.steps) == 6
