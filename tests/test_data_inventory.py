"""Tests for build_data_inventory()."""

from _helpers import make_baseline_osp, make_dataset_run

from api.models.opt_search_point import OptSearchPoint
from api.services.search import build_data_inventory
from api.services.search.plan_persistence import serialize_smart_search_plan


def _make_baseline() -> OptSearchPoint:
    return make_baseline_osp(persona="default persona", task_intent="default intent")


def _fake_run(run_id: str, rendered_prompt: str, n_queries: int = 3) -> dict:
    return make_dataset_run(
        run_id, accuracy=1.0, n_queries=n_queries,
        content_hash=f"ch_{run_id}", rendered_prompt=rendered_prompt,
    )


def _build_index(store, backend_id, runs):
    for run in runs:
        store.dataset_runs.save(backend_id, run["run_id"], run)
    from api.services.search import build_prompt_result_index
    return build_prompt_result_index(store, backend_id)


def test_pipeline_params_from_smart_search(tmp_store):

    bl = _make_baseline()
    plan_data = serialize_smart_search_plan(
        plan_id="ssplan_abc123",
        config={"n_diagnostic": 6, "max_rounds": 3},
        baseline_opt=bl,
        search_baseline_opt=bl,
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
