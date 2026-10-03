"""**Wrong-level guardrail:** state an optimizer carries ACROSS rounds rides ``working_state``, which
every round document snapshots as its ``optimizer_state`` — a bare field here does not survive a
resume."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.difficulty import DifficultyView
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import (
    ReferenceReading,
    RoundResult,
    ScoredCandidate,
    merge_known_outcomes,
)
from promptpotter.domain.ruler import AbilityReading
from promptpotter.domain.run_records import ResumeCheckpointRecord
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.optimizers.nodes import WorkingState
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = ["Cycle"]


def _origin_round(
    opt_sp: OptSearchPoint,
    sp: JobSearchPoint,
    *,
    report: ScoredCandidate,
    results: list[dict[str, Any]],
    ability: AbilityReading | None,
    optimizer_state: OptimizerState,
    stamps_theta: bool,
) -> RoundResult:
    """C0's row IS what the scoring gateway produced, plus the two facts only a round close can
    add: its θ on the cycle's δ ruler where the selector stamps one, and a reference that is
    itself. Nothing re-derived."""
    prompt_fields = opt_sp.prompt_field_dict()
    deprecated = _compute_accuracy(cast("list[QueryMeasurement]", results))["deprecated"]
    arm = ability if stamps_theta else None
    row = report.model_copy(
        update={
            "theta": arm.theta if arm is not None else None,
            "theta_se": arm.se if arm is not None else None,
            "prompt_fields": prompt_fields,
            "resolved_pipeline_params": sp.config_params,
            "reference_id": opt_sp.lineage.id,
            "reference_accuracy": report.accuracy,
            "reference_composite": report.composite_fitness,
        }
    )
    return RoundResult(
        round=0,
        label=row.label,
        accuracy=row.accuracy,
        composite_fitness=row.composite_fitness,
        total=row.total,
        # What the walk never sent — an abort pads no error rows onto the tail.
        not_attempted=max(0, row.expected_samples - row.scored_samples),
        improved=False,
        stamps_theta=stamps_theta,
        prompt_fields=prompt_fields,
        pipeline_params=sp.config_params,
        results=results,
        all_candidate_results={opt_sp.lineage.id: results},
        candidates_scored=1,
        candidate_scores=[row],
        selected_labels=[row.label],
        deprecated=deprecated,
        # Round 0's frontier reading IS the origin's, so the trend line starts on the θ scale too.
        ability=ability,
        evaluators=dict(row.evaluators),
        opt_sp=opt_sp,
        optimizer_state=optimizer_state,
    )


def _assert_overlay_preserved(
    sp: JobSearchPoint, session_pipeline_params: dict[str, Any] | None
) -> None:
    """``Cycle.start`` must pass the MERGED overlay, not a sparse schema view — ``content_hash``
    flips on an overlay edit only if those keys survive into ``sp.pipeline_params``."""
    sp_pp = sp.pipeline_params or {}
    for node, cfg in node_config_items(session_pipeline_params):
        missing = set(cfg) - set(sp_pp.get(node, {}))
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "Cycle.start must pass session.pipeline_params, not a sparse schema view"
        )


@dataclass
class CycleRoundState:
    """The searchpoint the cycle stands on and what it last measured. The origin's own scalars are
    NOT here — they are round 0's, read off ``Cycle.origin_round``."""

    current_sp: JobSearchPoint | None = None
    # ``None`` is unmeasured. It reads one round's rows, so it never names the result:
    # ``Cycle.selection``.
    current_accuracy: float | None = None
    current_composite_fitness: float = 0.0
    current_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig
    # The selected optimizer's own state, minted by its runtime; carried across every adoption and
    # snapshotted onto each round. Nothing here reads inside it.
    working_state: WorkingState
    difficulty: DifficultyView

    # 0-indexed: ``rounds[0]`` IS the origin's measurement, built by ``start`` before the loop
    # opens, so it is never absent.
    rounds: list[RoundResult] = field(default_factory=list)
    tracking: CycleRoundState = field(default_factory=CycleRoundState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    # The campaign's operator-authored framing, frozen for the run; every target render splices it.
    framing: TaskDecomposition = field(default_factory=TaskDecomposition)
    # The archive's per-sample history the scoring gateway reads; refreshed as each round closes.
    sample_index: SampleIndex | None = None
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    # A warm fit re-read round 0 after its document was saved; the next close re-saves it.
    origin_restamped: bool = False

    @classmethod
    def start(
        cls,
        resolved_origin: OptSearchPoint,
        origin_report: ScoredCandidate,
        *,
        schema: PipelineSchema,
        framing: TaskDecomposition,
        origin_results: list[dict[str, Any]] | None = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """``origin_report`` arrives ALREADY measured — nothing here recomputes its accuracy,
        composite or evaluator namespace."""
        opt_sp = resolved_origin
        selected = select_optimizer(config.optimization)
        working_state = selected.runtime.start(session, config, list(origin_results or []))
        # `session.pipeline_params` carries the dataset overlay; `schema.to_pipeline_params()`
        # is sparse and strips operator config.
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=session.pipeline_params or None,
            schema=schema,
            framing=framing,
            demo=session.scoring.require_partition().demo,
        )
        _assert_overlay_preserved(sp, session.pipeline_params)
        # Hashed here because ``opt_sp`` advances on ``adopt``.
        difficulty, origin_theta = DifficultyView.open(
            session,
            config,
            origin_sp_hash=sp.sp_hash(schema),
            origin_results=list(origin_results or []),
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
                    sp,
                    report=origin_report,
                    results=list(origin_results or []),
                    ability=difficulty.reading(origin_theta, results=list(origin_results or [])),
                    # C0's measurement is optimizer-independent but its critique is not, and
                    # a campaign paused before round 1 would otherwise hold nothing naming the
                    # optimizer it ran under.
                    optimizer_state=working_state.origin_state(selected),
                    stamps_theta=selected.stamps_theta,
                )
            ],
            tracking=CycleRoundState(
                current_sp=sp,
                current_accuracy=origin_report.accuracy,
                current_composite_fitness=origin_report.composite_fitness,
                current_results=origin_results or [],
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
        """The optimizer's declared pick: the last round whose selector kept anybody, round 0
        keeping the origin. The bench grades it and every result surface names it."""
        return next(rr for rr in reversed(self.rounds) if rr.selected_labels)

    @property
    def selected_sp(self) -> JobSearchPoint:
        picked = self.selection.opt_sp
        assert picked is not None, "a closed round names the individual it ended on"
        return self.searchpoint(picked.lineage.id)

    def searchpoint(
        self, individual_id: str, *, rounds: Sequence[RoundResult] | None = None
    ) -> JobSearchPoint:
        """Any individual a closed round measured, as it was measured — off what each round document
        banks: the individual it ended on with its params, and every arm's fields and params.
        ``rounds`` are the cycle's own unless a caller holds rounds it has not absorbed yet."""
        schema = self.session.pipeline_schema
        assert schema is not None, "a cycle is started under a pipeline_schema"

        def built(individual: OptSearchPoint, params: dict[str, Any]) -> JobSearchPoint:
            return individual.to_job_search_point(
                base_pipeline_params=params,
                schema=schema,
                framing=self.framing,
                demo=self.session.scoring.require_partition().demo,
            )

        for rr in reversed(self.rounds if rounds is None else rounds):
            ended_on = rr.opt_sp
            if ended_on and ended_on.lineage.id == individual_id and rr.pipeline_params is not None:
                return built(ended_on, rr.pipeline_params)
            for cs in rr.candidate_scores:
                if cs.candidate_id != individual_id or cs.resolved_pipeline_params is None:
                    continue
                sp = built(
                    OptSearchPoint.from_prompt_fields(cs.prompt_fields), cs.resolved_pipeline_params
                )
                if cs.sp_hash and sp.sp_hash(schema) != cs.sp_hash:
                    raise ValueError(
                        f"{cs.label}: its banked fields and params rebuild searchpoint "
                        f"{sp.sp_hash(schema)}, not the {cs.sp_hash} its rows were measured under"
                    )
                return sp
        raise KeyError(f"no closed round of this cycle measured individual {individual_id}")

    def restamp_origin_round(self, parent: ReferenceReading) -> None:
        """A whole round in, a whole round out, so a re-measure cannot leave one field reading from
        the run it replaces. The reading is carried, not re-fit: the ruler is locked."""
        assert self.tracking.current_sp is not None
        self.rounds[0] = _origin_round(
            self.opt_sp,
            self.tracking.current_sp,
            report=parent.report,
            results=list(parent.results),
            ability=self.origin_round.ability,
            optimizer_state=self.origin_round.optimizer_state,
            stamps_theta=self.origin_round.stamps_theta,
        )

    def replay_priors(self, priors: list[RoundResult]) -> None:
        """RE-RUNNABLE: rounds at or after *priors*' first number are REPLACED, and the frontier
        re-seeds from round 0 — so a second replay reconstructs instead of accumulating."""
        if not priors:
            return
        schema = self.session.pipeline_schema
        demo = self.session.scoring.require_partition().demo
        tr = self.tracking
        from_round = min(rr.round for rr in priors)
        self.rounds = [rr for rr in self.rounds if rr.round < from_round] + sorted(
            priors, key=lambda rr: rr.round
        )
        # Never return early on the grounds that ``start`` seeded tracking — true on a FIRST
        # call only. Re-seeding is what makes replaying rounds 0..k for ascending k reconstruct
        # each state instead of decorating the previous one.
        last_rr = self.rounds[-1]
        if last_rr.opt_sp is None or last_rr.pipeline_params is None:
            raise ValueError(
                f"round {last_rr.round} closed without an opt_sp / pipeline_params — "
                "the round file cannot seed a resume."
            )
        # Deep copy: the cycle mutates its working OSP, and the round is a record of what ran.
        self.opt_sp = last_rr.opt_sp.model_copy(deep=True)
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, last_rr.prompt_fields.get(f, ""))
        self.working_state.replay(last_rr)
        # The winner's OWN resolved params, off the round file — without them resume reverts
        # every config axis L1 won back to the origin floor.
        tr.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=last_rr.pipeline_params,
            schema=schema,
            framing=self.framing,
            demo=demo,
        )
        acc_cum: list[dict[str, Any]] = []
        for rr in self.rounds:
            acc_cum = merge_known_outcomes(acc_cum, list(rr.results))
        tr.current_results = acc_cum
        # Mirrors `absorb_round`: "current" is the last round's OWN measurement.
        tr.current_accuracy = last_rr.accuracy
        tr.current_composite_fitness = last_rr.composite_fitness

    def calibrate_ruler(self, measured: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
        if self.difficulty.calibrate(measured, self.rounds):
            self.origin_restamped = True

    def absorb_round(self, rr: RoundResult) -> RoundResult:
        """Sole sink for a finished round; returns the round, stamped for ``save_round_file``."""
        schema = self.session.pipeline_schema
        tr = self.tracking

        self.rounds.append(rr)
        # The winner OSP carries its own lineage, so IDENTITY moves forward rather than just
        # the six prompt strings. A HELD round returns the parent itself, so the lineage ids
        # match, nothing is adopted and no node is minted.
        winner_opt_sp = rr.opt_sp
        if winner_opt_sp is not None and winner_opt_sp.lineage.id != self.opt_sp.lineage.id:
            self.opt_sp = winner_opt_sp
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=_pp,
            schema=schema,
            framing=self.framing,
            demo=self.session.scoring.require_partition().demo,
        )
        tr.current_results = merge_known_outcomes(tr.current_results, list(rr.results))
        # "Current" is what the parent SCORED, never an accuracy over the mixed-provenance
        # pool above. On a held round `rr` already carries the parent's re-score for this
        # round's subset, so this stays a real measurement either way.
        tr.current_accuracy, tr.current_composite_fitness = rr.accuracy, rr.composite_fitness
        rr.ability = self.difficulty.frontier(tr.current_results)
        rr.opt_sp = self.opt_sp
        self.working_state.absorb(rr)
        return rr
