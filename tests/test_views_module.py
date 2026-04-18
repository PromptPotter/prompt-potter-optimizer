"""Unit coverage for ``promptpotter.presentation.views`` shared renderers.

Minimal substring assertions — keeps ``tests/CLAUDE.md`` volume discipline.
Both the CLI ``show-*`` commands and the notebook ``show_*`` wrappers pass
through these functions, so one test per renderer covers both surfaces.
"""

from __future__ import annotations

from promptpotter.presentation.views import (
    render_campaign_summary,
    render_dashboard,
    render_flip_tracking,
    render_lineage,
    render_progress,
    render_status,
)


def _rounds() -> list[dict]:
    """Two-round trial-file shape (disk shape — ``opt_search_point`` dict)."""
    return [
        {
            "round": 0,
            "label": "baseline",
            "accuracy": 0.40,
            "hits": 8,
            "total": 20,
            "opt_search_point": {
                "id": "abc123def456xxx",
                "parent_id": None,
                "changes_description": "",
            },
            "results": [
                {"query": "q1", "hit": True, "predicted": "A", "ground_truth": "A"},
                {"query": "q2", "hit": False, "predicted": "B", "ground_truth": "C"},
                {"query": "q3", "hit": True, "predicted": "D", "ground_truth": "D"},
            ],
        },
        {
            "round": 1,
            "label": "L1 variant",
            "accuracy": 0.55,
            "hits": 11,
            "total": 20,
            "opt_search_point": {
                "id": "ffffeeeeddddcc",
                "parent_id": "abc123def456xxx",
                "changes_description": "sharpen instruction",
            },
            "results": [
                {"query": "q1", "hit": True, "predicted": "A", "ground_truth": "A"},
                {"query": "q2", "hit": True, "predicted": "C", "ground_truth": "C"},
                {"query": "q3", "hit": False, "predicted": "X", "ground_truth": "D"},
            ],
        },
    ]


def test_render_campaign_summary_contains_rounds_and_prompt_id() -> None:
    out = render_campaign_summary(_rounds())
    assert "CAMPAIGN SUMMARY (2 rounds)" in out
    assert "40.0%" in out and "55.0%" in out
    assert "baseline" in out and "L1 variant" in out
    assert "abc123def456" in out  # prompt id truncated to 12


def test_render_campaign_summary_empty() -> None:
    assert render_campaign_summary([]) == "No campaign rounds."


def test_render_flip_tracking_counts_flips() -> None:
    out = render_flip_tracking(_rounds())
    assert "Gained (MISS->HIT): 1" in out
    assert "Lost   (HIT->MISS): 1" in out
    assert "Net change:         +0" in out
    assert "MISS->HIT" in out and "HIT->MISS" in out


def test_render_flip_tracking_needs_two_rounds() -> None:
    assert render_flip_tracking([_rounds()[0]]) == ""


def test_render_lineage_shows_parent_and_changes() -> None:
    out = render_lineage(_rounds())
    assert "LINEAGE CHAIN" in out
    assert "abc123def456" in out
    assert "ffffeeeedddd" in out
    assert "parent: abc123def456" in out
    assert "changes: sharpen instruction" in out


def test_render_progress_shows_rolling_and_trend() -> None:
    out = render_progress(_rounds())
    assert "PROGRESS" in out
    assert "40.0%" in out and "55.0%" in out
    assert "+15.0%" in out  # trend delta between rounds


def test_render_progress_plateau_marker() -> None:
    plateau_rounds = [
        {"round": i, "label": f"r{i}", "accuracy": 0.50, "hits": 5, "total": 10} for i in range(3)
    ]
    out = render_progress(plateau_rounds)
    assert "Plateau" in out


def test_render_dashboard_groups_tiers() -> None:
    d = {
        "phase": "l1_score",
        "workflow": "optimize",
        "round": 2,
        "max_rounds": 10,
        "layer": "L1",
        "baseline": 0.40,
        "best": 0.55,
        "best_round": 1,
        "current_acc": 0.52,
        "cycle_id": "xyz",
        "elapsed_s": 123.4,
        "active_nodes": ["llm_only"],
        "excluded_nodes": [],
        "cache_hit_rate": 0.75,
        "hit_rate": 0.52,
        "degraded_count": 0,
        "error_count": 0,
        "improvement_streak": 1,
        "rounds_completed": 2,
        "model": "openai/gpt-oss-120b",
        "n_variants": 4,
        "sp_budget_ttest": 40,
    }
    out = render_dashboard(d)
    assert "DASHBOARD" in out
    assert "Execution" in out and "Timing" in out and "Pipeline" in out
    assert "Quality" in out and "Config" in out
    assert "40.0%" in out and "55.0%" in out
    assert "llm_only" in out
    assert "openai/gpt-oss-120b" in out


def test_render_dashboard_empty() -> None:
    assert render_dashboard({}) == "No dashboard data."


def test_render_status_includes_control_and_result() -> None:
    dashboard = {"phase": "done", "baseline": 0.4, "best": 0.6}
    control = {"requested_state": "pause", "pause_before_scoring": True}
    result = {"best_accuracy": 0.6, "best_round": 3, "n_rounds": 4, "stop_reason": "converged"}
    out = render_status(dashboard, control, result)
    assert "CONTROL" in out and "pause" in out
    assert "LAST RESULT" in out and "converged" in out
