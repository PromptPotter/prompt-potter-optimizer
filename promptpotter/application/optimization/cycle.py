"""**Wrong-level guardrail:** state a layer carries ACROSS generations belongs on
``OptSearchPoint``, never a field here — one added here is invisible to ``derive()``."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

# Leaf import (not the package surface): EscalationFSM is the foundational
# state type Cycle holds; importing it via escalation/__init__ would load the
# firing driver, which depends back on Cycle → import cycle. See escalation/__init__.
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
            "matched_origin_accuracy": report.accuracy,
            "matched_origin_composite": report.composite_fitness,
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
        matched_origin_accuracy=row.accuracy,
        matched_origin_composite=row.composite_fitness,
        prompt_fields=prompt_fields,
        pipeline_params=sp.pipeline_params,
        results=results,
        all_candidate_results={opt_sp.lineage.id: results},
        candidates_scored=1,
        candidate_scores=[row],
        deprecated=deprecated,
        # Round 0's frontier θ is the origin's θ — the subset-invariant peer of the round's
        # own `accuracy`, so the trend line starts on the θ scale too, and the one place θ
        # is read from.
        cumulative_theta=theta[0] if theta is not None else None,
        cumulative_theta_se=theta[1] if theta is not None else None,
        calibration_model=calibration_model,
        evaluators=dict(row.evaluators),
        opt_sp=opt_sp,
        # C0's measurement is optimizer-independent, but its critique is not — and this is
        # where a cycle's first stamp has to land, or a campaign paused before round 1 has
        # nothing on disk that names the optimizer it started under.
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
    """The per-cycle FIXED ruler every later θ readout is measured against — slice 2 of
    ``docs/specs/fitness-comparability.md``. Cold start returns a FLAT ruler and a ``None`` model."""
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
    # ``archive_obs`` already carries the origin under ``ORIGIN_ABILITY_ID`` (the caller passes
    # its sp_hash) whenever the archive has banked it. ``origin_obs`` is still the guaranteed,
    # freshest source — an L4 inner cycle that cache-replays its origin banked that run INSIDE
    # its own evidence epoch, so the archive read cannot see it — and it wins the cells both hold.
    obs = dedup_observations(archive_obs, origin_obs)
    if not obs:
        return {}, None, None
    # Two warmth conditions, both knowable without fitting — below either the ruler stays flat
    # and the fit would be discarded unread, so skip it entirely rather than pay a
    # 1PL + 2PL + 5-fold-CV storm per round to throw the result away.
    #
    # DISTINCT SAMPLES ≥ n_min: both fits key δ on ``sorted({o.sample_id})``.
    #
    # DISTINCT ARMS ≥ 2: δ is only identified against a second ability. One arm and the
    # likelihood has nothing to separate — the anchor pins θ, and δ becomes that arm's own
    # hit pattern shrunk to two values, one for what it passed and one for what it failed.
    # Adopting that as THE ruler makes every later θ in the cycle a restatement of whether
    # round 0 happened to get the sample right, on a scale where the origin sits at exactly
    # 0.000 by construction. It is what a fresh campaign always hands this function: 40 origin
    # rows from one candidate clears any sample floor on its own, so the L4 inner cycles were
    # locking a two-valued ruler at ``Cycle.start`` and then judging eight rounds of promotion
    # against it (`justlogic-d234`, 2026-07-27 — two rounds carrying +14.3pp at p<0.05 were
    # stamped `improved=False` on it, and both campaigns died of the lives that cost them).
    # One arm therefore stays FLAT and re-attempts next round, by which time the round's own
    # candidates are banked grade-A and the fit has arms to compare.
    ruler: dict[int, RulerEntry] = {}
    model: CalibrationModel | None = None
    if len({o.sample_id for o in obs}) >= n_min and len({o.candidate_id for o in obs}) >= 2:
        fitted, post = graduate_ruler_model(obs, enable=enable_2pl)
        # Cold below the floor: too few banked samples to trust a fitted ruler → stay flat.
        if len(post.delta) >= n_min:
            ruler, model = post.ruler(), fitted
            if model == "2PL":
                logger.info("δ ruler graduated to 2PL (%d samples fit)", len(post.delta))
    # θ_C0 THROUGH THE SAME ESTIMATOR EVERY OTHER LEVEL USES — the whole point of this line.
    # Never hand back the JOINT fit's ``post.theta[ORIGIN_ABILITY_ID]``: ``fit_rasch``
    # re-anchors ``mean(θ)==0`` and re-estimates σ_θ per call, so its scale is set by whichever
    # arms were in the pool, and the L4 law then differences two abilities from two estimators
    # — a BIAS channel, which does not average out over a panel.
    # ``obs``, not ``origin_obs``: the deduped set carries every origin row the archive holds too.
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
        # Surface, don't swallow: on failure L1 never sees configs sibling forks
        # already proved to fail — a real optimization-quality regression, not noise.
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
    # Running-max ability θ of the cumulative frontier on the fixed ruler — the
    # θ-space peer of ``best_composite_fitness``, the comparator the L2/L3 stall
    # ladder reads (slice 2). Seeded from ``origin_theta``, re-maxed each round
    # in ``absorb_round``. None for a cold-started cycle → the ladder falls back
    # to ``best_composite_fitness``. Like c0_ok, a near-no-op while the scoring
    # set is fixed (per_round_resubset OFF) — θ and accuracy then move together.
    best_theta: float | None = None


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig

    # The whole trajectory, 0-indexed: ``rounds[0]`` IS the origin's measurement.
    # ``start`` builds it before the loop opens, so it is never absent.
    rounds: list[RoundResult] = field(default_factory=list)
    tracking: CycleRoundState = field(default_factory=CycleRoundState)
    opt_sp: OptSearchPoint = field(default_factory=OptSearchPoint)
    axes: AxisIndex | None = None
    # Earned prompt-block library for this cycle's task shape — `{field: (block, ...)}` of short
    # reusable field values that earned credible lift on a run with the SAME answer-space
    # signature. Mined once at `start` (the walk is cross-campaign); the `guidance` block
    # catalogue renders from it, silent when empty. Never the static seed set.
    earned_blocks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    escalation: EscalationFSM = field(default_factory=EscalationFSM)
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    archive_observations: list[Observation] = field(default_factory=list)
    # The origin's searchpoint identity (``prompt_fields_id`` = ``sp_hash``), captured at
    # ``start`` because ``opt_sp`` advances on ``adopt``. The δ fit renames the archive
    # candidate carrying it to ``ORIGIN_ABILITY_ID``, so the origin is ONE candidate with one θ
    # instead of one per round subset it was ever re-scored against.
    origin_sp_hash: str = ""
    # The cycle's δ ruler (sample_id → difficulty), from the grade-A archive + origin
    # (slice 2). Calibrated at start; a cold start (empty) re-warms from round data and
    # LOCKS on the first warm fit (``_maybe_warm_ruler``), then held constant — so every
    # cross-round θ readout (``c0_ok``, the stall ladder) lands on one scale via
    # ``fit_theta_given_delta``. Empty {} = still cold → gates degenerate to θ==logit-accuracy.
    delta_scale: dict[int, RulerEntry] | None = None
    # Which model the ruler above was fitted under, for the operator's readout. None while
    # the ruler is cold — a flat ruler is neither 1PL nor 2PL, so naming one would lie.
    calibration_model: CalibrationModel | None = None
    # Stashed by L2/L3 rebase emission; runner.entry resolves it post-finalize
    # into _mint_fork + observer rebuild + loop re-entry on the new fork.
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
        # Pass session.pipeline_params (carries dataset overlay) — schema.to_pipeline_params() is sparse and strips operator config.
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=session.pipeline_params or None,
            schema=schema,
        )
        _assert_overlay_preserved(sp, session.pipeline_params)
        _inherit_sibling_runtime_failures(opt_sp, session)
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )

        # ONE archive walk, both consumers. The ruler and the intelligence layer asked for
        # the same observations at the same moment and each loaded them — a full fold plus
        # a detail read per grade-A run, done twice.
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

        # Mine the earned block library for THIS task's answer-space shape, once — the
        # `guidance` catalogue renders it instead of the static seed set, silent when no block
        # earned credible lift on a matching shape (the dispatch-first "signal or silence" rule).
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
        # The seed round: the last L1 round, or round 0 when the list carries only the origin.
        # Never return early there on the grounds that ``start`` seeded tracking — true on a
        # FIRST call only. Re-seeding is what makes replaying rounds 0..k for ascending k
        # reconstruct each state instead of decorating the previous one.
        last_rr = self.rounds[-1]
        if last_rr.opt_sp is None or last_rr.pipeline_params is None:
            raise ValueError(
                f"round {last_rr.round} closed without an opt_sp / pipeline_params — "
                "the round file cannot seed a resume."
            )
        # Deep copy: the cycle mutates its working OSP (wounds, memory), and the round
        # is a record of what ran, not a scratchpad.
        self.opt_sp = last_rr.opt_sp.model_copy(deep=True)
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, last_rr.prompt_fields.get(f, ""))
        # The winner's OWN resolved params — not the origin's. Reading them off the round
        # is only possible because the round file records them; while it didn't, resume
        # reverted every config axis L1 had won back to the origin floor.
        tr.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=last_rr.pipeline_params, schema=schema
        )
        # best_round = high-water-mark over what each round actually MEASURED, walked from
        # round 0 (the origin floor) forward. It is the round NUMBER, which is now also its
        # index. Each round's own scalars — never a score over `acc_cum`, whose rows come
        # from different configurations (see `domain/results.py::merge_known_outcomes`); the pool is rebuilt
        # here only to reseed `current_results` and `best_theta` below.
        # Re-seed the mark from the origin floor first — see the re-runnable note above.
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
                # Build best_sp from THIS round's prompts, not self.opt_sp (pinned to the last
                # prior above) — else a resumed best≠last cycle pairs best params with last text.
                best_opt_sp = self.opt_sp.model_copy(
                    update={f: rr.prompt_fields.get(f, "") for f in PROMPT_STRING_FIELDS}
                )
                tr.best_sp = best_opt_sp.to_job_search_point(
                    base_pipeline_params=rr.pipeline_params, schema=schema
                )
            # best_theta is a running max over the same cumulative frontier as
            # best_composite — re-maxed here so a resumed cycle reconstructs exactly
            # what fresh absorb_round held (seeded from origin_theta by Cycle.start).
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
        # Two attempt points, because one was a round too late. The ≥2-arm floor is satisfied the
        # moment the round's own candidates are banked — `score_search_point` archives each as it
        # finishes, so they are grade-A and in the pool BEFORE `elect_round_winner` runs. Trying
        # only after `absorb_round` meant the election that needed the ruler always ran on a cold
        # one, and at L4's `max_rounds: 2` there was no later round to spend the warm ruler on:
        # per-candidate θ could never be stamped from the outer loop's own data at all.
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
            # Round 0 carries θ twice — its own frontier and C0's row — and a warm fit
            # must move both, or the round file reports the origin at two abilities.
            o_theta, o_se = origin_theta if origin_theta is not None else (None, None)
            self.origin_round.cumulative_theta = o_theta
            self.origin_round.cumulative_theta_se = o_se
            self.origin_round.candidate_scores = [
                c.model_copy(update={"theta": o_theta, "theta_se": o_se})
                for c in self.origin_round.candidate_scores
            ]
            # …and every L1 round that already closed, same argument one line up. A round that
            # closed on a flat ruler had its θ fit at δ≡0 — logit-accuracy on its own subset, a
            # DIFFERENT scale from every later round. Left unrestamped they sit side by side in
            # ``round_adopted_levels`` and the L4 law averages the mixture. This walk restamps
            # the ROUND's frontier θ only; ``l1_score`` stamps no candidate θ on a cold ruler,
            # so there is no cold value left to contradict the warm stamp.
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
        # Advance the incumbent to the elected winner. The winner OSP already carries
        # its own lineage (parent = this incumbent), so identity — not just the six
        # prompt strings — moves forward, and next round's candidates descend from the
        # winner. A HELD round returns the incumbent itself as the "winner" (origin.opt_sp),
        # so the lineage ids match and nothing is adopted: the incumbent is unchanged and
        # mints no node. The winner's task_context (an L1 child can win on a framing
        # override, its 3rd mutation slot) rides along; wounds + l1_layout carry from the
        # outgoing incumbent — all through the single `adopt` seam.
        winner_opt_sp = rr.opt_sp
        if winner_opt_sp is not None and winner_opt_sp.lineage.id != self.opt_sp.lineage.id:
            self.adopt(winner_opt_sp, advanced={"task_context": winner_opt_sp.memory.task_context})
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_results = merge_known_outcomes(tr.current_results, list(rr.results))
        # "Current" is what the incumbent SCORED — its own measurement on its own samples,
        # never an accuracy over the mixed-provenance pool above (see `domain/results.py::merge_known_outcomes`).
        # On a held round `rr` already carries the incumbent's re-score for this round's
        # subset (`rescore_parent`), so this stays a real measurement either way.
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
