"""Tests for build_data_inventory()."""
import hashlib

from api.models.prompt_state import PromptState
from api.services.search import (
    build_data_inventory,
    serialize_grid_plan,
    serialize_smart_search_plan,
)
from api.services.prompt_eval import HASH_TRUNCATE


def _rp_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:HASH_TRUNCATE]


def _make_baseline() -> PromptState:
    return PromptState(
        instruction="Rank the candidates.",
        persona="default persona",
        task_intent="default intent",
        thinking_style="",
        answer_format="",
    )


def _fake_run(run_id: str, rendered_prompt: str, n_queries: int = 3) -> dict:
    """Build a minimal dataset run dict."""
    items = [
        {"query": f"q{i}", "hit": True, "predicted": "p", "ground_truth": "p"}
        for i in range(n_queries)
    ]
    return {
        "run_id": run_id,
        "name": run_id,
        "content_hash": f"ch_{run_id}",
        "prompt_state_id": f"ps_{run_id}",
        "rendered_prompt_hash": _rp_hash(rendered_prompt),
        "model": "test",
        "temperature": 0,
        "item_count": n_queries,
        "scores": {"hits": n_queries, "total": n_queries, "accuracy": 1.0, "errors": 0},
        "created_at": "2026-01-01T00:00:00Z",
        "dataset_run_items": items,
    }


def _save_grid_plan(store, backend_id, baseline_ps, variants):
    """Save a grid plan with the given baseline and variant PromptStates."""
    state_lookup = {ps.id: ps for ps in [baseline_ps, *variants]}
    plan_data = serialize_grid_plan(
        plan_id="gridplan_test1",
        grid_axes={"persona": ["default persona", "expert"]},
        baseline_ps=baseline_ps,
        layer1_fields={},
        grid_points=[],
        state_lookup=state_lookup,
        sampling_meta={"total_space": 1},
    )
    store.grid_plans.save(backend_id, "gridplan_test1", plan_data)


def _build_index(store, backend_id, runs):
    """Save runs then build index via the real function."""
    for run in runs:
        store.dataset_runs.save(backend_id, run["run_id"], run)
    from api.services.search import build_prompt_result_index
    return build_prompt_result_index(store, backend_id)


def test_empty_index_empty_inventory(tmp_store):
    """No data -> all zeros."""
    inv = build_data_inventory({}, tmp_store, "b1")
    assert inv["total_prompts"] == 0
    assert inv["total_results"] == 0
    assert inv["matched_prompts"] == 0
    assert inv["baseline_prompts"] == 0
    assert inv["unmatched_prompts"] == 0
    assert inv["axes"] == {}


def test_single_axis_change_counted(tmp_store):
    """Grid plan with one variant (persona changed) -> persona.n_prompts=1."""
    bl = _make_baseline()
    variant = bl.derive(persona="expert")
    _save_grid_plan(tmp_store, "b1", bl, [variant])

    # Put variant in the index
    index = _build_index(tmp_store, "b1", [
        _fake_run("r1", variant.render(), n_queries=2),
    ])
    inv = build_data_inventory(index, tmp_store, "b1")

    assert inv["total_prompts"] == 1
    assert inv["matched_prompts"] == 1
    assert "persona" in inv["axes"]
    assert inv["axes"]["persona"]["n_prompts"] == 1
    assert inv["axes"]["persona"]["n_queries"] == 2
    assert inv["axes"]["persona"]["distinct_values"] == 1


def test_multi_axis_change_counted_per_axis(tmp_store):
    """Variant with persona+task_intent changed -> both axes get +1."""
    bl = _make_baseline()
    variant = bl.derive(persona="expert", task_intent="new intent")
    _save_grid_plan(tmp_store, "b1", bl, [variant])

    index = _build_index(tmp_store, "b1", [
        _fake_run("r1", variant.render(), n_queries=4),
    ])
    inv = build_data_inventory(index, tmp_store, "b1")

    assert inv["matched_prompts"] == 1
    assert "persona" in inv["axes"]
    assert "task_intent" in inv["axes"]
    assert inv["axes"]["persona"]["n_prompts"] == 1
    assert inv["axes"]["persona"]["n_queries"] == 4
    assert inv["axes"]["task_intent"]["n_prompts"] == 1
    assert inv["axes"]["task_intent"]["n_queries"] == 4


def test_baseline_itself_counted(tmp_store):
    """Plan baseline present in the index -> baseline_queries > 0."""
    bl = _make_baseline()
    _save_grid_plan(tmp_store, "b1", bl, [])

    index = _build_index(tmp_store, "b1", [
        _fake_run("r1", bl.render(), n_queries=5),
    ])
    inv = build_data_inventory(index, tmp_store, "b1")

    assert inv["baseline_prompts"] == 1
    assert inv["baseline_queries"] == 5
    assert inv["matched_prompts"] == 1
    assert inv["matched_results"] == 5


def test_unmatched_prompts_tracked(tmp_store):
    """Extra prompt in index not from any plan -> unmatched_prompts=1."""
    bl = _make_baseline()
    _save_grid_plan(tmp_store, "b1", bl, [])

    # One run matches the baseline, one is unknown
    unknown_ps = PromptState(instruction="totally different prompt")
    index = _build_index(tmp_store, "b1", [
        _fake_run("r1", bl.render(), n_queries=3),
        _fake_run("r2", unknown_ps.render(), n_queries=2),
    ])
    inv = build_data_inventory(index, tmp_store, "b1")

    assert inv["total_prompts"] == 2
    assert inv["matched_prompts"] == 1
    assert inv["unmatched_prompts"] == 1
    assert inv["unmatched_results"] == 2


def test_pipeline_params_from_smart_search(tmp_store):
    """Smart search plan with pipeline_param axis_profiles -> pipeline_params."""
    bl = _make_baseline()
    plan_data = serialize_smart_search_plan(
        plan_id="ssplan_abc123",
        config={"n_diagnostic": 6, "max_rounds": 3},
        baseline_ps=bl,
        search_baseline_ps=bl,
        layer1_fields={},
        diagnostic=[],
        diag_summary={},
        variant_library_hash="vl_hash",
    )
    # Add scan results with pipeline_param axis profiles
    plan_data["status"] = "scan_complete"
    plan_data["scan_results"] = {
        "rows": [],
        "axis_profiles": [
            {
                "axis": "persona",
                "axis_type": "prompt_field",
                "cardinality": 4,
                "sensitivity_range": 0.12,
                "best_delta": 0.05,
                "exploration_budget": "medium",
            },
            {
                "axis": "ranking_temperature",
                "axis_type": "pipeline_param",
                "cardinality": 4,
                "sensitivity_range": 0.0,
                "best_delta": 0.0,
                "exploration_budget": "skip",
            },
            {
                "axis": "max_token_candidates",
                "axis_type": "pipeline_param",
                "cardinality": 3,
                "sensitivity_range": 0.02,
                "best_delta": 0.01,
                "exploration_budget": "skip",
            },
        ],
    }
    tmp_store.smart_search.save("b1", "ssplan_abc123", plan_data)

    inv = build_data_inventory({}, tmp_store, "b1")

    pp = inv["pipeline_params"]
    assert "ranking_temperature" in pp
    assert pp["ranking_temperature"]["scanned"] is True
    assert pp["ranking_temperature"]["cardinality"] == 4
    assert pp["ranking_temperature"]["sensitivity_range"] == 0.0
    assert pp["ranking_temperature"]["exploration_budget"] == "skip"

    assert "max_token_candidates" in pp
    assert pp["max_token_candidates"]["cardinality"] == 3
    assert pp["max_token_candidates"]["sensitivity_range"] == 0.02

    # prompt_field profiles should NOT appear in pipeline_params
    assert "persona" not in pp
