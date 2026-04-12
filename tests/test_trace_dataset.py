"""Smoke test for potter-trace dataset loader."""

from promptpotter.application.datasets.trace_dataset import (
    _infer_escalation_layer,
    load_potter_traces,
)
from promptpotter.infrastructure.store.project_store import ProjectStore


def _make_trial(
    *,
    round_num: int,
    accuracy: float,
    changes_description: str,
    plan: str = "",
    optimizer_params: dict | None = None,
    l2_directive: str = "",
) -> dict:
    return {
        "trial_id": f"t{round_num}",
        "round": round_num,
        "label": changes_description,
        "prompt_fields_id": f"pf{round_num}",
        "accuracy": accuracy,
        "hits": int(accuracy * 10),
        "total": 10,
        "improved": False,
        "created_at": "2026-04-12T00:00:00+00:00",
        "prompt_fields": {
            "persona": "p",
            "task_intent": "ti",
            "problem_description": "pd",
            "instruction": "i",
            "thinking_style": "ts",
            "answer_format": "af",
            "plan": plan,
            "optimizer_params": optimizer_params or {},
            "l2_directive": l2_directive,
            "critique_text": "c",
            "changes_description": changes_description,
        },
        "results": [],
    }


class TestEscalationInference:
    def test_l3_on_plan_change(self):
        prev = {"plan": "A"}
        nxt = {"plan": "B"}
        assert _infer_escalation_layer(prev, nxt) == "L3"

    def test_l2_on_optimizer_params_change(self):
        prev = {"plan": "A", "optimizer_params": {"creativity": 0.5}}
        nxt = {"plan": "A", "optimizer_params": {"creativity": 0.9}}
        assert _infer_escalation_layer(prev, nxt) == "L2"

    def test_l2_on_fresh_directive(self):
        prev = {"plan": "A", "l2_directive": ""}
        nxt = {"plan": "A", "l2_directive": "Focus on web_search."}
        assert _infer_escalation_layer(prev, nxt) == "L2"

    def test_l1_default(self):
        prev = {"plan": "A"}
        nxt = {"plan": "A"}
        assert _infer_escalation_layer(prev, nxt) == "L1"


class TestLoadPotterTraces:
    def test_two_trial_transition(self, tmp_path):
        store = ProjectStore(tmp_path)
        backend_id = "bt"
        cycle_id = "cyc1"

        store.campaigns.create(backend_id, cycle_id, {"type": "optimization_loop"})
        store.campaigns.add_trial(
            backend_id,
            cycle_id,
            _make_trial(round_num=0, accuracy=0.3, changes_description="baseline"),
        )
        store.campaigns.add_trial(
            backend_id,
            cycle_id,
            _make_trial(
                round_num=1,
                accuracy=0.5,
                changes_description="bump creativity",
                optimizer_params={"creativity": 0.9},
            ),
        )

        rows = load_potter_traces(store, backend_id)
        assert len(rows) == 1
        row = rows[0]
        assert row["query"] == "cyc1:round_0"
        assert row["ground_truth"] == "bump creativity"
        assert row["score_delta"] == 0.2
        assert row["escalation_layer"] == "L2"
        assert "opt_search_point" in row["round_context"]
        assert row["prev_prompt"]
        assert row["next_prompt"]

    def test_empty_when_single_trial(self, tmp_path):
        store = ProjectStore(tmp_path)
        store.campaigns.create("bt", "cyc1", {"type": "optimization_loop"})
        store.campaigns.add_trial(
            "bt",
            "cyc1",
            _make_trial(round_num=0, accuracy=0.3, changes_description="baseline"),
        )
        assert load_potter_traces(store, "bt") == []
