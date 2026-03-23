"""Tests for PipelineSchema model and pipeline discovery.

Verifies derivation methods, consistency with hardcoded constants,
parse_pipeline_response factory, and registry metadata flow.
"""

from api.models.pipeline_schema import (
    ObservationMapping,
    PipelineSchema,
    PipelineStep,
)
from api.services.pipeline_discovery import TERMNORM_DEFAULT_SCHEMA, parse_pipeline_response


# ---------------------------------------------------------------------------
# Derivation methods
# ---------------------------------------------------------------------------


def test_derivation_methods():
    """step_param_keys, obs_extraction_map, langfuse_type_map, runtime filtering."""
    schema = PipelineSchema(
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

    # step_param_keys
    assert schema.step_param_keys() == {
        "search": {"max_results"},
        "rank": {"temperature"},
    }
    assert "cache" not in schema.step_param_keys()

    # obs_extraction_map
    obs_map = schema.obs_extraction_map()
    assert set(obs_map.keys()) == {"search", "rank"}
    assert obs_map["search"][0].pipeline_key == "results"
    assert obs_map["rank"][0].is_llm is True

    # langfuse_type_map
    assert schema.langfuse_type_map() == {
        "search": "tool", "rank": "generation", "cache": "span",
    }

    # runtime filtering
    assert [s.name for s in schema.backend_steps()] == ["search", "rank"]
    assert [s.name for s in schema.frontend_steps()] == ["cache"]


# ---------------------------------------------------------------------------
# Consistency with hardcoded constants
# ---------------------------------------------------------------------------


class TestTermNormConsistency:
    """Verify TERMNORM_DEFAULT_SCHEMA matches every hardcoded constant."""

    def test_override_map_covers_param_keys(self):
        """Every param_keys entry has a corresponding override_map entry."""
        for step in TERMNORM_DEFAULT_SCHEMA.steps:
            for pk in step.param_keys:
                assert pk in step.override_map, (
                    f"Step '{step.name}': param_key '{pk}' missing from override_map"
                )

    def test_obs_extraction_map_matches(self):
        from api.services.search.eval_dataset import OBS_EXTRACTION_MAP

        schema_map = TERMNORM_DEFAULT_SCHEMA.obs_extraction_map()
        assert set(schema_map.keys()) == set(OBS_EXTRACTION_MAP.keys())

        for obs_name in OBS_EXTRACTION_MAP:
            original_fields = OBS_EXTRACTION_MAP[obs_name]
            schema_fields = schema_map[obs_name]
            assert len(schema_fields) == len(original_fields)
            for orig, schema_f in zip(original_fields, schema_fields):
                assert schema_f.pipeline_key == orig.pipeline_key
                assert schema_f.output_field == orig.output_field
                assert schema_f.is_llm == orig.is_llm

    def test_required_step_matches(self):
        from api.services.search.eval_dataset import REQUIRED_PIPELINE_KEY
        assert TERMNORM_DEFAULT_SCHEMA.required_step == REQUIRED_PIPELINE_KEY

    def test_template_variables(self):
        expected = {"{{core_concept}}", "{{entity_profile_json}}", "{{matches}}"}
        assert TERMNORM_DEFAULT_SCHEMA.template_variables == expected

    def test_dataset_name_matches(self):
        from api.services.constants import DATASET_NAME
        assert TERMNORM_DEFAULT_SCHEMA.dataset_name == DATASET_NAME

    def test_langfuse_type_map_matches(self):
        expected = {
            "web_search": "tool",
            "entity_profiling": "generation",
            "token_matching": "retriever",
            "llm_ranking": "generation",
        }
        lf_map = TERMNORM_DEFAULT_SCHEMA.langfuse_type_map()
        for step_name, expected_type in expected.items():
            assert lf_map[step_name] == expected_type

    def test_fuzzy_matching_param_keys(self):
        fm = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "fuzzy_matching")
        assert fm.param_keys == {"fuzzy_threshold", "fuzzy_scorer"}

    def test_runtime_classification(self):
        frontend = [s.name for s in TERMNORM_DEFAULT_SCHEMA.frontend_steps()]
        assert frontend == ["cache_lookup", "fuzzy_matching"]
        assert all(s.short_circuit for s in TERMNORM_DEFAULT_SCHEMA.frontend_steps())

        backend = [s.name for s in TERMNORM_DEFAULT_SCHEMA.backend_steps()]
        assert backend == ["web_search", "entity_profiling", "token_matching", "llm_ranking"]


# ---------------------------------------------------------------------------
# parse_pipeline_response
# ---------------------------------------------------------------------------


class TestParsePipelineResponse:
    def test_known_pipeline(self):
        data = {"config": {"name": "TermNorm", "version": "2.0", "description": "Updated"}}
        schema = parse_pipeline_response(data)
        assert schema.name == "termnorm"
        assert schema.version == "2.0"
        assert len(schema.steps) == 6
        assert "web_search" in schema.step_param_keys()

    def test_unknown_pipeline(self):
        data = {
            "config": {
                "name": "CustomPipeline",
                "version": "0.1",
                "nodes": {
                    "step_a": {
                        "type": "tool",
                        "config": {"param1": 10, "param2": "x"},
                    },
                    "step_b": {"type": "tool", "config": {}},
                },
            }
        }
        schema = parse_pipeline_response(data)
        assert schema.name == "custompipeline"
        assert len(schema.steps) == 2
        step_a = next(s for s in schema.steps if s.name == "step_a")
        assert step_a.param_keys == {"param1", "param2"}

    def test_top_level_resolved_unknown(self):
        """New format: top-level resolved_schemas/resolved_prompts dicts."""
        data = {
            "name": "CustomNodes",
            "version": "1.0",
            "nodes": {
                "step_a": {
                    "type": "LLMGeneration",
                    "config": {
                        "schema_family": "my_schema", "schema_version": 1,
                        "prompt_family": "my_prompt", "prompt_version": 2,
                    },
                },
                "step_b": {"type": "DeterministicFunction", "config": {"threshold": 0.8}},
            },
            "resolved_schemas": {
                "my_schema/1": {
                    "family": "my_schema", "version": 1, "fields": ["field1", "field2"],
                    "json_schema": {"properties": {
                        "field1": {"type": "string", "description": "First field"},
                        "field2": {"type": "number"},
                    }},
                },
            },
            "resolved_prompts": {
                "my_prompt/2": {
                    "family": "my_prompt", "version": 2,
                    "template_variables": ["input", "context"],
                    "template": "You are a {{input}} with {{context}}",
                },
            },
        }
        schema = parse_pipeline_response(data)
        assert len(schema.steps) == 2

        step_a = schema.steps[0]
        assert step_a.output_schema is not None
        assert step_a.output_schema.family == "my_schema"
        assert step_a.output_schema.fields == ["field1", "field2"]
        assert step_a.output_schema.field_descriptions == {"field1": "First field"}
        assert step_a.prompt_meta is not None
        assert step_a.prompt_meta.template_variables == ["input", "context"]

        assert schema.steps[1].output_schema is None

    def test_top_level_resolved_known_pipeline(self):
        """Known pipeline with top-level resolved dicts merges metadata."""
        data = {
            "name": "TermNorm", "version": "v2.0",
            "nodes": {
                "web_search": {
                    "type": "ExternalService",
                    "config": {
                        "schema_family": "search_results", "schema_version": 1,
                        "prompt_family": "search_prompt", "prompt_version": 1,
                    },
                },
            },
            "resolved_schemas": {
                "search_results/1": {
                    "family": "search_results", "version": 1, "fields": ["sources"],
                    "json_schema": {"properties": {
                        "sources": {"type": "array", "description": "URLs found"},
                    }},
                },
            },
            "resolved_prompts": {
                "search_prompt/1": {
                    "family": "search_prompt", "version": 1,
                    "template_variables": ["query"],
                },
            },
        }
        schema = parse_pipeline_response(data)
        assert len(schema.steps) == 6
        ws = next(s for s in schema.steps if s.name == "web_search")
        assert ws.output_schema is not None
        assert ws.output_schema.family == "search_results"
        assert ws.prompt_meta is not None
        assert ws.prompt_meta.template_variables == ["query"]


# ---------------------------------------------------------------------------
# Default schema — no hardcoded registry metadata
# ---------------------------------------------------------------------------


def test_termnorm_default_no_hardcoded_metadata():
    """LLM steps have structural fields but no registry-owned output_schema/prompt_meta."""
    ep = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "entity_profiling")
    lr = next(s for s in TERMNORM_DEFAULT_SCHEMA.steps if s.name == "llm_ranking")

    # No hardcoded registry metadata
    assert ep.output_schema is None
    assert ep.prompt_meta is None
    assert lr.prompt_meta is None

    # Structural fields preserved
    assert ep.langfuse_type == "generation"
    assert ep.observation_name == "entity_profiling"
    assert len(ep.observation_mappings) == 1
    assert ep.param_keys == {
        "raw_content_limit", "profiling_temperature", "profiling_max_tokens",
        "profiling_prompt", "profiling_schema", "profiling_model",
    }
    assert lr.langfuse_type == "generation"


# ---------------------------------------------------------------------------
# Live response metadata flow
# ---------------------------------------------------------------------------


def test_data_envelope_unwrap():
    """TermNorm wraps response in {"status": "success", "data": {...}}.

    parse_pipeline_response must unwrap the "data" envelope to find the
    pipeline name, nodes, and resolved metadata.
    """
    data = {
        "status": "success",
        "data": {
            "name": "TermNorm",
            "version": "2.0",
            "nodes": {
                "entity_profiling": {
                    "type": "LLMGeneration",
                    "config": {
                        "schema_family": "entity_profile", "schema_version": 1,
                        "profiling_temperature": 0.3,
                        "profiling_model": "llama-4-maverick",
                        "raw_content_limit": 4000,
                        "_internal_flag": True,
                    },
                },
            },
            "resolved_schemas": {
                "entity_profile/1": {
                    "family": "entity_profile", "version": 1,
                    "fields": ["entity_name", "core_concept"],
                    "json_schema": {"properties": {
                        "entity_name": {"type": "string", "description": "Canonical name"},
                        "core_concept": {"type": "string", "description": "Conceptual essence"},
                    }},
                },
            },
        },
    }
    schema = parse_pipeline_response(data)

    # Must match known pipeline (not "unknown pipeline ''")
    assert schema.name == "termnorm"
    assert len(schema.steps) == 6

    # Resolved metadata must be merged
    ep = next(s for s in schema.steps if s.name == "entity_profiling")
    assert ep.output_schema is not None
    assert ep.output_schema.fields == ["entity_name", "core_concept"]
    assert ep.output_schema.field_descriptions == {
        "entity_name": "Canonical name", "core_concept": "Conceptual essence",
    }

    # current_config populated from node config, filtered to param_keys only
    assert ep.current_config == {
        "profiling_temperature": 0.3,
        "profiling_model": "llama-4-maverick",
        "raw_content_limit": 4000,
    }
    # Internal keys and non-param keys excluded
    assert "_internal_flag" not in ep.current_config
    assert "schema_family" not in ep.current_config


def test_unknown_pipeline_current_config():
    """Unknown pipeline path captures all non-internal config keys as current_config."""
    data = {
        "name": "CustomPipeline",
        "version": "1.0",
        "nodes": {
            "my_llm": {
                "type": "LLMGeneration",
                "config": {
                    "temperature": 0.5,
                    "model": "gpt-4",
                    "max_tokens": 1000,
                    "_cache_key": "abc",
                },
            },
            "my_retriever": {
                "type": "Retriever",
                "config": {
                    "top_k": 10,
                    "threshold": 0.8,
                },
            },
        },
    }
    schema = parse_pipeline_response(data)

    llm_step = next(s for s in schema.steps if s.name == "my_llm")
    assert llm_step.current_config == {
        "temperature": 0.5,
        "model": "gpt-4",
        "max_tokens": 1000,
    }
    assert "_cache_key" not in llm_step.current_config

    ret_step = next(s for s in schema.steps if s.name == "my_retriever")
    assert ret_step.current_config == {"top_k": 10, "threshold": 0.8}


def test_known_pipeline_metadata_from_live_response():
    """Live response resolved metadata merges onto correct known-pipeline steps."""
    data = {
        "name": "TermNorm", "version": "2.0",
        "nodes": {
            "entity_profiling": {
                "type": "LLMGeneration",
                "config": {
                    "schema_family": "entity_profile", "schema_version": 1,
                    "prompt_family": "entity_profiling", "prompt_version": 1,
                },
            },
            "llm_ranking": {
                "type": "LLMGeneration",
                "config": {
                    "prompt_family": "llm_ranking", "prompt_version": 1,
                },
            },
        },
        "resolved_schemas": {
            "entity_profile/1": {
                "family": "entity_profile", "version": 1,
                "fields": ["entity_name", "core_concept"],
                "json_schema": {"properties": {
                    "entity_name": {"type": "string", "description": "Canonical name"},
                    "core_concept": {"type": "string", "description": "Conceptual essence"},
                }},
            },
        },
        "resolved_prompts": {
            "entity_profiling/1": {
                "family": "entity_profiling", "version": 1,
                "template_variables": ["query", "format_string", "combined_text"],
            },
            "llm_ranking/1": {
                "family": "llm_ranking", "version": 1,
                "template_variables": ["core_concept", "entity_profile_json", "matches"],
            },
        },
    }
    schema = parse_pipeline_response(data)

    # entity_profiling gets both
    ep = next(s for s in schema.steps if s.name == "entity_profiling")
    assert ep.output_schema is not None
    assert ep.output_schema.fields == ["entity_name", "core_concept"]
    assert ep.output_schema.field_descriptions == {
        "entity_name": "Canonical name", "core_concept": "Conceptual essence",
    }
    assert ep.prompt_meta is not None
    assert ep.prompt_meta.template_variables == ["query", "format_string", "combined_text"]

    # llm_ranking gets prompt_meta
    lr = next(s for s in schema.steps if s.name == "llm_ranking")
    assert lr.prompt_meta is not None
    assert "core_concept" in lr.prompt_meta.template_variables

    # Other steps unaffected
    ws = next(s for s in schema.steps if s.name == "web_search")
    assert ws.output_schema is None
    assert ws.prompt_meta is None
