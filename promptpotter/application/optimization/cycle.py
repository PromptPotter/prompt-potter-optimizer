"""Optimization cycle state — pure optimizer progress tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.decisions import Decision
from promptpotter.domain.analysis import RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import RoundBaseline, RoundResult
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.intelligence.axis_index import AxisIndex
    from promptpotter.application.optimization.nodes.l1.score import L1ScoringResult
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryResult
    from promptpotter.domain.search_point import TaskDecomposition


def _rf_dedup_key(rf_dict: dict) -> tuple:
    cfg = rf_dict.get("observed_config") or {}
    return (
        rf_dict.get("source", ""),
        rf_dict.get("dominant_warning", ""),
        json.dumps(cfg, sort_keys=True, default=str),
    )


__all__ = ["Cycle", "EscalationState", "LayerCounter"]


@dataclass
class LayerCounter:
    """Per-layer escalation tracking (stall count, round count, entry baseline)."""

    round: int = 0
    stall_count: int = 0
    best_accuracy_at_entry: float = 0.0
    best_composite_at_entry: float = 0.0

    def record_outcome(self, best_composite: float) -> bool:
        """Update stall_count after a round. Returns True if stalled (not improved)."""
        improved = best_composite > self.best_composite_at_entry
        self.stall_count = 0 if improved or self.round == 0 else self.stall_count + 1
        return not improved and self.round > 0

    def record_entry(self, best_accuracy: float, best_composite: float) -> None:
        self.round += 1
        self.best_accuracy_at_entry = best_accuracy
        self.best_composite_at_entry = best_composite


@dataclass
class EscalationState:
    """All escalation-layer tracking — L1 stall counter + L2/L3 ``LayerCounter`` instances."""

    l1_stall_count: int = 0
    l2: LayerCounter = field(default_factory=LayerCounter)
    l3: LayerCounter = field(default_factory=LayerCounter)

    def reset_for_l3(self, best_accuracy: float, best_composite: float) -> None:
        self.l2 = LayerCounter(
            best_accuracy_at_entry=best_accuracy,
            best_composite_at_entry=best_composite,
        )

    def to_checkpoint_dict(self) -> dict[str, Any]:
        """Serialize for trial dict — entry baselines required so resume preserves L2/L3 patience."""
        return {
            "l1_stall_count": self.l1_stall_count,
            "l2_round": self.l2.round,
            "l3_round": self.l3.round,
            "l2_stall_count": self.l2.stall_count,
            "l3_stall_count": self.l3.stall_count,
            "l2_best_accuracy_at_entry": self.l2.best_accuracy_at_entry,
            "l2_best_composite_at_entry": self.l2.best_composite_at_entry,
            "l3_best_accuracy_at_entry": self.l3.best_accuracy_at_entry,
            "l3_best_composite_at_entry": self.l3.best_composite_at_entry,
        }

    @classmethod
    def from_checkpoint_dict(cls, d: dict) -> EscalationState:
        """Restore — defaults the new entry-baseline keys to 0.0 for old trials."""
        return cls(
            l1_stall_count=d["l1_stall_count"],
            l2=LayerCounter(
                round=d["l2_round"],
                stall_count=d["l2_stall_count"],
                best_accuracy_at_entry=d.get("l2_best_accuracy_at_entry", 0.0),
                best_composite_at_entry=d.get("l2_best_composite_at_entry", 0.0),
            ),
            l3=LayerCounter(
                round=d["l3_round"],
                stall_count=d["l3_stall_count"],
                best_accuracy_at_entry=d.get("l3_best_accuracy_at_entry", 0.0),
                best_composite_at_entry=d.get("l3_best_composite_at_entry", 0.0),
            ),
        )


@dataclass
class Cycle:
    """Mutable orchestration state for the feedback cycle round loop."""

    session: Session
    config: CampaignConfig

    rounds: list[RoundResult] = field(default_factory=list)
    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    # Frozen at ``start()`` — the baseline composite that anchors the
    # campaign's "origin" for trajectory rendering. Lets renderers print
    # ``Δ from baseline=0.5012`` even at deep rounds.
    baseline_composite: float = 0.0

    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)

    probe_next_round: bool = False
    axes: AxisIndex | None = None
    escalation: EscalationState = field(default_factory=EscalationState)

    # Flushed into the next trial's ``decisions`` list before ``campaign_store.add_trial``.
    # Stored as ``Decision`` instances; converted to dict at the RoundResult boundary.
    pending_decisions: list[Decision] = field(default_factory=list)

    state_version: int = 1

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
        from promptpotter.application.scoring.metrics import compute_composite_score

        composite = (
            compute_composite_score(
                baseline_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )["composite"]
            if baseline_results and schema is not None
            else baseline_accuracy
        )
        opt_sp = baseline_osp.model_copy(
            update={
                "task_context": task_context,
                "optimizer_params": dict(baseline_osp.optimizer_params),
            }
        )
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=schema.to_pipeline_params() if schema else None,
            schema=schema,
        )
        return cls(
            session=session,
            config=config,
            current_sp=sp,
            current_accuracy=baseline_accuracy,
            current_composite=composite,
            current_results=baseline_results or [],
            best_accuracy=baseline_accuracy,
            best_composite=composite,
            best_sp=sp,
            opt_sp=opt_sp,
            baseline_composite=composite,
        )

    def restore_from_trial(self, trial: dict[str, Any]) -> None:
        """Restore optimizer state from a campaign checkpoint dict (in-place)."""
        self.opt_sp = OptSearchPoint(**trial["opt_search_point"])
        self.escalation = EscalationState.from_checkpoint_dict(trial)

    def adopt_transition(
        self,
        new_opt: OptSearchPoint,
        pipeline_params: dict | None,
        *,
        schema: PipelineSchema | None,
    ) -> None:
        """Adopt a new OptSearchPoint, preserving accumulated memory."""
        self.opt_sp.copy_memory_to(new_opt)
        self.opt_sp = new_opt
        assert self.current_sp is not None
        self.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=pipeline_params or self.current_sp.pipeline_params,
            schema=schema,
        )

    def update_current(
        self,
        rr: RoundResult,
        search_point: JobSearchPoint,
        round_num: int,
    ) -> None:
        """Apply a round result to current/best tracking."""
        self.current_sp = search_point
        self.current_accuracy = rr.accuracy
        self.current_composite = rr.composite
        self.current_results = list(rr.results)
        if self.current_composite > self.best_composite:
            self.best_composite = self.current_composite
            self.best_accuracy = self.current_accuracy
            self.best_round = round_num
            self.best_sp = self.current_sp

    def record_round(self, rr: RoundResult, round_num: int) -> None:
        """Append a RoundResult and propagate to memory + current/best tracking."""
        from promptpotter.domain.opt_search_point import RoundSummary
        from promptpotter.shared.constants import PROMPT_STRING_FIELDS

        schema = self.session.pipeline_schema
        self.rounds.append(rr)
        self.opt_sp.round_history.append(
            RoundSummary(
                round=rr.round,
                accuracy=rr.accuracy,
                composite=rr.composite,
                improved=rr.improved,
                degraded_queries=rr.degraded_queries,
                pipeline_params=rr.pipeline_params,
                candidate_scores=list(rr.candidate_scores),
            )
        )
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, rr.prompt_fields.get(f, ""))
        assert self.current_sp is not None
        _pp = (
            rr.pipeline_params
            if rr.pipeline_params is not None
            else self.current_sp.pipeline_params
        )
        self.update_current(
            rr,
            self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema),
            round_num,
        )

    def record_decision(self, d: Decision) -> None:
        """Queue a decision produced outside the normal round flow (escalation/probe)."""
        self.pending_decisions.append(d)

    def flush_decisions(self) -> list[Decision]:
        """Drain queued decisions (used before checkpointing a round)."""
        out = list(self.pending_decisions)
        self.pending_decisions.clear()
        return out

    def set_probe(self, flag: bool) -> None:
        """Mark whether the next round is a probe."""
        self.probe_next_round = flag

    def apply_round_outcome(self, scoring_result: L1ScoringResult, critique_text: str) -> None:
        """Fold per-round optimizer-memory updates onto ``opt_sp`` atomically.

        Single mutation point for: l1_critique_text, failure_analysis,
        warning_inventory (from all candidate results), runtime_failures
        (deduped by source/warning/observed-config).
        """
        from promptpotter.application.optimization.utils import update_query_tracker
        from promptpotter.application.scoring.metrics import compile_failure_analysis

        schema = self.session.pipeline_schema

        self.opt_sp.l1_critique_text = critique_text

        if scoring_result.winner_results and schema is not None:
            self.opt_sp.failure_analysis = compile_failure_analysis(
                scoring_result.winner_results, schema
            )
        else:
            self.opt_sp.failure_analysis = None

        # Aborted candidates also carry warnings — span all candidate results.
        all_results: list = [r for rs in scoring_result.all_candidate_results.values() for r in rs]
        if all_results:
            update_query_tracker(self.opt_sp.warning_inventory, all_results)

        existing_keys = {_rf_dedup_key(rf.to_dict()) for rf in self.opt_sp.runtime_failures}
        for cs in scoring_result.candidate_scores:
            for rf_dict in cs["runtime_failures"]:
                k = _rf_dedup_key(rf_dict)
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.runtime_failures.append(RuntimeFailure(**rf_dict))

    def baseline_for_round(self, scoring_dataset: list[Sample], round_num: int) -> RoundBaseline:
        """Build the round's baseline — probe-aware.

        On a probe round, the previous winner's accuracy is recomputed over the
        probe subset (probe queries are typically harder, so a probe round
        without subset-rescore would always look like regression).
        """
        from promptpotter.application.scoring.metrics import compute_composite_score

        schema = self.session.pipeline_schema
        accuracy = self.current_accuracy
        composite = self.current_composite
        results: list[dict] = list(self.current_results)
        if self.probe_next_round and self.current_results and schema is not None:
            probe_queries = {s.query for s in scoring_dataset}
            subset = [r for r in self.current_results if r.get("query") in probe_queries]
            if subset:
                subset_scores = compute_composite_score(
                    cast("list[QueryResult]", subset),
                    schema,
                    round_scorer=self.session.scoring.round_scorer,
                )
                accuracy = subset_scores["accuracy"]
                composite = subset_scores.get("composite", accuracy)
                results = subset
        return RoundBaseline(
            accuracy=accuracy,
            composite=composite,
            osp=self.opt_sp,
            results=results,
            label=f"round_{round_num}" if round_num > 0 else "baseline",
        )

    def checkpoint(self, rr: RoundResult, round_num: int) -> dict[str, Any]:
        """Build the trial dict for ``campaign_store.add_trial`` — self-contained replay."""
        return {
            "trial_id": f"round_{round_num}",
            "round": round_num,
            "label": rr.label,
            "accuracy": rr.accuracy,
            "composite": rr.composite,
            "hits": rr.hits,
            "total": rr.total,
            "improved": rr.improved,
            "prompt_fields": rr.prompt_fields,
            "results": rr.results,
            "all_candidate_results": dict(rr.all_candidate_results),
            "candidates_scored": rr.candidates_scored,
            "candidate_scores": list(rr.candidate_scores),
            "decisions": list(rr.decisions),
            "evaluators": dict(rr.evaluators),
            **self.escalation.to_checkpoint_dict(),
            "opt_search_point": self.opt_sp.model_dump(),
            **(
                {"scoring_set_events": list(rr.scoring_set_events)} if rr.scoring_set_events else {}
            ),
        }
