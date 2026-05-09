"""Cycle state — round-loop mutable orchestration object.

Resume-checkpoint records, the divergence walker, and the fork-mint
helpers live in :mod:`promptpotter.application.optimization.resume_and_fork`
— this file only owns the in-memory ``Cycle`` dataclass + per-round
mutation. Escalation FSM (``EscalationState``, ``NextAction``,
``EscalationEvent``, ``build_escalation_entry``) lives in
:mod:`promptpotter.application.optimization.escalation.state`; the L2/L3
firing driver lives in :mod:`.escalation.firing`. One-way arrow: this
module imports from ``escalation.state``; the reverse is forbidden —
``firing`` imports from cycle.py via ``TYPE_CHECKING`` only.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.elimination import update_sample_tracker
from promptpotter.application.optimization.escalation.state import EscalationState
from promptpotter.application.scoring.metrics import (
    compile_failure_analysis,
    compute_composite_fitness,
)
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.analysis import RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint, RoundSummary
from promptpotter.domain.results import RoundBaseline, RoundResult
from promptpotter.domain.run_records import DecisionRecord
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import TaskDecomposition

logger = logging.getLogger(__name__)

__all__ = ["Cycle", "TrackingState"]


def _build_scoreboard(
    candidate_scores: list[dict[str, Any]], winner_label: str
) -> list[dict[str, Any]]:
    """Trial-JSON `scoreboard`: rank by (composite_fitness, accuracy) desc; tag winner."""
    ranked = sorted(
        candidate_scores,
        key=lambda c: (c["composite_fitness"], c["accuracy"]),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ranked, start=1):
        is_winner = bool(c.get("changes_description")) and c["changes_description"] == winner_label
        rows.append(
            {
                "rank": i,
                "candidate_id": c.get("candidate_id"),
                "label": c.get("changes_description", ""),
                "accuracy": c.get("accuracy"),
                "composite_fitness": c.get("composite_fitness"),
                "hits": c.get("hits"),
                "total": c.get("total"),
                "ci_lo": c.get("ci_lo"),
                "ci_hi": c.get("ci_hi"),
                "is_winner": is_winner,
                "escalation_aborted": c.get("escalation_aborted", False),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Cycle state — escalation counters + per-round mutation
# ---------------------------------------------------------------------------


def _rf_dedup_key(rf_dict: dict) -> tuple:
    cfg = rf_dict.get("observed_config") or {}
    return (
        rf_dict.get("source", ""),
        rf_dict.get("dominant_warning", ""),
        json.dumps(cfg, sort_keys=True, default=str),
    )


@dataclass
class TrackingState:
    """Current/best searchpoint trajectory + frozen baseline composite_fitness."""

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite_fitness: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite_fitness: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    baseline_composite_fitness: float = 0.0


@dataclass
class Cycle:
    """Mutable orchestration state for the feedback cycle round loop."""

    session: Session
    config: CampaignConfig

    rounds: list[RoundResult] = field(default_factory=list)
    tracking: TrackingState = field(default_factory=TrackingState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    # Inter-round bridge state: ``probe_next_round`` set by L2 when it picks
    # ``action="probe_round"``; consumed and reset on the very next round.
    # ``last_l2_axis`` is the most recent ``axis_targeted`` from L2, read by
    # ``compute_round_diagnostics`` to label probe outcomes. Neither is folded
    # from the ledger today: a resume that lands inside the one-round gap
    # between an L2 fire and the probe round drops the probe (the next round
    # runs full-set) and renders ``axis_tested=""``. Acceptable trade —
    # ``PROBE_ROUND_COMMITMENT`` is ARCHIVAL so divergence isn't detected,
    # and probe outcomes are L2/L3 telemetry rather than control-flow signals.
    probe_next_round: bool = False
    last_l2_axis: str = ""
    axes: AxisIndex | None = None
    escalation: EscalationState = field(default_factory=EscalationState)
    # Flushed into the next round_data's `decisions` before campaign_store.save_round_file.
    pending_decisions: list[DecisionRecord] = field(default_factory=list)
    state_version: int = 1
    # Round-end Rasch posterior; one fit per round, reused by finalize.
    last_rasch_posterior: Any = None

    @classmethod
    def start(
        cls,
        baseline_osp: OptSearchPoint,
        baseline_accuracy: float,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema | None,
        baseline_results: list[dict] | None = None,
        round_scorer: Any = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """Construct a fresh Cycle from a scored baseline."""
        composite_fitness = (
            compute_composite_fitness(
                baseline_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )["composite_fitness"]
            if baseline_results and schema is not None
            else baseline_accuracy
        )
        opt_sp = baseline_osp.model_copy(
            update={
                "task_context": task_context,
                "l1_config": dict(baseline_osp.l1_config),
            }
        )
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=schema.to_pipeline_params() if schema else None,
            schema=schema,
        )
        return cls(
            session=session,
            config=config,
            tracking=TrackingState(
                current_sp=sp,
                current_accuracy=baseline_accuracy,
                current_composite_fitness=composite_fitness,
                current_results=baseline_results or [],
                best_accuracy=baseline_accuracy,
                best_composite_fitness=composite_fitness,
                best_sp=sp,
                baseline_composite_fitness=composite_fitness,
            ),
            opt_sp=opt_sp,
        )

    def restore_from_trial(self, round_data: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place).

        ``EscalationState`` is NOT read from the round_data — it's a projection of
        the ledger and must be rebuilt by the resume path via
        ``EscalationState.from_ledger``.
        """
        self.opt_sp = OptSearchPoint(**round_data["opt_search_point"])

    def absorb_round(
        self,
        rr: RoundResult,
        round_num: int,
    ) -> dict[str, Any]:
        """Sole sink for a finished L1 round: fold optimizer-memory onto opt_sp,
        append the round, propagate tracking, project the trial dict.

        l1.py never mutates Cycle — it returns the round result (which
        already carries ``rr.critique`` from ``run_l1_critique``) and the
        runner calls this once at the round boundary. The returned dict
        is the input to ``save_round_file`` on the normal path; probe and
        escalation paths discard it.
        """
        schema = self.session.pipeline_schema
        tr = self.tracking

        # 1. opt_sp memory — failure analysis, warning inventory, runtime failures.
        if rr.results and schema is not None:
            self.opt_sp.failure_analysis = compile_failure_analysis(
                cast("list[QueryMeasurement]", rr.results), schema
            )
        else:
            self.opt_sp.failure_analysis = None
        all_results: list = [r for rs in rr.all_candidate_results.values() for r in rs]
        if all_results:
            update_sample_tracker(self.opt_sp.warning_inventory, all_results)
        existing_keys = {_rf_dedup_key(rf.to_dict()) for rf in self.opt_sp.runtime_failures}
        for cs in rr.candidate_scores:
            for rf_dict in cs.get("runtime_failures") or []:
                k = _rf_dedup_key(rf_dict)
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.runtime_failures.append(RuntimeFailure(**rf_dict))

        # 2. round + history + tracking.
        self.rounds.append(rr)
        self.opt_sp.round_history.append(
            RoundSummary(
                round=rr.round,
                accuracy=rr.accuracy,
                composite_fitness=rr.composite_fitness,
                improved=rr.improved,
                degraded_samples=rr.degraded_samples,
                pipeline_params=rr.pipeline_params,
                candidate_scores=list(rr.candidate_scores),
            )
        )
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, rr.prompt_fields.get(f, ""))
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_accuracy = rr.accuracy
        tr.current_composite_fitness = rr.composite_fitness
        tr.current_results = list(rr.results)
        if tr.current_composite_fitness > tr.best_composite_fitness:
            tr.best_composite_fitness = tr.current_composite_fitness
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp

        # 3. trial dict — pure projection of post-mutation state.
        return {
            "round_id": f"round_{round_num}",
            "round": round_num,
            "label": rr.label,
            "accuracy": rr.accuracy,
            "composite_fitness": rr.composite_fitness,
            "hits": rr.hits,
            "total": rr.total,
            "improved": rr.improved,
            "p_value": rr.p_value,
            "baseline_accuracy": rr.baseline_accuracy,
            "scoreboard": _build_scoreboard(rr.candidate_scores, rr.label),
            "prompt_fields": rr.prompt_fields,
            "results": rr.results,
            "all_candidate_results": dict(rr.all_candidate_results),
            "candidates_scored": rr.candidates_scored,
            "candidate_scores": list(rr.candidate_scores),
            "decisions": list(rr.decisions),
            "evaluators": dict(rr.evaluators),
            "critique": rr.critique,
            "opt_search_point": self.opt_sp.model_dump(),
            **(
                {"scoring_set_events": list(rr.scoring_set_events)} if rr.scoring_set_events else {}
            ),
        }

    def baseline_for_round(self, scoring_set: list[Sample], round_num: int) -> RoundBaseline:
        """Build round baseline; on probe rounds, rescore over the probe subset."""
        schema = self.session.pipeline_schema
        tr = self.tracking
        accuracy = tr.current_accuracy
        composite_fitness = tr.current_composite_fitness
        results: list[dict] = list(tr.current_results)
        if self.probe_next_round and tr.current_results and schema is not None:
            probe_queries = {s.query for s in scoring_set}
            subset = [r for r in tr.current_results if r.get("query") in probe_queries]
            if subset:
                subset_scores = compute_composite_fitness(
                    cast("list[QueryMeasurement]", subset),
                    schema,
                    round_scorer=self.session.scoring.round_scorer,
                )
                accuracy = subset_scores["accuracy"]
                composite_fitness = subset_scores.get("composite_fitness", accuracy)
                results = subset
        return RoundBaseline(
            accuracy=accuracy,
            composite_fitness=composite_fitness,
            osp=self.opt_sp,
            results=results,
            label=f"round_{round_num}" if round_num > 0 else "baseline",
        )
