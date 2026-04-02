"""Tests for PipelineSchema model and pipeline discovery.

Verifies derivation methods, parse_pipeline_response factory,
and registry metadata flow.
"""

from api.models.pipeline_schema import (
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from api.services.pipeline_discovery import parse_pipeline_response


def test_derivation_methods():
    schema = PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(
                name="search",
                runtime="backend",
                param_keys={"max_results"},
                observation_name="search",
                observation_mappings=[
                    ObservationMapping(pipeline_key="results", output_field="items"),
                ],
                langfuse_type="tool",
            ),
            PipelineNode(
                name="rank",
                runtime="backend",
                param_keys={"temperature"},
                observation_name="rank",
                observation_mappings=[
                    ObservationMapping(pipeline_key="ranked", is_llm=True),
                ],
                langfuse_type="generation",
            ),
            PipelineNode(
                name="cache",
                runtime="frontend",
                langfuse_type="span",
            ),
        ],
    )

    # node_param_keys
    assert schema.node_param_keys() == {
        "search": {"max_results"},
        "rank": {"temperature"},
    }
    assert "cache" not in schema.node_param_keys()

    # obs_extraction_map
    obs_map = schema.obs_extraction_map()
    assert set(obs_map.keys()) == {"search", "rank"}
    assert obs_map["search"][0].pipeline_key == "results"
    assert obs_map["rank"][0].is_llm is True

    # langfuse_type_map
    assert schema.langfuse_type_map() == {
        "search": "tool", "rank": "generation", "cache": "span",
    }



class TestParsePipelineResponse:
    def test_pipeline_with_optimizer_metadata(self):
        """Test parsing a self-describing pipeline with optimizer metadata."""
        data = {
            "config": {
                "name": "TermNorm",
                "version": "2.0",
                "description": "Test pipeline",
                "required_step": "entity_profile",
                "template_variables": ["{{core_concept}}", "{{matches}}"],
                "dataset_name": "termnorm_ground_truth",
                "available_models": ["model-a"],
                "nodes": {
                    "cache_lookup": {
                        "type": "cache",
                        "runtime": "frontend",
                        "short_circuit": True,
                        "node_role": "cache",
                        "description": "Cache step",
                        "config": {},
                        "optimizer": {"langfuse_type": "span"},
                    },
                    "web_search": {
                        "type": "tool",
                        "runtime": "backend",
                        "node_role": "enricher",
                        "description": "Web search step",
                        "config": {
                            "max_sites": 7,
                            "num_results": 20,
                            "query_prefix": "",
                        },
                        "optimizer": {
                            "param_keys": ["max_sites", "num_results"],
                            "param_descriptions": {
                                "max_sites": "Max pages to fetch",
                                "num_results": "Search results count",
                            },
                            "observation_name": "web_search",
                            "observation_mappings": [
                                {"pipeline_key": "web_sources", "output_field": "sources"},
                            ],
                            "langfuse_type": "tool",
                        },
                    },
                    "llm_ranking": {
                        "type": "generation",
                        "runtime": "backend",
                        "node_role": "ranker",
                        "description": "LLM ranking step",
                        "config": {
                            "temperature": 0.0,
                            "model": "model-a",
                        },
                        "optimizer": {
                            "param_keys": ["ranking_temperature"],
                            "langfuse_type": "generation",
                        },
                    },
                },
                "pipelines": {
                    "default": ["cache_lookup", "web_search", "llm_ranking"],
                },
            },
        }
        schema = parse_pipeline_response(data)

        assert schema.name == "termnorm"
        assert schema.version == "2.0"
        assert len(schema.nodes) == 3

        # Step order matches pipelines.default
        assert [s.name for s in schema.nodes] == ["cache_lookup", "web_search", "llm_ranking"]

        # Cache step
        cache = schema.nodes[0]
        assert cache.runtime == "frontend"
        assert cache.short_circuit is True
        assert cache.node_type == "cache"
        assert cache.langfuse_type == "span"

        # Web search step — full optimizer metadata
        ws = schema.nodes[1]
        assert ws.runtime == "backend"
        assert ws.node_type == "enricher"
        assert ws.param_keys == {"max_sites", "num_results"}
        assert ws.observation_name == "web_search"
        assert len(ws.observation_mappings) == 1
        assert ws.observation_mappings[0].pipeline_key == "web_sources"
        assert ws.langfuse_type == "tool"

        # LLM ranking step
        lr = schema.nodes[2]
        assert lr.node_type == "ranker"
        assert lr.param_keys == {"ranking_temperature"}
        assert lr.param_keys == {"ranking_temperature"}

    def test_unknown_pipeline_no_optimizer(self):
        """Unknown pipelines without optimizer metadata still work."""
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
        assert len(schema.nodes) == 2
        # Without optimizer, param_keys is empty
        step_a = next(s for s in schema.nodes if s.name == "step_a")
        assert step_a.param_keys == set()

    def test_resolved_metadata_merged(self):
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
        assert len(schema.nodes) == 2

        step_a = schema.nodes[0]
        assert step_a.output_schema is not None
        assert step_a.output_schema.family == "my_schema"
        assert step_a.output_schema.fields == ["field1", "field2"]
        assert step_a.output_schema.field_descriptions == {"field1": "First field"}
        assert step_a.prompt_meta is not None
        assert step_a.prompt_meta.template_variables == ["input", "context"]

        assert schema.nodes[1].output_schema is None

    def test_data_envelope_unwrap(self):
        data = {
            "status": "success",
            "data": {
                "name": "TestPipeline",
                "version": "2.0",
                "nodes": {
                    "entity_profiling": {
                        "type": "LLMGeneration",
                        "runtime": "backend",
                        "config": {
                            "schema_family": "entity_profile", "schema_version": 1,
                            "temperature": 0.3,
                        },
                        "optimizer": {
                            "param_keys": ["profiling_temperature"],
                            "langfuse_type": "generation",
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

        assert schema.name == "testpipeline"
        assert len(schema.nodes) == 1

        ep = schema.nodes[0]
        assert ep.output_schema is not None
        assert ep.output_schema.fields == ["entity_name", "core_concept"]

    def test_step_order_from_pipelines_default(self):
        data = {
            "name": "Test",
            "nodes": {
                "step_c": {"type": "tool", "config": {}},
                "step_a": {"type": "tool", "config": {}},
                "step_b": {"type": "tool", "config": {}},
            },
            "pipelines": {"default": ["step_a", "step_b", "step_c"]},
        }
        schema = parse_pipeline_response(data)
        assert [s.name for s in schema.nodes] == ["step_a", "step_b", "step_c"]

    def test_observation_mappings_with_is_llm(self):
        data = {
            "name": "Test",
            "nodes": {
                "llm_step": {
                    "type": "generation",
                    "config": {},
                    "optimizer": {
                        "observation_name": "llm_step",
                        "observation_mappings": [
                            {"pipeline_key": "output", "output_field": "result", "is_llm": True},
                        ],
                        "langfuse_type": "generation",
                    },
                },
            },
        }
        schema = parse_pipeline_response(data)
        step = schema.nodes[0]
        assert step.observation_name == "llm_step"
        assert len(step.observation_mappings) == 1
        assert step.observation_mappings[0].is_llm is True
