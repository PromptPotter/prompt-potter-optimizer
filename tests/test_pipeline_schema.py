"""Tests for PipelineSchema model and pipeline discovery."""

import pytest

from api.models.pipeline_schema import ObservationMapping, PipelineSchema, PipelineStep
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
        from api.services.obs.langfuse_push import DATASET_NAME

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
