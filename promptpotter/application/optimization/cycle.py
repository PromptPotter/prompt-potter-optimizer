"""Cycle state — round-loop mutable orchestration object."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.optimization.pobb.elimination import extract_warning_types
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    RoundOrigin,
    RoundResult,
    ScoredCandidate,
    is_round_winner,
)
from promptpotter.domain.run_records import RebaseRequest, ResumeCheckpointRecord
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.exploration import Observation
    from promptpotter.application.intelligence.indexes import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import TaskDecomposition

logger = logging.getLogger(__name__)

__all__ = ["Cycle", "CycleRoundState"]


def _build_scoreboard(
    candidate_scores: list[ScoredCandidate], winner_label: str
) -> list[dict[str, Any]]:
    """Trial-JSON `scoreboard`: rank by (composite_fitness, accuracy) desc; tag winner."""
    ranked = sorted(
        candidate_scores,
        key=lambda c: (c.composite_fitness, c.accuracy),
        reverse=True,
    )
    rows: list[dict[str, Any]] = []
    for i, c in enumerate(ranked, start=1):
        rows.append(
            {
                "rank": i,
                "candidate_id": c.candidate_id,
                "label": c.changes_description,
                "accuracy": c.accuracy,
                "composite_fitness": c.composite_fitness,
                "hits": c.hits,
                "total": c.total,
                "ci_lo": c.ci_lo,
                "ci_hi": c.ci_hi,
                "is_winner": is_round_winner(c.changes_description, winner_label),
                "escalation_aborted": c.escalation_aborted,
                "matched_origin_accuracy": c.matched_origin_accuracy,
                "matched_origin_hits": c.matched_origin_hits,
                "matched_origin_composite": c.matched_origin_composite,
            }
        )
    return rows


def _rf_dedup_key(rf_dict: dict[str, Any]) -> tuple[str, str, str]:
    cfg = rf_dict.get("observed_config") or {}
    return (
        rf_dict.get("source", ""),
        rf_dict.get("dominant_warning", ""),
        json.dumps(cfg, sort_keys=True, default=str),
    )


def _merge_into_cumulative(
    prior: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Sample-keyed merge: incoming overwrites prior; entries without ``sample_id`` dropped.
    Keeps ``tr.current_results`` a non-shrinking cumulative origin across rounds."""
    by_sid: dict[Any, dict[str, Any]] = {
        r.get("sample_id"): r for r in prior if r.get("sample_id") is not None
    }
    for r in incoming:
        sid = r.get("sample_id")
        if sid is not None:
            by_sid[sid] = r
    return list(by_sid.values())


def _build_initial_opt_sp(
    resolved_origin: OptSearchPoint, task_context: TaskDecomposition
) -> OptSearchPoint:
    """Seed the optimizer state from a scored origin: nest task_context + a copied
    l1_overrides dict under ``memory`` so L2/L3 mutations don't share references."""
    return resolved_origin.model_copy(
        update={
            "memory": resolved_origin.memory.model_copy(
                update={
                    "task_context": task_context,
                    "l1_overrides": dict(resolved_origin.memory.l1_overrides),
                }
            ),
        }
    )


def _assert_overlay_preserved(
    sp: JobSearchPoint, session_pipeline_params: dict[str, Any] | None
) -> None:
    """Dataset-overlay keys must survive into ``sp.pipeline_params`` so ``content_hash``
    flips on overlay edits and the wire adapter forwards the overlay. ``Cycle.start``
    must pass ``session.pipeline_params`` (the merged overlay), not a sparse schema view."""
    sp_pp = sp.pipeline_params or {}
    for node, cfg in (session_pipeline_params or {}).items():
        if node == "steps" or not isinstance(cfg, dict):
            continue
        missing = set(cfg) - set(sp_pp.get(node, {}))
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "Cycle.start must pass session.pipeline_params, not a sparse schema view"
        )


def _load_archive_observations(session: Session) -> list[Observation]:
    """Per-(backend, dataset) prior-measurement observations for the intelligence layer."""
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_observations,
    )

    return build_archive_observations(
        session.store,
        session.backend_id,
        dataset_name=session.dataset_name,
    )


def _inherit_sibling_runtime_failures(opt_sp: OptSearchPoint, session: Session) -> None:
    """Pull RuntimeFailures from sibling forks of this cycle's root so L1 sees configs
    prior siblings already proved to fail (``_r_runtime_failures`` filters by pipeline match)."""
    from promptpotter.application.intelligence.sibling_wounds import (
        gather_sibling_runtime_failures,
    )
    from promptpotter.infrastructure.store import root_cycle_id as _root_cycle_id

    if not session.state.cycle_id:
        return
    try:
        root_id = _root_cycle_id(session.state.cycle_id)
        failures = gather_sibling_runtime_failures(
            session.store,
            session.campaign_id,
            root_id,
            session.backend_id,
            exclude_cycle_id=session.state.cycle_id,
        )
    except Exception:
        logger.debug("sibling runtime_failures inheritance skipped", exc_info=True)
        return
    if failures:
        opt_sp.memory.wounds.runtime_failures.extend(failures)
        logger.info(
            "inherited %d runtime_failures from sibling forks of %s",
            len(failures),
            session.state.cycle_id,
        )


@dataclass
class CycleRoundState:
    """Current/best/origin searchpoint trajectory; ``origin_*`` is round-0 frozen."""

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite_fitness: float = 0.0
    current_results: list[dict[str, Any]] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite_fitness: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    origin_accuracy: float = 0.0
    origin_composite_fitness: float = 0.0
    origin_per_sample_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Cycle:
    """Mutable orchestration state for the feedback cycle round loop."""

    session: Session
    config: CampaignConfig

    rounds: list[RoundResult] = field(default_factory=list)
    tracking: CycleRoundState = field(default_factory=CycleRoundState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    # L2 inter-round bridge: probe_next_round set on action="probe_round" (consumed next round); last_l2_axis labels the probe.
    probe_next_round: bool = False
    last_l2_axis: str = ""
    warned_queries: set[str] = field(default_factory=set)
    axes: AxisIndex | None = None
    escalation: EscalationFSM = field(default_factory=EscalationFSM)
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    state_version: int = 1
    last_rasch_posterior: Any = None
    archive_observations: list[Observation] = field(default_factory=list)
    # Stashed by L2/L3 rebase emission; runner.entry resolves it post-finalize
    # into _mint_fork + observer rebuild + loop re-entry on the new fork.
    rebase_request: RebaseRequest | None = None

    @classmethod
    def start(
        cls,
        resolved_origin: OptSearchPoint,
        origin_accuracy: float,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema | None,
        origin_results: list[dict[str, Any]] | None = None,
        round_scorer: Any = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """Construct a fresh Cycle from a scored origin."""
        composite_fitness = (
            compute_composite_fitness(
                origin_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )["composite_fitness"]
            if origin_results and schema is not None
            else origin_accuracy
        )
        opt_sp = _build_initial_opt_sp(resolved_origin, task_context)
        # Pass session.pipeline_params (carries dataset overlay) — schema.to_pipeline_params() is sparse and strips operator config.
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=session.pipeline_params or None,
            schema=schema,
        )
        _assert_overlay_preserved(sp, session.pipeline_params)
        _inherit_sibling_runtime_failures(opt_sp, session)
        return cls(
            session=session,
            config=config,
            tracking=CycleRoundState(
                current_sp=sp,
                current_accuracy=origin_accuracy,
                current_composite_fitness=composite_fitness,
                current_results=origin_results or [],
                best_accuracy=origin_accuracy,
                best_composite_fitness=composite_fitness,
                best_sp=sp,
                origin_accuracy=origin_accuracy,
                origin_composite_fitness=composite_fitness,
                origin_per_sample_results=list(origin_results or []),
            ),
            opt_sp=opt_sp,
            archive_observations=_load_archive_observations(session),
        )

    def replay_priors(self, priors: list[dict[str, Any]]) -> None:
        """Reconstruct round-loop state from persisted prior rounds (in-place).

        ``EscalationFSM`` is NOT touched — caller rebuilds via ``from_ledger``.
        """
        if not priors:
            return
        schema = self.session.pipeline_schema
        tr = self.tracking
        for round_data in priors:
            rr = RoundResult.model_validate(round_data)
            self.rounds.append(rr)
            for r in rr.results or []:
                if extract_warning_types(r) and (q := r.get("query")):
                    self.warned_queries.add(q)
        last = priors[-1]
        self.opt_sp = OptSearchPoint(**last["opt_search_point"])
        last_rr = self.rounds[-1]
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, last_rr.prompt_fields.get(f, ""))
        assert tr.current_sp is not None
        last_pp = (
            last_rr.pipeline_params
            if last_rr.pipeline_params is not None
            else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=last_pp, schema=schema)
        # best_round = cumulative-state high-water-mark (mirrors absorb_round);
        # without this, resume/fork after lock-in reseeds PoBB priors on the wrong subset.
        acc_cum: list[dict[str, Any]] = []
        for i, rr in enumerate(self.rounds, start=1):
            acc_cum = _merge_into_cumulative(acc_cum, list(rr.results))
            if schema is not None:
                cumi = compute_composite_fitness(
                    cast("list[QueryMeasurement]", acc_cum),
                    schema,
                    opt_sp=None,
                    round_scorer=self.session.scoring.round_scorer,
                )
                cum_acc = cumi["accuracy"]
                cum_comp = cumi["composite_fitness"]
            else:
                cum_acc = rr.accuracy
                cum_comp = rr.composite_fitness
            if cum_comp > tr.best_composite_fitness:
                tr.best_composite_fitness = cum_comp
                tr.best_accuracy = cum_acc
                tr.best_round = i
                tr.best_sp = self.opt_sp.to_job_search_point(
                    base_pipeline_params=(rr.pipeline_params or last_pp), schema=schema
                )
        tr.current_results = acc_cum
        if schema is not None:
            cum = compute_composite_fitness(
                cast("list[QueryMeasurement]", tr.current_results),
                schema,
                opt_sp=None,
                round_scorer=self.session.scoring.round_scorer,
            )
            tr.current_accuracy = cum["accuracy"]
            tr.current_composite_fitness = cum["composite_fitness"]
        else:
            tr.current_accuracy = last_rr.accuracy
            tr.current_composite_fitness = last_rr.composite_fitness

    def absorb_round(
        self,
        rr: RoundResult,
        round_num: int,
    ) -> dict[str, Any]:
        """Sole sink for a finished L1 round; returns the trial dict for ``save_round_file``."""
        schema = self.session.pipeline_schema
        tr = self.tracking

        all_results: list[Any] = [r for rs in rr.all_candidate_results.values() for r in rs]
        for r in all_results:
            if extract_warning_types(r) and (q := r.get("query")):
                self.warned_queries.add(q)
        existing_keys = {
            _rf_dedup_key(rf.model_dump()) for rf in self.opt_sp.memory.wounds.runtime_failures
        }
        for cs in rr.candidate_scores:
            for rf in cs.runtime_failures:
                k = _rf_dedup_key(rf.model_dump())
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.memory.wounds.runtime_failures.append(rf)

        self.rounds.append(rr)
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, rr.prompt_fields.get(f, ""))
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_results = _merge_into_cumulative(tr.current_results, list(rr.results))
        if schema is not None:
            # opt_sp=None: cumulative pool mixes multiple searchpoints; opt_sp-aware evaluators take their vacuous fallback (per matched_origin_stats convention).
            cum = compute_composite_fitness(
                cast("list[QueryMeasurement]", tr.current_results),
                schema,
                opt_sp=None,
                round_scorer=self.session.scoring.round_scorer,
            )
            tr.current_accuracy = cum["accuracy"]
            tr.current_composite_fitness = cum["composite_fitness"]
        else:
            tr.current_accuracy = rr.accuracy
            tr.current_composite_fitness = rr.composite_fitness
        if tr.current_composite_fitness > tr.best_composite_fitness:
            tr.best_composite_fitness = tr.current_composite_fitness
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp

        rr.cumulative_total = len(tr.current_results)
        rr.cumulative_accuracy = tr.current_accuracy

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
            "origin_accuracy": rr.origin_accuracy,
            "matched_origin_accuracy": rr.matched_origin_accuracy,
            "matched_origin_hits": rr.matched_origin_hits,
            "matched_origin_composite": rr.matched_origin_composite,
            "cumulative_total": rr.cumulative_total,
            "cumulative_accuracy": rr.cumulative_accuracy,
            "scoreboard": _build_scoreboard(rr.candidate_scores, rr.label),
            "prompt_fields": rr.prompt_fields,
            "results": rr.results,
            "all_candidate_results": dict(rr.all_candidate_results),
            "sample_order_timeline": [s.model_dump() for s in rr.sample_order_timeline],
            "candidates_scored": rr.candidates_scored,
            "candidate_scores": [c.model_dump() for c in rr.candidate_scores],
            "decisions": list(rr.decisions),
            "evaluators": dict(rr.evaluators),
            "critique": rr.critique,
            "opt_search_point": self.opt_sp.model_dump(),
        }

    def origin_for_round(self, scoring_set: list[Sample], round_num: int) -> RoundOrigin:
        """Build round origin; on probe rounds, rescore over the probe subset."""
        schema = self.session.pipeline_schema
        tr = self.tracking
        accuracy = tr.current_accuracy
        composite_fitness = tr.current_composite_fitness
        results: list[dict[str, Any]] = list(tr.current_results)
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
        return RoundOrigin(
            accuracy=accuracy,
            composite_fitness=composite_fitness,
            osp=self.opt_sp,
            results=results,
            label=f"round_{round_num}" if round_num > 0 else "origin",
        )
