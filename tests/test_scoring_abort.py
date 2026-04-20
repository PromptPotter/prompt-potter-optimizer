"""Regression tests for the two scoring-loop bugs landed alongside this file.

Bug 1 — cache-replay bypasses elimination: when ``prior_results`` cover the
first N queries of a candidate, the Wilcoxon early-stop check must fire
against cached rows too. Previously the check ran only after a real
``measure_sample()`` call, so a dominated candidate ran one extra real
query before stopping.

Bug 2 — consecutive backend errors must attach as a per-candidate
``RuntimeFailure`` (Rail 2), not raise ``KeyboardInterrupt`` and kill the
round. The query loop marks the rest of the dataset as errors,
``score_search_point`` synthesizes a ``scoring_error_abort`` escalation
signal, and ``_handle_scored_candidate`` attaches a RuntimeFailure so L2
can teach L1 away from the offending config next round.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from promptpotter.application.optimization.nodes.score import _handle_scored_candidate
from promptpotter.application.scoring import sample_measurement, search_point_scorer
from promptpotter.application.scoring.search_point_scorer import (
    _run_query_loop,
    score_search_point,
)
from promptpotter.domain.analysis import (
    EscalationSignal,
    EscalationTarget,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import NodePromptMeta, PipelineNode, PipelineSchema
from promptpotter.domain.scoring import QueryResult, ScoringEnv
from promptpotter.domain.search_point import JobSearchPoint
from promptpotter.shared.errors import ErrorCategory
from promptpotter.shared.scoring import compile_scorer

_TRIVIAL_SCORER = compile_scorer("float(hit)")


def _llm_only_schema() -> PipelineSchema:
    return PipelineSchema(
        name="bbeh",
        nodes=[PipelineNode(name="llm_only", prompt_meta=NodePromptMeta())],
    )


def _cached_row(query: str, score: float) -> QueryResult:
    return cast(
        QueryResult,
        {
            "query": query,
            "ground_truth": "gt",
            "predicted": "x",
            "hit": bool(score),
            "score": score,
            "error": None,
            "pipeline_data": {"final_ranking": [{"candidate": "x"}], "diagnostics": {}},
        },
    )


class _StubCheck:
    """Fires at n_min=4 the first time it's consulted, against cached rows."""

    enabled = True
    name = "stub_elimination"

    def __init__(self, n_min: int = 4) -> None:
        self.n_min = n_min
        self.call_history: list[int] = []

    def evaluate(
        self,
        results_so_far: list[dict],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        self.call_history.append(len(results_so_far))
        if len(results_so_far) < self.n_min:
            return None
        return EscalationSignal(
            check_name="elimination",
            target=EscalationTarget.ELIMINATE_CANDIDATE,
            check_result={"queries_evaluated": len(results_so_far)},
            candidate_idx=candidate_idx,
            candidates_scored=candidate_idx + 1,
            candidates_skipped=n_total_candidates - candidate_idx - 1,
        )


# ---------------------------------------------------------------------------
# Bug 1 — elimination must fire against cached rows, before any real call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_replay_triggers_elimination_without_extra_measure(monkeypatch):
    """After 4 cached rows, elimination stops the loop before a 5th real call.

    Pre-fix behavior ran ``measure_sample`` once past the threshold because
    the check was only consulted on the miss path.
    """
    measure_calls: list[str] = []

    async def fake_measure_sample(query_data, env, pipeline_params=None):
        measure_calls.append(query_data["query"])
        return cast(
            QueryResult,
            {
                "query": query_data["query"],
                "ground_truth": query_data["ground_truth"],
                "predicted": "x",
                "hit": False,
                "score": 0.0,
                "error": None,
                "pipeline_data": {"final_ranking": [{"candidate": "x"}], "diagnostics": {}},
            },
        )

    monkeypatch.setattr(search_point_scorer, "measure_sample", fake_measure_sample)

    dataset = [{"query": f"q{i}", "ground_truth": "gt"} for i in range(20)]
    prior: dict[str, QueryResult] = {f"q{i}": _cached_row(f"q{i}", 0.0) for i in range(4)}

    env = ScoringEnv(
        backend_client=object(),  # type: ignore[arg-type]
        scorer=_TRIVIAL_SCORER,
        pipeline_schema=_llm_only_schema(),
    )
    sp = JobSearchPoint(pipeline_params={"llm_only": {}})
    check = _StubCheck(n_min=4)

    batch = await _run_query_loop(
        sp,
        dataset,
        env,
        prior_results=prior,
        on_result=None,
        on_start=None,
        degradation_checks=[check],
        candidate_idx=1,
        n_total_candidates=3,
        save_run=None,
    )

    assert batch.stop_reason == "escalation"
    assert batch.escalation_signal is not None
    assert batch.escalation_signal.check_name == "elimination"
    assert len(batch.results) == 4, (
        "loop must stop after the 4th cached row — no real call past the threshold"
    )
    assert measure_calls == [], "no measure_sample call should happen: all 4 were cache hits"


# ---------------------------------------------------------------------------
# Bug 2 — consecutive backend errors return a signal, not a KeyboardInterrupt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consecutive_server_errors_return_scoring_error_abort(monkeypatch):
    """A 502 cascade returns ``scoring_error_abort`` signal, not KeyboardInterrupt.

    Pre-fix behavior raised ``KeyboardInterrupt`` out of ``score_search_point``
    which propagated up through the candidate loop and killed the campaign.
    """
    call_count = {"n": 0}

    async def fake_measure_sample(query_data, env, pipeline_params=None):
        call_count["n"] += 1
        return cast(
            QueryResult,
            {
                "query": query_data["query"],
                "ground_truth": query_data["ground_truth"],
                "predicted": "ERROR",
                "error": f"[{ErrorCategory.SERVER}] HTTP 502: gateway",
                "pipeline_data": None,
            },
        )

    monkeypatch.setattr(search_point_scorer, "measure_sample", fake_measure_sample)
    # Also patch sample_measurement in case load_reusable_results is imported
    monkeypatch.setattr(sample_measurement, "measure_sample", fake_measure_sample)

    dataset = [{"query": f"q{i}", "ground_truth": "gt"} for i in range(20)]

    env = ScoringEnv(
        backend_client=object(),  # type: ignore[arg-type]
        scorer=_TRIVIAL_SCORER,
        pipeline_schema=_llm_only_schema(),
        max_consecutive_errors=3,
    )
    sp = JobSearchPoint(pipeline_params={"llm_only": {}})

    results, _scores, was_cached, signal = await score_search_point(
        sp,
        dataset,
        env,
        label="test_candidate",
    )

    # Must not raise. Signal must carry the abort metadata.
    assert signal is not None
    assert signal.check_name == "scoring_error_abort"
    assert signal.target == EscalationTarget.ELIMINATE_CANDIDATE
    assert signal.check_result["degraded_count"] >= 3
    assert "HTTP 502" in signal.check_result["last_error"]
    # Backend stopped being called after 3 consecutive errors.
    assert call_count["n"] == 3
    # Padded to full dataset length so downstream averaging stays consistent.
    assert len(results) == len(dataset)
    assert was_cached is False


# ---------------------------------------------------------------------------
# Bug 2 — _handle_scored_candidate attaches a RuntimeFailure for the signal
# ---------------------------------------------------------------------------


class _StubElimCheck:
    """Minimal stub with the two methods ``_handle_scored_candidate`` uses."""

    name = "elimination"
    alpha = 0.2
    n_min = 4
    registrations: list[tuple[list[float], str]]

    def __init__(self) -> None:
        self.registrations = []

    def register_completed(self, scores: list[float], candidate_id: str = "") -> None:
        self.registrations.append((scores, candidate_id))

    def prior_ids_snapshot(self) -> list[str]:
        return []


def test_handle_scored_candidate_attaches_runtime_failure_for_error_abort():
    """``scoring_error_abort`` signal → RuntimeFailure + no prior registration."""
    osp = OptSearchPoint.from_prompt_fields({})

    dataset = [{"query": f"q{i}", "ground_truth": "gt"} for i in range(10)]
    results: list[QueryResult] = [
        cast(
            QueryResult,
            {
                "query": f"q{i}",
                "ground_truth": "gt",
                "predicted": "ERROR",
                "error": "[server] HTTP 502: gateway",
                "pipeline_data": None,
            },
        )
        for i in range(10)
    ]
    scores = {"accuracy": 0.0, "composite": 0.0, "hits": 0, "total": 0}
    signal = EscalationSignal(
        check_name="scoring_error_abort",
        target=EscalationTarget.ELIMINATE_CANDIDATE,
        check_result={
            "stop_reason": "skipped_after_consecutive_errors",
            "dominant_warning": "[server] HTTP 502: gateway",
            "warning_types": {str(ErrorCategory.SERVER): 3},
            "degraded_count": 3,
            "total_evaluated": 3,
            "last_error": "[server] HTTP 502: gateway",
        },
        candidate_idx=2,
        candidates_scored=3,
        candidates_skipped=2,
    )
    elim = _StubElimCheck()

    report, residual = _handle_scored_candidate(
        osp,
        {"llm_only": {"reasoning_effort": "minimal"}},
        results,
        scores,
        signal,
        {"llm_only": {"reasoning_effort": "minimal"}},
        dataset,
        elim,
        idx=2,
        n_candidates=5,
    )

    assert residual is None, "outer loop must continue to next candidate"
    assert report["elimination_stopped"] is True
    assert report["escalation_aborted"] is True
    assert report["runtime_failures"], "runtime_failures must be attached to the report"
    rf = report["runtime_failures"][0]
    assert rf["source"] == "scoring_error_abort"
    assert rf["observed_config"] == {"llm_only": {"reasoning_effort": "minimal"}}
    assert rf["degraded_count"] == 3
    # Candidate must NOT be registered as a prior — synthetic error scores
    # would poison the Wilcoxon distribution.
    assert elim.registrations == []
    # Memory carries the new RuntimeFailure for L2 next round.
    assert any(f.source == "scoring_error_abort" for f in osp.memory.runtime_failures)


def _ignore_unused() -> Any:
    # Keep type import used for mypy in strict setups.
    return EscalationSignal
