"""Tests for api/services/grid_search.py."""
import json

import pandas as pd
import pytest

from api.models.prompt_state import PromptState
from api.services.llm_client import MockLLMClient
from api.services.grid_search import (
    DEFAULT_GRID_AXES,
    EXPLORATION_PRESETS,
    GRID_SEARCHABLE_FIELDS,
    validate_grid_config,
    build_grid_combinations,
    restructure_context,
    run_grid_search,
    analyze_grid_results,
    select_grid_winner,
    load_eval_dataset,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def baseline():
    return PromptState(
        instruction=(
            "Rank candidates for {{core_concept}} "
            "given {{entity_profile_json}} and {{matches}}."
        ),
        changes_description="test baseline",
    )


@pytest.fixture
def eval_data():
    """Minimal eval data for grid search tests."""
    return [
        {
            "query": "aspirin",
            "ground_truth": "Acetylsalicylic acid",
            "status": "success",
            "pipeline_data": {
                "entity_profile": {"core_concept": "aspirin"},
                "token_matched_candidates": ["Acetylsalicylic acid", "Ibuprofen"],
            },
        },
    ]


@pytest.fixture
def mock_reranker_response():
    return json.dumps({
        "ranked_candidates": [
            {"candidate": "Acetylsalicylic acid", "core_concept_score": 0.95}
        ]
    })


# ---------------------------------------------------------------------------
# Constants well-formedness
# ---------------------------------------------------------------------------


class TestDefaultGridAxes:
    def test_all_keys_are_searchable(self):
        for key in DEFAULT_GRID_AXES:
            assert key in GRID_SEARCHABLE_FIELDS

    def test_each_axis_has_at_least_two_values(self):
        for key, values in DEFAULT_GRID_AXES.items():
            assert len(values) >= 2

    def test_each_axis_contains_empty_string(self):
        for key, values in DEFAULT_GRID_AXES.items():
            assert "" in values

    def test_values_are_strings(self):
        for key, values in DEFAULT_GRID_AXES.items():
            for v in values:
                assert isinstance(v, str)


# ---------------------------------------------------------------------------
# EXPLORATION_PRESETS
# ---------------------------------------------------------------------------


class TestExplorationPresets:
    def test_all_presets_have_valid_fields(self):
        for name, axes in EXPLORATION_PRESETS.items():
            invalid = set(axes.keys()) - GRID_SEARCHABLE_FIELDS
            assert not invalid, f"Preset '{name}' has invalid fields: {invalid}"

    def test_conservative_is_subset(self):
        conservative_axes = set(EXPLORATION_PRESETS["conservative"].keys())
        balanced_axes = set(EXPLORATION_PRESETS["balanced"].keys())
        assert conservative_axes < balanced_axes, (
            "Conservative should have strictly fewer axes than balanced"
        )

    def test_balanced_matches_defaults(self):
        assert set(EXPLORATION_PRESETS["balanced"].keys()) == set(DEFAULT_GRID_AXES.keys())

    def test_exploration_is_superset(self):
        balanced_axes = set(EXPLORATION_PRESETS["balanced"].keys())
        exploration_axes = set(EXPLORATION_PRESETS["exploration"].keys())
        assert balanced_axes <= exploration_axes

    def test_each_preset_has_empty_variant(self):
        for name, axes in EXPLORATION_PRESETS.items():
            for field, values in axes.items():
                assert "" in values, (
                    f"Preset '{name}', field '{field}' missing empty variant"
                )

    def test_combo_counts(self):
        """Verify expected combo counts for each preset."""
        for name, axes in EXPLORATION_PRESETS.items():
            total = 1
            for values in axes.values():
                total *= len(values)
            if name == "conservative":
                assert total == 4
            elif name == "balanced":
                assert total == 96
            elif name == "exploration":
                assert total == 288


# ---------------------------------------------------------------------------
# validate_grid_config
# ---------------------------------------------------------------------------


class TestValidateGridConfig:
    def test_valid_config(self, baseline):
        config = {"persona": ["", "Expert"], "thinking_style": ["", "Step by step"]}
        meta = validate_grid_config(config, baseline)
        assert meta["total"] == 4
        assert meta["axis_names"] == ["persona", "thinking_style"]
        assert meta["is_subsampled"] is False

    def test_rejects_invalid_field(self, baseline):
        with pytest.raises(ValueError, match="Invalid grid axis fields"):
            validate_grid_config({"bogus_field": ["a", "b"]}, baseline)

    def test_rejects_instruction_missing_template_vars(self, baseline):
        config = {"instruction": ["", "Just rank them, no template vars needed."]}
        with pytest.raises(ValueError, match="missing template variables"):
            validate_grid_config(config, baseline)

    def test_accepts_instruction_with_all_template_vars(self, baseline):
        valid = (
            "Given {{core_concept}} and {{entity_profile_json}}, "
            "rank {{matches}} by relevance."
        )
        meta = validate_grid_config({"instruction": ["", valid]}, baseline)
        assert meta["total"] == 2

    def test_empty_instruction_variant_allowed(self, baseline):
        meta = validate_grid_config({"instruction": [""]}, baseline)
        assert meta["total"] == 1

    def test_default_axes_validate(self, baseline):
        meta = validate_grid_config(dict(DEFAULT_GRID_AXES), baseline)
        expected = 1
        for values in DEFAULT_GRID_AXES.values():
            expected *= len(values)
        assert meta["total"] == expected


# ---------------------------------------------------------------------------
# build_grid_combinations
# ---------------------------------------------------------------------------


class TestBuildGridCombinations:
    def test_correct_count(self, baseline):
        config = {"persona": ["", "A", "B"], "thinking_style": ["", "X"]}
        combos, lookup = build_grid_combinations(config, baseline)
        assert len(combos) == 6
        assert len(lookup) == 6

    def test_subsampling(self, baseline):
        config = {"persona": ["", "A", "B"], "thinking_style": ["", "X"]}
        combos, lookup = build_grid_combinations(config, baseline, max_combinations=3)
        assert len(combos) == 3

    def test_subsampling_is_reproducible(self, baseline):
        config = {"persona": ["", "A", "B"], "thinking_style": ["", "X"]}
        c1, _ = build_grid_combinations(config, baseline, max_combinations=3, seed=42)
        c2, _ = build_grid_combinations(config, baseline, max_combinations=3, seed=42)
        assert [c[0] for c in c1] == [c[0] for c in c2]

    def test_each_combo_is_prompt_state(self, baseline):
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        for coord, ps_id in combos:
            ps = lookup[ps_id]
            assert isinstance(ps, PromptState)
            assert ps.parent_id == baseline.id

    def test_non_empty_values_override(self, baseline):
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        for coord, ps_id in combos:
            if coord["persona"] == 1:
                assert lookup[ps_id].persona == "Expert"
            elif coord["persona"] == 0:
                assert lookup[ps_id].persona == baseline.persona

    def test_changes_description_format(self, baseline):
        config = {"persona": ["", "A"], "task_intent": ["", "B"]}
        combos, lookup = build_grid_combinations(config, baseline)
        for _, ps_id in combos:
            ps = lookup[ps_id]
            assert ps.changes_description.startswith("grid[")
            assert ps.changes_description.endswith("]")

    def test_single_axis_grid(self, baseline):
        config = {"answer_format": ["", "JSON", "Markdown"]}
        combos, lookup = build_grid_combinations(config, baseline)
        assert len(combos) == 3

    def test_default_axes_full_product(self, baseline):
        combos, lookup = build_grid_combinations(dict(DEFAULT_GRID_AXES), baseline)
        expected = 1
        for values in DEFAULT_GRID_AXES.values():
            expected *= len(values)
        assert len(combos) == expected


# ---------------------------------------------------------------------------
# restructure_context
# ---------------------------------------------------------------------------


class TestRestructureContext:
    @pytest.mark.asyncio
    async def test_parse_mode(self):
        mock_resp = json.dumps({
            "persona": "Domain expert",
            "task_intent": "Rank candidates",
            "problem_description": "Term normalization",
            "instruction": "Given candidates, rank them.",
            "thinking_style": "Step by step",
            "answer_format": "JSON",
        })
        client = MockLLMClient(responses=[mock_resp])
        result = await restructure_context("normalize medical terms", client)
        assert result["persona"] == "Domain expert"
        assert all(k in result for k in (
            "persona", "task_intent", "problem_description",
            "instruction", "thinking_style", "answer_format",
        ))

    @pytest.mark.asyncio
    async def test_validate_mode(self):
        mock_resp = json.dumps({
            "persona": "Expert",
            "task_intent": "Rank",
            "problem_description": "",
            "instruction": "Rank them",
            "thinking_style": "",
            "answer_format": "",
        })
        client = MockLLMClient(responses=[mock_resp])
        result = await restructure_context(
            {"persona": "Expert", "task_intent": "Rank"}, client
        )
        assert result["persona"] == "Expert"

    @pytest.mark.asyncio
    async def test_fills_missing_keys(self):
        mock_resp = json.dumps({"persona": "Test"})
        client = MockLLMClient(responses=[mock_resp])
        result = await restructure_context("test", client)
        # Should have all 6 keys even if LLM only returned one
        assert "thinking_style" in result
        assert result["thinking_style"] == ""

    @pytest.mark.asyncio
    async def test_with_improvement_areas(self):
        mock_resp = json.dumps({
            "persona": "Domain expert",
            "task_intent": "Rank candidates",
            "problem_description": "Term normalization",
            "instruction": "Rank them",
            "thinking_style": "Step by step",
            "answer_format": "JSON",
            "consultation": "Focus on improving entity profile schema coverage.",
        })
        client = MockLLMClient(responses=[mock_resp])
        result = await restructure_context(
            "normalize terms", client,
            improvement_areas="profile schema quality, web search relevance",
        )
        assert "consultation" in result
        assert result["consultation"] == "Focus on improving entity profile schema coverage."
        # Layer 1 fields still present
        assert result["persona"] == "Domain expert"

    @pytest.mark.asyncio
    async def test_without_improvement_areas_no_consultation(self):
        mock_resp = json.dumps({
            "persona": "Expert",
            "task_intent": "Rank",
            "problem_description": "",
            "instruction": "",
            "thinking_style": "",
            "answer_format": "",
        })
        client = MockLLMClient(responses=[mock_resp])
        result = await restructure_context("test", client)
        assert "consultation" not in result


# ---------------------------------------------------------------------------
# run_grid_search
# ---------------------------------------------------------------------------


class TestRunGridSearch:
    @pytest.mark.asyncio
    async def test_evaluates_all_combos(self, baseline, eval_data, mock_reranker_response):
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        client = MockLLMClient(responses=[mock_reranker_response])

        df = await run_grid_search(combos, lookup, eval_data, client)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "accuracy" in df.columns
        assert "hits" in df.columns

    @pytest.mark.asyncio
    async def test_on_combo_done_callback(self, baseline, eval_data, mock_reranker_response):
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        client = MockLLMClient(responses=[mock_reranker_response])

        callback_calls = []
        def on_done(idx, row):
            callback_calls.append(idx)

        await run_grid_search(combos, lookup, eval_data, client, on_combo_done=on_done)
        assert len(callback_calls) == 2

    @pytest.mark.asyncio
    async def test_sorted_by_accuracy(self, baseline, eval_data):
        # First combo gets a hit, second gets a miss
        hit_resp = json.dumps({
            "ranked_candidates": [
                {"candidate": "Acetylsalicylic acid", "core_concept_score": 0.9}
            ]
        })
        miss_resp = json.dumps({
            "ranked_candidates": [
                {"candidate": "Wrong", "core_concept_score": 0.1}
            ]
        })
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        client = MockLLMClient(responses=[hit_resp, miss_resp])

        df = await run_grid_search(combos, lookup, eval_data, client)
        assert df.iloc[0]["accuracy"] >= df.iloc[1]["accuracy"]


# ---------------------------------------------------------------------------
# analyze_grid_results
# ---------------------------------------------------------------------------


class TestAnalyzeGridResults:
    @pytest.mark.asyncio
    async def test_returns_analysis(self):
        grid_df = pd.DataFrame([
            {"persona": 0, "accuracy": 0.8, "hits": 4, "total": 5,
             "errors": 0, "prompt_state_id": "a"},
            {"persona": 1, "accuracy": 0.6, "hits": 3, "total": 5,
             "errors": 0, "prompt_state_id": "b"},
        ])
        config = {"persona": ["", "Expert"]}

        mock_resp = json.dumps({
            "key_findings": ["Finding 1"],
            "strongest_fields": ["persona"],
            "recommended_focus": "persona",
            "campaign_advice": "Focus on persona.",
        })
        client = MockLLMClient(responses=[mock_resp])

        result = await analyze_grid_results(grid_df, config, client)
        assert "key_findings" in result
        assert "campaign_advice" in result


# ---------------------------------------------------------------------------
# select_grid_winner
# ---------------------------------------------------------------------------


class TestSelectGridWinner:
    def test_selects_best(self, baseline):
        config = {"persona": ["", "Expert"]}
        _, lookup = build_grid_combinations(config, baseline)
        ps_ids = list(lookup.keys())

        grid_df = pd.DataFrame([
            {"persona": 1, "accuracy": 0.9, "hits": 9, "total": 10,
             "errors": 0, "prompt_state_id": ps_ids[0]},
            {"persona": 0, "accuracy": 0.5, "hits": 5, "total": 10,
             "errors": 0, "prompt_state_id": ps_ids[1]},
        ])

        result = select_grid_winner(grid_df, lookup)
        assert result["accuracy"] == 0.9
        assert result["round"] == "grid"
        assert result["prompt_state"].id == ps_ids[0]


# ---------------------------------------------------------------------------
# load_eval_dataset
# ---------------------------------------------------------------------------


class TestLoadEvalDataset:
    @staticmethod
    def _make_trace(query, bom_material, entity_profile, candidates=None):
        """Build a single Langfuse-style trace dict."""
        observations = [
            {"name": "entity_profiling", "output": entity_profile},
        ]
        if candidates is not None:
            observations.append(
                {"name": "token_matching", "output": {"candidates": candidates}},
            )
        return {
            "id": f"trace-{query}",
            "input": {"query": query},
            "output": {"target": "", "confidence": 0},
            "observations": observations,
        }

    @staticmethod
    def _make_mapping(bom_material, dataset_entry):
        return {"bom_material": bom_material, "dataset_entry": dataset_entry}

    def test_loads_from_run_traces(self, tmp_store):
        """Langfuse-style runs[0].traces[] are parsed into eval data."""
        from api.models.backend import BackendConnection
        tmp_store.register_backend(BackendConnection(
            id="test", name="Test", backend_type="test", base_url="http://test",
        ))

        exp_data = {
            "mappings": [
                self._make_mapping("aspirin", "Acetylsalicylic acid"),
                self._make_mapping("ibuprofen", "Ibuprofen"),
                self._make_mapping("no_trace", "Paracetamol"),  # no matching trace
            ],
            "runs": [{
                "traces": [
                    self._make_trace(
                        "aspirin/process1", "aspirin",
                        {"core_concept": "aspirin analgesic"},
                        [["Acetylsalicylic acid", 0.9], ["Aspirin", 0.4]],
                    ),
                    self._make_trace(
                        "ibuprofen/process2", "ibuprofen",
                        {"core_concept": "ibuprofen nsaid"},
                        [["Ibuprofen", 0.8]],
                    ),
                ],
            }],
        }
        tmp_store.save_sync("test", "experiments/exp1.json", exp_data)

        result = load_eval_dataset(tmp_store, "test", "exp1")
        assert len(result) == 2
        assert result[0]["query"] == "aspirin/process1"
        assert result[0]["ground_truth"] == "Acetylsalicylic acid"
        assert result[0]["pipeline_data"]["entity_profile"]["core_concept"] == "aspirin analgesic"
        assert result[0]["pipeline_data"]["token_matched_candidates"] == [
            ["Acetylsalicylic acid", 0.9], ["Aspirin", 0.4],
        ]
        assert result[1]["query"] == "ibuprofen/process2"
        assert result[1]["ground_truth"] == "Ibuprofen"

    def test_skips_traces_without_entity_profile(self, tmp_store):
        """Traces missing entity_profiling observation are filtered out."""
        from api.models.backend import BackendConnection
        tmp_store.register_backend(BackendConnection(
            id="test", name="Test", backend_type="test", base_url="http://test",
        ))

        exp_data = {
            "mappings": [self._make_mapping("q1", "gt1")],
            "runs": [{
                "traces": [
                    {
                        "id": "trace-no-ep",
                        "input": {"query": "q1"},
                        "output": {},
                        "observations": [
                            {"name": "token_matching", "output": {"candidates": []}},
                        ],
                    },
                ],
            }],
        }
        tmp_store.save_sync("test", "experiments/exp1.json", exp_data)

        result = load_eval_dataset(tmp_store, "test", "exp1")
        assert len(result) == 0

    def test_skips_traces_without_ground_truth(self, tmp_store):
        """Traces whose bom_material has no mapping are skipped."""
        from api.models.backend import BackendConnection
        tmp_store.register_backend(BackendConnection(
            id="test", name="Test", backend_type="test", base_url="http://test",
        ))

        exp_data = {
            "mappings": [self._make_mapping("other_bom", "SomeGT")],
            "runs": [{
                "traces": [
                    self._make_trace(
                        "unknown_bom/process", "unknown_bom",
                        {"core_concept": "unknown"},
                    ),
                ],
            }],
        }
        tmp_store.save_sync("test", "experiments/exp1.json", exp_data)

        result = load_eval_dataset(tmp_store, "test", "exp1")
        assert len(result) == 0

    def test_query_limit(self, tmp_store):
        from api.models.backend import BackendConnection
        tmp_store.register_backend(BackendConnection(
            id="test", name="Test", backend_type="test", base_url="http://test",
        ))

        mappings = [
            self._make_mapping(f"bom{i}", f"gt{i}") for i in range(10)
        ]
        traces = [
            self._make_trace(
                f"bom{i}/proc", f"bom{i}",
                {"core_concept": f"c{i}"},
                [[f"gt{i}", 0.5]],
            )
            for i in range(10)
        ]
        tmp_store.save_sync("test", "experiments/exp1.json", {
            "mappings": mappings,
            "runs": [{"traces": traces}],
        })

        result = load_eval_dataset(tmp_store, "test", "exp1", query_limit=3)
        assert len(result) == 3

