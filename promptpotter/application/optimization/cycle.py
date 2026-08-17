"""**Wrong-level guardrail:** state a layer carries ACROSS generations belongs on
``OptSearchPoint``, never a field here — one added here is invisible to ``derive()``."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

# Leaf import, never the package surface: `escalation/__init__` loads the firing driver, which
# depends back on Cycle.
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    compute_optimizer_prompt_hashes,
)
from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.scoring.metrics import _compute_accuracy
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import rf_dedup_key
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import (
    RoundParent,
    RoundResult,
    ScoredCandidate,
    merge_known_outcomes,
)
from promptpotter.domain.run_records import RebaseRequest, ResumeCheckpointRecord
from promptpotter.domain.search_point import JobSearchPoint

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.exploration import Observation, RulerEntry
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CalibrationModel
    from promptpotter.domain.scoring import QueryMeasurement

logger = logging.getLogger(__name__)

__all__ = ["Cycle"]


def _origin_round(
    opt_sp: OptSearchPoint,
    sp: JobSearchPoint,
    *,
    report: ScoredCandidate,
    results: list[dict[str, Any]],
    theta: tuple[float, float] | None,
    calibration_model: CalibrationModel | None,
) -> RoundResult:
    """C0's row IS what the scoring gateway produced, plus the two facts only a round close can
    add: its θ on the cycle's δ ruler, and a matched origin that is itself. Nothing re-derived."""
    prompt_fields = {**opt_sp.prompt_field_dict(), "lineage": opt_sp.lineage.model_dump()}
    deprecated = _compute_accuracy(cast("list[QueryMeasurement]", results))["deprecated"]
    row = report.model_copy(
        update={
            "theta": theta[0] if theta is not None else None,
            "theta_se": theta[1] if theta is not None else None,
            "prompt_fields": prompt_fields,
            "resolved_pipeline_params": sp.config_params,
            "matched_parent_accuracy": report.accuracy,
            "matched_parent_composite": report.composite_fitness,
        }
    )
    return RoundResult(
        round=0,
        label=row.label,
        accuracy=row.accuracy,
        composite_fitness=row.composite_fitness,
        total=row.total,
        improved=False,
        origin_accuracy=row.accuracy,
        matched_parent_accuracy=row.accuracy,
        matched_parent_composite=row.composite_fitness,
        prompt_fields=prompt_fields,
        pipeline_params=sp.pipeline_params,
        results=results,
        all_candidate_results={opt_sp.lineage.id: results},
        candidates_scored=1,
        candidate_scores=[row],
        deprecated=deprecated,
        # Round 0's frontier θ IS the origin's, so the trend line starts on the θ scale too.
        cumulative_theta=theta[0] if theta is not None else None,
        cumulative_theta_se=theta[1] if theta is not None else None,
        calibration_model=calibration_model,
        evaluators=dict(row.evaluators),
        opt_sp=opt_sp,
        # C0's measurement is optimizer-independent but its critique is not, and a campaign
        # paused before round 1 would otherwise hold nothing naming the optimizer it ran under.
        optimizer_prompt_hashes=compute_optimizer_prompt_hashes(),
    )


def _build_initial_opt_sp(resolved_origin: OptSearchPoint) -> OptSearchPoint:
    """Copies ``l1_overrides`` so later L2/L3 mutations do not share references with the origin.
    The framing is NOT re-set here: the resolver stamped it, and it is what identity hashed."""
    return resolved_origin.model_copy(
        update={
            "memory": resolved_origin.memory.model_copy(
                update={"l1_overrides": dict(resolved_origin.memory.l1_overrides)}
            ),
        }
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


def _calibrate_delta_ruler(
    origin_results: list[dict[str, Any]] | None,
    n_min: int,
    *,
    enable_2pl: bool,
    archive_obs: list[Observation],
) -> tuple[dict[int, RulerEntry], tuple[float, float] | None, CalibrationModel | None]:
    """The per-cycle FIXED ruler every later θ readout is measured against
    (``docs/methods/verdict-resolution.md``). Cold start returns a FLAT ruler and a
    ``None`` model."""
    from promptpotter.application.intelligence.exploration import (
        ORIGIN_ABILITY_ID,
        Observation,
        dedup_observations,
        fit_theta_given_delta,
        graded_response,
        graduate_ruler_model,
    )
    from promptpotter.shared.errors import is_error_result

    origin_obs = [
        Observation(ORIGIN_ABILITY_ID, int(sid), graded_response(r))
        for r in origin_results or []
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    ]
    # ``origin_obs`` wins the cells both hold: an inner cycle that cache-replays its origin
    # banked that run INSIDE its own evidence epoch, where the archive read cannot see it.
    obs = dedup_observations(archive_obs, origin_obs)
    if not obs:
        return {}, None, None
    # Two warmth conditions, both knowable without fitting — below either the ruler stays flat
    # and the fit would be discarded unread, so skip the 1PL + 2PL + CV storm entirely.
    # DISTINCT SAMPLES ≥ n_min: both fits key δ on ``sorted({o.sample_id})``.
    # DISTINCT ARMS ≥ 2: δ is identified only against a second ability. With one arm the anchor
    # pins θ and δ collapses to that arm's own hit pattern in two values, so every later θ in
    # the cycle restates whether round 0 happened to get the sample right, on a scale where the
    # origin sits at 0.000 by construction. That is exactly what a fresh campaign hands this
    # function, since 40 origin rows from one candidate clear any sample floor alone. One arm
    # therefore stays FLAT and re-attempts next round, once the round's own candidates are
    # banked grade-A and the fit has arms to compare.
    ruler: dict[int, RulerEntry] = {}
    model: CalibrationModel | None = None
    if len({o.sample_id for o in obs}) >= n_min and len({o.candidate_id for o in obs}) >= 2:
        fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
        # Cold below the floor: too few banked samples to trust a fitted ruler → stay flat.
        if len(post.delta) >= n_min:
            ruler, model = post.ruler(), fitted
            if model == "2PL":
                logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    # θ_C0 THROUGH THE SAME ESTIMATOR EVERY OTHER LEVEL USES. Never hand back the JOINT fit's
    # ``post.theta[ORIGIN_ABILITY_ID]``: ``fit_rasch`` re-anchors ``mean(θ)==0`` per call, so
    # its scale is set by whichever arms were in the pool and the L4 law then differences two
    # estimators — a BIAS channel, which does not average out over a panel.
    # ``obs``, not ``origin_obs``: the deduped set carries the archive's origin rows too.
    origin_obs_all = [o for o in obs if o.candidate_id == ORIGIN_ABILITY_ID]
    return ruler, fit_theta_given_delta(origin_obs_all, ruler).get(ORIGIN_ABILITY_ID), model


_FRONTIER_ABILITY_ID = "_frontier"


def _cumulative_theta(
    results: list[dict[str, Any]], delta_scale: dict[int, RulerEntry] | None
) -> tuple[float, float] | None:
    """The θ-space peer of the cumulative composite: one virtual candidate (the frontier) fit
    against the fixed δ, so rounds land on one scale once per-round subsets drift."""
    from promptpotter.application.intelligence.exploration import (
        Observation,
        fit_theta_given_delta,
        graded_response,
    )
    from promptpotter.shared.errors import is_error_result

    obs = [
        Observation(_FRONTIER_ABILITY_ID, int(sid), graded_response(r))
        for r in results
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    ]
    return fit_theta_given_delta(obs, delta_scale or {}).get(_FRONTIER_ABILITY_ID)


def _inherit_sibling_runtime_failures(opt_sp: OptSearchPoint, session: Session) -> None:
    """Pull RuntimeFailures from sibling forks of this cycle's root so L1 sees configs
    prior siblings already proved to fail (``_r_runtime_failures`` filters by pipeline match)."""
    from promptpotter.application.intelligence.sibling_wounds import (
        gather_sibling_runtime_failures,
    )
    from promptpotter.infrastructure.store.layout import root_cycle_id as _root_cycle_id

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
        # Surface, don't swallow: on failure L1 never sees configs sibling forks already proved
        # to fail, which is an optimization-quality regression rather than noise.
        logger.warning("sibling runtime_failures inheritance skipped", exc_info=True)
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
    """Current/best searchpoint trajectory. The origin's own scalars are NOT here —
    they are round 0's, read off ``Cycle.origin_round``."""

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite_fitness: float = 0.0
    current_results: list[dict[str, Any]] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite_fitness: float = 0.0
    best_round: int = 0
    best_sp: JobSearchPoint | None = None
    # Running-max ability θ of the cumulative frontier on the fixed ruler — the θ-space peer of
    # ``best_composite_fitness`` the L2/L3 stall ladder reads. None on a cold-started cycle, and
    # the ladder falls back to ``best_composite_fitness``.
    best_theta: float | None = None


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig

    # 0-indexed: ``rounds[0]`` IS the origin's measurement, built by ``start`` before the loop
    # opens, so it is never absent.
    rounds: list[RoundResult] = field(default_factory=list)
    tracking: CycleRoundState = field(default_factory=CycleRoundState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    axes: AxisIndex | None = None
    # Reusable field values that earned credible lift on a run with the SAME answer-space
    # signature, mined once at `start` (the walk is cross-campaign). The `guidance` catalogue
    # renders from it, silent when empty; never the static seed set.
    earned_blocks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    escalation: EscalationFSM = field(default_factory=EscalationFSM)
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    archive_observations: list[Observation] = field(default_factory=list)
    # Captured at ``start`` because ``opt_sp`` advances on ``adopt``. The δ fit renames the
    # archive candidate carrying it to ``ORIGIN_ABILITY_ID``, so the origin is ONE candidate
    # with one θ rather than one per round subset it was re-scored against.
    origin_sp_hash: str = ""
    # sample_id → difficulty, calibrated at start and LOCKED on the first warm fit, so every
    # cross-round θ readout lands on one scale. Empty = still cold, and the gates degenerate to
    # θ == logit-accuracy.
    delta_scale: dict[int, RulerEntry] | None = None
    # None while the ruler is cold — a flat ruler is neither 1PL nor 2PL, so naming one lies.
    calibration_model: CalibrationModel | None = None
    # Stashed by L2/L3 rebase emission; `runner.entry` resolves it post-finalize.
    rebase_request: RebaseRequest | None = None

    @classmethod
    def start(
        cls,
        resolved_origin: OptSearchPoint,
        origin_report: ScoredCandidate,
        *,
        schema: PipelineSchema,
        origin_results: list[dict[str, Any]] | None = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """``origin_report`` arrives ALREADY measured — nothing here recomputes its accuracy,
        composite or evaluator namespace."""
        origin_accuracy = origin_report.accuracy
        opt_sp = _build_initial_opt_sp(resolved_origin)
        # `session.pipeline_params` carries the dataset overlay; `schema.to_pipeline_params()`
        # is sparse and strips operator config.
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=session.pipeline_params or None,
            schema=schema,
        )
        _assert_overlay_preserved(sp, session.pipeline_params)
        _inherit_sibling_runtime_failures(opt_sp, session)
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )

        # ONE archive walk, both consumers: the ruler and the intelligence layer ask for the
        # same observations at the same moment.
        origin_sp_hash = sp.sp_hash(schema)
        archive_obs = build_archive_observations(
            session.store,
            dataset_name=session.dataset_name,
            origin_sp_hash=origin_sp_hash,
        )
        delta_scale, origin_theta, calibration_model = _calibrate_delta_ruler(
            origin_results,
            config.optimization.elimination_n_min,
            enable_2pl=config.optimization.enable_2pl_graduation,
            archive_obs=archive_obs,
        )
        from promptpotter.application.intelligence.earned_blocks import (
            answer_space_signature,
            earned_library_for,
        )

        # Silent when no block earned credible lift on a matching shape — the dispatch-first
        # "signal or silence" rule.
        earned_blocks = earned_library_for(
            session.store,
            answer_space_signature(r.get("ground_truth") for r in (origin_results or [])),
        )
        return cls(
            session=session,
            config=config,
            earned_blocks=earned_blocks,
            rounds=[
                _origin_round(
                    opt_sp,
                    sp,
                    report=origin_report,
                    results=list(origin_results or []),
                    theta=origin_theta,
                    calibration_model=calibration_model,
                )
            ],
            tracking=CycleRoundState(
                current_sp=sp,
                current_accuracy=origin_accuracy,
                current_composite_fitness=origin_report.composite_fitness,
                current_results=origin_results or [],
                best_accuracy=origin_accuracy,
                best_composite_fitness=origin_report.composite_fitness,
                best_sp=sp,
                best_theta=origin_theta[0] if origin_theta is not None else None,
            ),
            opt_sp=opt_sp,
            archive_observations=archive_obs,
            origin_sp_hash=origin_sp_hash,
            delta_scale=delta_scale,
            calibration_model=calibration_model,
        )

    @property
    def origin_round(self) -> RoundResult:
        return self.rounds[0]

    def restamp_origin_round(self, parent: RoundParent) -> None:
        """A whole round in, a whole round out, so a re-measure cannot leave one field reading from
        the run before the fix. θ is carried, not re-fit: the ruler is locked."""
        assert self.tracking.current_sp is not None
        prior = self.origin_round
        carried = (
            None
            if prior.cumulative_theta is None or prior.cumulative_theta_se is None
            else (prior.cumulative_theta, prior.cumulative_theta_se)
        )
        self.rounds[0] = _origin_round(
            self.opt_sp,
            self.tracking.current_sp,
            report=parent.report,
            results=list(parent.results),
            theta=carried,
            calibration_model=self.calibration_model,
        )

    def replay_priors(self, priors: list[RoundResult]) -> None:
        """RE-RUNNABLE: rounds at or after *priors*' first number are REPLACED, and the high-water
        scalars re-seed from round 0 — so a second replay reconstructs instead of accumulating."""
        if not priors:
            return
        schema = self.session.pipeline_schema
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
        # The winner's OWN resolved params, off the round file — without them resume reverts
        # every config axis L1 won back to the origin floor.
        tr.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=last_rr.pipeline_params, schema=schema
        )
        # A high-water mark over what each round MEASURED, walked from the origin floor forward
        # on each round's own scalars — never a score over `acc_cum`, whose rows come from
        # different configurations. That pool is rebuilt here only to reseed `current_results`
        # and `best_theta`.
        origin_rr = self.rounds[0]
        tr.best_composite_fitness = origin_rr.composite_fitness
        tr.best_accuracy = origin_rr.accuracy
        tr.best_round = origin_rr.round
        tr.best_theta = origin_rr.cumulative_theta
        tr.best_sp = self.opt_sp.model_copy(
            update={f: origin_rr.prompt_fields.get(f, "") for f in PROMPT_STRING_FIELDS}
        ).to_job_search_point(base_pipeline_params=origin_rr.pipeline_params, schema=schema)
        acc_cum: list[dict[str, Any]] = []
        for rr in self.rounds:
            acc_cum = merge_known_outcomes(acc_cum, list(rr.results))
            if rr.composite_fitness > tr.best_composite_fitness:
                tr.best_composite_fitness = rr.composite_fitness
                tr.best_accuracy = rr.accuracy
                tr.best_round = rr.round
                # From THIS round's prompts, not `self.opt_sp` (pinned to the last prior above),
                # or a resumed best≠last cycle pairs best params with last text.
                best_opt_sp = self.opt_sp.model_copy(
                    update={f: rr.prompt_fields.get(f, "") for f in PROMPT_STRING_FIELDS}
                )
                tr.best_sp = best_opt_sp.to_job_search_point(
                    base_pipeline_params=rr.pipeline_params, schema=schema
                )
            # Re-maxed here so a resumed cycle reconstructs exactly what a fresh
            # `absorb_round` held.
            cum = _cumulative_theta(acc_cum, self.delta_scale)
            if cum is not None and (tr.best_theta is None or cum[0] > tr.best_theta):
                tr.best_theta = cum[0]
        tr.current_results = acc_cum
        # Mirrors `absorb_round`: "current" is the last round's OWN measurement.
        tr.current_accuracy = last_rr.accuracy
        tr.current_composite_fitness = last_rr.composite_fitness

    def warm_ruler_if_cold(self) -> None:
        """The first fit clearing the warmth floor is adopted and never re-fit, so every warm round
        shares one ruler. Attempted BEFORE the round's election and again once it closed."""
        # Two attempt points: the ≥2-arm floor is satisfied the moment the round's own
        # candidates are banked, which is BEFORE `elect_round_winner` runs, and trying only
        # after `absorb_round` leaves the election that needs the ruler always on a cold one.
        # This relaxes the TIMING, never the rule — a one-arm pool still stays flat below.
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )

        if self.delta_scale:
            return
        delta_scale, origin_theta, calibration_model = _calibrate_delta_ruler(
            self.origin_round.results,
            self.config.optimization.elimination_n_min,
            enable_2pl=self.config.optimization.enable_2pl_graduation,
            archive_obs=build_archive_observations(
                self.session.store,
                dataset_name=self.session.dataset_name,
                origin_sp_hash=self.origin_sp_hash,
            ),
        )
        if delta_scale:  # warmed — lock the ruler + re-read every θ already taken on it
            self.delta_scale = delta_scale
            self.calibration_model = calibration_model
            # Round 0 carries θ twice — its own frontier and C0's row — and a warm fit must
            # move both, or the round file reports the origin at two abilities.
            o_theta, o_se = origin_theta if origin_theta is not None else (None, None)
            self.origin_round.cumulative_theta = o_theta
            self.origin_round.cumulative_theta_se = o_se
            self.origin_round.candidate_scores = [
                c.model_copy(update={"theta": o_theta, "theta_se": o_se})
                for c in self.origin_round.candidate_scores
            ]
            # …and every L1 round that already closed: a round that closed on a flat ruler had
            # its θ fit at δ≡0, a DIFFERENT scale, and unrestamped they sit side by side in
            # ``round_adopted_levels`` for the L4 law to average. The ROUND's frontier θ only —
            # ``l1_score`` stamps no candidate θ on a cold ruler, so none can contradict this.
            frontier: list[dict[str, Any]] = []
            for rr in self.rounds:
                frontier = merge_known_outcomes(frontier, list(rr.results))
                if rr.round > 0:
                    restamped = _cumulative_theta(frontier, delta_scale)
                    rr.cumulative_theta, rr.cumulative_theta_se = (
                        restamped if restamped is not None else (None, None)
                    )
                    rr.calibration_model = calibration_model

    def adopt(self, new_incumbent: OptSearchPoint, *, advanced: dict[str, Any]) -> None:
        """The ONE adoption seam for an L1 win and an L2/L3 transition alike: persistent memory
        carries from the outgoing incumbent, and only ``advanced`` comes from the new one."""
        self.opt_sp.copy_memory_to(new_incumbent)
        for surface, val in advanced.items():
            setattr(new_incumbent.memory, surface, val)
        self.opt_sp = new_incumbent

    def absorb_round(
        self,
        rr: RoundResult,
        round_num: int,
    ) -> RoundResult:
        """Sole sink for a finished L1 round; returns the round, stamped for ``save_round_file``."""
        schema = self.session.pipeline_schema
        tr = self.tracking

        existing_keys = {
            rf_dedup_key(rf.model_dump()) for rf in self.opt_sp.memory.wounds.runtime_failures
        }
        for cs in rr.candidate_scores:
            for rf in cs.runtime_failures:
                k = rf_dedup_key(rf.model_dump())
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.memory.wounds.runtime_failures.append(rf)

        self.rounds.append(rr)
        self.warm_ruler_if_cold()
        # The winner OSP carries its own lineage, so IDENTITY moves forward rather than just
        # the six prompt strings. A HELD round returns the incumbent itself, so the lineage ids
        # match, nothing is adopted and no node is minted.
        winner_opt_sp = rr.opt_sp
        if winner_opt_sp is not None and winner_opt_sp.lineage.id != self.opt_sp.lineage.id:
            self.adopt(winner_opt_sp, advanced={"task_context": winner_opt_sp.memory.task_context})
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_results = merge_known_outcomes(tr.current_results, list(rr.results))
        # "Current" is what the incumbent SCORED, never an accuracy over the mixed-provenance
        # pool above. On a held round `rr` already carries the incumbent's re-score for this
        # round's subset, so this stays a real measurement either way.
        tr.current_accuracy, tr.current_composite_fitness = rr.accuracy, rr.composite_fitness
        if tr.current_composite_fitness > tr.best_composite_fitness:
            tr.best_composite_fitness = tr.current_composite_fitness
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp
        cur = _cumulative_theta(tr.current_results, self.delta_scale)
        if cur is not None and (tr.best_theta is None or cur[0] > tr.best_theta):
            tr.best_theta = cur[0]

        rr.cumulative_theta, rr.cumulative_theta_se = cur if cur is not None else (None, None)
        rr.calibration_model = self.calibration_model
        rr.opt_sp = self.opt_sp
        return rr
