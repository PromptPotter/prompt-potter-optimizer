"""Tests for PipelineSchema model and pipeline discovery.

Verifies derivation methods, parse_pipeline_response factory,
and registry metadata flow.
"""

from promptpotter.models.pipeline_schema import (
    NodePromptMeta,
    ObservationMapping,
    PipelineNode,
    PipelineSchema,
)
from promptpotter.services.pipeline_discovery import parse_pipeline_response


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
        "search": "tool",
        "rank": "generation",
        "cache": "span",
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
                "dataset_name": "ground_truth",
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
                        "schema_family": "my_schema",
                        "schema_version": 1,
                        "prompt_family": "my_prompt",
                        "prompt_version": 2,
                    },
                },
                "step_b": {"type": "DeterministicFunction", "config": {"threshold": 0.8}},
            },
            "resolved_schemas": {
                "my_schema/1": {
                    "family": "my_schema",
                    "version": 1,
                    "fields": ["field1", "field2"],
                    "json_schema": {
                        "properties": {
                            "field1": {"type": "string", "description": "First field"},
                            "field2": {"type": "number"},
                        }
                    },
                },
            },
            "resolved_prompts": {
                "my_prompt/2": {
                    "family": "my_prompt",
                    "version": 2,
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
                            "schema_family": "entity_profile",
                            "schema_version": 1,
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
                        "family": "entity_profile",
                        "version": 1,
                        "fields": ["entity_name", "core_concept"],
                        "json_schema": {
                            "properties": {
                                "entity_name": {"type": "string", "description": "Canonical name"},
                                "core_concept": {
                                    "type": "string",
                                    "description": "Conceptual essence",
                                },
                            }
                        },
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


def _three_node_schema() -> PipelineSchema:
    """Helper: a → b → c pipeline with param_keys on a and b."""
    return PipelineSchema(
        name="test",
        nodes=[
            PipelineNode(name="a", param_keys={"max_results"}),
            PipelineNode(
                name="b",
                param_keys={"temperature"},
                prompt_meta=NodePromptMeta(family="p"),
            ),
            PipelineNode(name="c"),
        ],
    )


class TestCoordinateLookups:
    def test_has_node(self):
        schema = _three_node_schema()
        assert schema.has_node("a")
        assert schema.has_node("b")
        assert not schema.has_node("z")

    def test_all_param_keys(self):
        schema = _three_node_schema()
        assert schema.all_param_keys == {"max_results", "temperature"}

    def test_has_prompt_nodes(self):
        schema = _three_node_schema()
        assert schema.has_prompt_nodes is True
        no_prompt = PipelineSchema(nodes=[PipelineNode(name="x")])
        assert no_prompt.has_prompt_nodes is False

    def test_exclude(self):
        schema = _three_node_schema()
        reduced = schema.exclude({"b"})
        assert [n.name for n in reduced.nodes] == ["a", "c"]
        # None / empty → identity
        assert schema.exclude(None) is schema
        assert schema.exclude(set()) is schema

    def test_prefix_keys_stable_and_chained(self):
        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}, "b": {"temperature": 0.7}}
        keys = schema.prefix_keys(pp)
        assert len(keys) == 3
        assert all(len(k) == 16 for _, k in keys)
        names = [name for name, _ in keys]
        assert names == ["a", "b", "c"]
        # Changing upstream config changes downstream keys
        pp2 = {"a": {"max_results": 10}, "b": {"temperature": 0.7}}
        keys2 = schema.prefix_keys(pp2)
        assert keys2[0][1] != keys[0][1]  # a changed
        assert keys2[1][1] != keys[1][1]  # b cascaded
        assert keys2[2][1] != keys[2][1]  # c cascaded

    def test_prefix_keys_matches_node_cache_key(self):
        """prefix_keys produces the same hashes as node_cache_key."""
        from promptpotter.models.pipeline_schema import node_cache_key

        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}}
        keys = schema.prefix_keys(pp)
        # First node: upstream=""
        assert keys[0][1] == node_cache_key("a", {"max_results": 5}, "")
        # Second node: upstream=first key
        assert keys[1][1] == node_cache_key("b", {}, keys[0][1])

    def test_sp_hash_is_terminal_of_prefix_keys(self):
        """sp_hash returns the terminal element of prefix_keys."""
        schema = _three_node_schema()
        pp = {"a": {"max_results": 5}, "b": {"temperature": 0.7}}
        chain = schema.prefix_keys(pp)
        assert schema.sp_hash(pp) == chain[-1][1]

    def test_sp_hash_empty_schema(self):
        schema = PipelineSchema(nodes=[])
        assert schema.sp_hash({}) == ""

    def test_sp_hash_changes_with_config(self):
        schema = _three_node_schema()
        h1 = schema.sp_hash({"a": {"max_results": 5}})
        h2 = schema.sp_hash({"a": {"max_results": 10}})
        assert h1 != h2

    def test_excluded_nodes(self):
        schema = _three_node_schema()
        pp = {"steps": ["a", "c"]}
        assert schema.excluded_nodes(pp) == {"b"}

    def test_excluded_nodes_no_steps(self):
        """No 'steps' key → all nodes assumed active → empty set."""
        schema = _three_node_schema()
        assert schema.excluded_nodes({"a": {"max_results": 5}}) == set()

    def test_excluded_nodes_all_active(self):
        schema = _three_node_schema()
        pp = {"steps": ["a", "b", "c"]}
        assert schema.excluded_nodes(pp) == set()
