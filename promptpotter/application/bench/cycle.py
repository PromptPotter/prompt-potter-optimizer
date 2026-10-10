"""State an optimizer carries ACROSS rounds rides ``working_state``: a bare field does not survive a resume."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.bench.difficulty import DifficultyView
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.optimizers.nodes import round_state
from promptpotter.application.scoring.metrics import fold_cells
from promptpotter.application.scoring.row_diagnostics import count_degraded_samples
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.paired_reading import ReadingState
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import (
    DisplayMetric,
    OverlapReading,
    ReferenceReading,
    RoundResult,
    ScoredCandidate,
    declared_selection,
    measured_searchpoint,
    merge_known_outcomes,
    recall_at,
)
from promptpotter.domain.results_health import compute_round_health
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import ResumeCheckpointRecord
from promptpotter.domain.scoring import NO_CELLS
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.bench import BenchPasses
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import CellSheet, GradedCell

__all__ = ["Cycle"]


def _origin_round(
    opt_sp: OptSearchPoint,
    *,
    report: ScoredCandidate,
    results: CellSheet,
    ability: AbilityReading | None,
    optimizer_state: OptimizerState,
    elects_on: DisplayMetric,
    schema: PipelineSchema | None,
) -> RoundResult:
    deprecated = fold_cells(results).deprecated
    # An abort pads no error rows onto the tail.
    not_attempted = max(0, report.expected_samples - report.scored_samples)
    row = report.model_copy(
        update={
            "theta": ability.theta if ability is not None else None,
            "theta_se": ability.se if ability is not None else None,
        }
    )
    origin = RoundResult(
        round=0,
        label=row.label,
        accuracy=row.accuracy,
        composite_fitness=row.composite_fitness,
        total=row.total,
        not_attempted=not_attempted,
        health=compute_round_health(
            results=results.cells, prior_healths=[], is_origin=True, not_attempted=not_attempted
        ),
        improved=False,
        overlap=OverlapReading.unpaired(ReadingState.SAME_INDIVIDUAL, 0, False),
        elects_on=elects_on,
        prompt_fields=row.prompt_fields,
        pipeline_params=row.resolved_pipeline_params,
        results=results,
        all_candidate_results={opt_sp.id: results},
        candidates_scored=1,
        candidate_scores=[row],
        selected_labels=[row.label],
        leading_label=row.label,
        degraded_samples=count_degraded_samples(results),
        deprecated=deprecated,
        recall_at=recall_at(results),
        ability=ability,
        evaluators=dict(row.evaluators),
        opt_sp=opt_sp,
        optimizer_state=optimizer_state,
    )
    if not results:
        return origin
    # Without them round 1 opens blind to the origin's per-sample failure pattern.
    return origin.model_copy(
        update={"diagnostics": compute_round_diagnostics(origin, [origin], schema)}
    )


def _assert_overlay_preserved(
    sp: JobSearchPoint, session_pipeline_params: dict[str, Any] | None
) -> None:
    """``content_hash`` flips on an overlay edit only if those keys survive into ``sp.pipeline_params``."""
    sp_pp = sp.pipeline_params or {}
    for node, cfg in node_config_items(session_pipeline_params):
        missing = set(cfg) - set(sp_pp.get(node, {}))
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "the origin must carry session.pipeline_params, not a sparse schema view"
        )


@dataclass
class CycleRoundState:
    current_sp: JobSearchPoint | None = None
    # ``None`` is unmeasured; it reads ONE round's rows, so the result is ``Cycle.selection``.
    current_accuracy: float | None = None
    current_composite_fitness: float | None = None
    # The frontier: each sample's latest cell, of whichever individual measured it last.
    current_results: list[GradedCell] = field(default_factory=list)


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig
    working_state: WorkingState
    difficulty: DifficultyView

    # ``rounds[0]`` IS the origin's measurement, built by ``start``, so it is never absent.
    rounds: list[RoundResult] = field(default_factory=list)
    tracking: CycleRoundState = field(default_factory=CycleRoundState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    population: list[OptSearchPoint] = field(default_factory=list)
    framing: TaskDecomposition = field(default_factory=TaskDecomposition)
    sample_index: SampleIndex | None = None
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    # A warm fit re-read round 0 after its document was saved; the next close re-saves it.
    origin_restamped: bool = False
    bench_passes: BenchPasses | None = None

    @classmethod
    def start(
        cls,
        resolved_origin: OptSearchPoint,
        origin_report: ScoredCandidate,
        *,
        schema: PipelineSchema,
        framing: TaskDecomposition,
        origin_results: CellSheet | None = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        opt_sp = resolved_origin
        origin_sheet = NO_CELLS if origin_results is None else origin_results
        selected = select_optimizer(config.optimization)
        working_state = selected.runtime.start(session, config, origin_sheet)
        sp = opt_sp.to_job_search_point(
            schema=schema, framing=framing, demo=session.scoring.require_partition().demo
        )
        _assert_overlay_preserved(sp, session.pipeline_params)
        # Hashed here because ``opt_sp`` advances on ``adopt``.
        difficulty, origin_theta = DifficultyView.open(
            session,
            config,
            origin_sp_hash=sp.sp_hash(schema),
            origin_results=origin_sheet,
            scope="campaign" if session.controlled else "dataset",
        )

        return cls(
            session=session,
            config=config,
            working_state=working_state,
            difficulty=difficulty,
            rounds=[
                _origin_round(
                    opt_sp,
                    report=origin_report,
                    results=origin_sheet,
                    ability=difficulty.reading(origin_theta, results=origin_sheet),
                    # A campaign paused before round 1 holds nothing else naming its optimizer.
                    optimizer_state=round_state(selected, working_state.round_payload(), []),
                    elects_on=selected.elects_on,
                    schema=schema,
                )
            ],
            tracking=CycleRoundState(
                current_sp=sp,
                current_accuracy=origin_report.accuracy,
                current_composite_fitness=origin_report.composite_fitness,
                current_results=list(origin_sheet),
            ),
            opt_sp=opt_sp,
            framing=framing,
        )

    @property
    def origin_round(self) -> RoundResult:
        return self.rounds[0]

    @property
    def optimizer(self) -> SelectedOptimizer:
        return select_optimizer(self.config.optimization)

    @property
    def selection(self) -> RoundResult:
        return declared_selection(self.rounds)

    @property
    def selected_sp(self) -> JobSearchPoint:
        picked = self.selection.opt_sp
        assert picked is not None, "a closed round names the individual it ended on"
        return self.searchpoint(picked.id)

    def searchpoint(
        self, individual_id: str, *, rounds: Sequence[RoundResult] | None = None
    ) -> JobSearchPoint:
        schema = self.session.pipeline_schema
        assert schema is not None, "a cycle is started under a pipeline_schema"
        return measured_searchpoint(
            self.rounds if rounds is None else rounds,
            individual_id,
            schema=schema,
            framing=self.framing,
            demo=self.session.scoring.require_partition().demo,
        )

    def restamp_origin_round(self, parent: ReferenceReading) -> None:
        """The ability reading is carried, not re-fit: the ruler is locked."""
        self.rounds[0] = _origin_round(
            self.opt_sp,
            report=parent.report,
            results=parent.results,
            ability=self.origin_round.ability,
            optimizer_state=self.origin_round.optimizer_state,
            elects_on=self.origin_round.elects_on,
            schema=self.session.pipeline_schema,
        )
        tr = self.tracking
        tr.current_results = list(parent.results)
        tr.current_accuracy = parent.report.accuracy
        tr.current_composite_fitness = parent.report.composite_fitness

    def replay_priors(self, priors: list[RoundResult]) -> None:
        """Re-runnable: the frontier re-seeds from round 0, so a second replay does not accumulate."""
        if not priors:
            return
        schema = self.session.pipeline_schema
        demo = self.session.scoring.require_partition().demo
        tr = self.tracking
        from_round = min(rr.round for rr in priors)
        self.rounds = [rr for rr in self.rounds if rr.round < from_round] + sorted(
            priors, key=lambda rr: rr.round
        )
        # Never return early because ``start`` seeded tracking: that holds on a FIRST call only.
        last_rr = self.rounds[-1]
        if last_rr.opt_sp is None:
            raise ValueError(
                f"round {last_rr.round} closed without an opt_sp — the round file cannot seed "
                "a resume."
            )
        self.opt_sp = last_rr.opt_sp
        self.population = list(last_rr.optimizer_state.population)
        self.working_state.replay(last_rr)
        tr.current_sp = self.opt_sp.to_job_search_point(
            schema=schema, framing=self.framing, demo=demo
        )
        acc_cum: list[GradedCell] = []
        for rr in self.rounds:
            acc_cum = merge_known_outcomes(acc_cum, rr.results)
        tr.current_results = acc_cum
        tr.current_accuracy = last_rr.accuracy
        tr.current_composite_fitness = last_rr.composite_fitness

    def calibrate_ruler(self, measured: Mapping[str, CellSheet]) -> None:
        if (reread := self.difficulty.calibrate(measured, self.rounds)) is not None:
            self.rounds = reread
            self.origin_restamped = True

    def seat(self, rr: RoundResult) -> RoundResult:
        slot = next(i for i, held in enumerate(self.rounds) if held.round == rr.round)
        self.rounds[slot] = rr
        return rr

    def ability_after(self, results: CellSheet) -> AbilityReading | None:
        return self.difficulty.frontier(
            merge_known_outcomes(self.tracking.current_results, results)
        ).ability

    def absorb_round(self, rr: RoundResult) -> None:
        schema = self.session.pipeline_schema
        tr = self.tracking

        self.rounds.append(rr)
        # A HELD round returns the parent itself, so nothing is adopted.
        winner_opt_sp = rr.opt_sp
        if winner_opt_sp is not None and winner_opt_sp.id != self.opt_sp.id:
            self.opt_sp = winner_opt_sp
        assert tr.current_sp is not None
        tr.current_sp = self.opt_sp.to_job_search_point(
            schema=schema,
            framing=self.framing,
            demo=self.session.scoring.require_partition().demo,
        )
        tr.current_results = merge_known_outcomes(tr.current_results, rr.results)
        # What the parent SCORED this round, never an accuracy over the mixed-provenance pool above.
        tr.current_accuracy, tr.current_composite_fitness = rr.accuracy, rr.composite_fitness
        self.working_state.absorb(rr)
