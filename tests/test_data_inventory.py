"""Tests for build_data_inventory()."""

from api.models.prompt_state import PromptState
from api.services.search import build_data_inventory
from api.services.search.plan_persistence import (
    serialize_grid_plan,
    serialize_smart_search_plan,
)

from _helpers import make_baseline_ps, make_dataset_run


def _make_baseline() -> PromptState:
    return make_baseline_ps(persona="default persona", task_intent="default intent")


def _fake_run(run_id: str, rendered_prompt: str, n_queries: int = 3) -> dict:
    return make_dataset_run(
        run_id, accuracy=1.0, n_queries=n_queries,
        content_hash=f"ch_{run_id}", rendered_prompt=rendered_prompt,
    )


def _save_grid_plan(store, backend_id, baseline_ps, variants):
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
    for run in runs:
        store.dataset_runs.save(backend_id, run["run_id"], run)
    from api.services.search import build_prompt_result_index
    return build_prompt_result_index(store, backend_id)


def test_single_axis_change_counted(tmp_store):
    """Grid plan with one variant (persona changed) -> persona.n_prompts=1."""
    bl = _make_baseline()
    variant = bl.derive(persona="expert")
    _save_grid_plan(tmp_store, "b1", bl, [variant])

    index = _build_index(tmp_store, "b1", [
        _fake_run("r1", variant.render(), n_queries=2),
    ])
    inv = build_data_inventory(index, tmp_store, "b1")

    assert inv["total_prompts"] == 1
    assert inv["matched_prompts"] == 1
    assert "persona" in inv["axes"]
    assert inv["axes"]["persona"]["n_prompts"] == 1
    assert inv["axes"]["persona"]["n_queries"] == 2


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


def test_unmatched_prompts_tracked(tmp_store):
    """Extra prompt in index not from any plan -> unmatched_prompts=1."""
    bl = _make_baseline()
    _save_grid_plan(tmp_store, "b1", bl, [])

    unknown_ps = PromptState(instruction="totally different prompt")
    index = _build_index(tmp_store, "b1", [
        _fake_run("r1", bl.render(), n_queries=3),
        _fake_run("r2", unknown_ps.render(), n_queries=2),
    ])
    inv = build_data_inventory(index, tmp_store, "b1")

    assert inv["total_prompts"] == 2
    assert inv["matched_prompts"] == 1
    assert inv["unmatched_prompts"] == 1


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
    plan_data["status"] = "scan_complete"
    plan_data["scan_results"] = {
        "rows": [],
        "axis_profiles": [
            {
                "axis": "persona", "axis_type": "prompt_field",
                "cardinality": 4, "sensitivity_range": 0.12,
                "best_delta": 0.05, "exploration_budget": "medium",
            },
            {
                "axis": "ranking_temperature", "axis_type": "pipeline_param",
                "cardinality": 4, "sensitivity_range": 0.0,
                "best_delta": 0.0, "exploration_budget": "skip",
            },
        ],
    }
    tmp_store.smart_search.save("b1", "ssplan_abc123", plan_data)

    inv = build_data_inventory({}, tmp_store, "b1")

    pp = inv["pipeline_params"]
    assert "ranking_temperature" in pp
    assert pp["ranking_temperature"]["scanned"] is True
    assert pp["ranking_temperature"]["sensitivity_range"] == 0.0
    assert "persona" not in pp
