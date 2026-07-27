"""Cycle state — round-loop mutable orchestration object.

**Wrong-level guardrail:** new *optimizer* state flows through ``OptSearchPoint``
(``domain/opt_search_point.py``), never a sidecar field here. `Cycle` holds
round-loop orchestration — the escalation FSM, the axis index, this round's
bookkeeping — and is rebuilt per cycle. State a layer must carry ACROSS
generations (anything L1/L2/L3 reads or writes about the individual) belongs on
the searchpoint, which is what lineage, hashing, and resume all key on. A field
added here instead is invisible to `derive()` and silently lost on fork.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

# Leaf import (not the package surface): EscalationFSM is the foundational
# state type Cycle holds; importing it via escalation/__init__ would load the
# firing driver, which depends back on Cycle → import cycle. See escalation/__init__.
from promptpotter.application.optimization.escalation.state import EscalationFSM
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import rf_dedup_key
from promptpotter.domain.opt_search_point import OptSearchPoint, node_config_items
from promptpotter.domain.results import (
    CritiqueReadout,
    DegradationHealth,
    RoundParent,
    RoundResult,
)
from promptpotter.domain.run_records import RebaseRequest, ResumeCheckpointRecord
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.exploration import Observation, RulerEntry
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import CalibrationModel
    from promptpotter.domain.scoring import QueryMeasurement, RoundScorer
    from promptpotter.domain.search_point import TaskDecomposition

logger = logging.getLogger(__name__)

__all__ = ["Cycle", "CycleRoundState"]


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
    for node, cfg in node_config_items(session_pipeline_params):
        missing = set(cfg) - set(sp_pp.get(node, {}))
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "Cycle.start must pass session.pipeline_params, not a sparse schema view"
        )


def _calibrate_delta_ruler(
    session: Session,
    origin_results: list[dict[str, Any]] | None,
    n_min: int,
    *,
    enable_2pl: bool,
    archive_obs: list[Observation],
) -> tuple[dict[int, RulerEntry], float | None, CalibrationModel | None]:
    """Calibrate the per-cycle FIXED difficulty ruler + read the origin's θ on it.

    The cross-round comparability anchor (slice 2 of fitness-comparability). Every
    later θ readout in this cycle (round winners for ``c0_ok``, the stall ladder) is
    measured against this one fixed ruler via ``fit_theta_given_delta`` — so they compare
    on a shared scale instead of each round's own re-anchored fit. Calibrated at cycle
    start (and, on a cold start, re-attempted per round until it warms + LOCKS —
    ``Cycle._maybe_warm_ruler``) from the **grade-A** archive (the operator's pick: cleanest reference, same
    de-biasing the AxisIndex digest applies) plus the origin's own per-sample outcomes
    (so the origin's samples are always on the ruler). The origin rides under
    ``ORIGIN_ABILITY_ID``, so the calibration fit yields θ_C0 directly. Cold start —
    fewer than ``n_min`` calibrated samples (a fresh dataset's first cycle) — returns a
    **FLAT ruler** ``{}`` (δ≡0) and the origin's θ on it (logit-accuracy), so the gates
    still compare in θ (one ruler, θ always) rather than a separate accuracy floor.
    ``n_min`` is ``optimization.elimination_n_min`` — the single min-samples floor that
    also gates when PoBB acts on a candidate's θ; difficulty and ability become trustworthy
    at the same evidence threshold (no separate ruler-only constant).

    The model is chosen by ``graduate_ruler_model`` (slice 3): the bank uses 1PL until a
    data-rich, genuinely-discriminating dataset wins held-out cross-validation, then the
    ruler carries per-sample discrimination ``(δ, a)``. Returned as the third element so
    the operator reads the model actually fitted; a cold ruler is **neither** 1PL nor 2PL
    (it is flat), so it returns ``None`` rather than the name the graduation would have
    picked had the fit been adopted. Gated by ``enable_2pl``
    (``optimization.enable_2pl_graduation``). The switch is invisible above the
    seam — ``ruler()`` folds δ + a into the one mapping every θ consumer already reads.
    """
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
    # Both fits key δ on ``sorted({o.sample_id})``, so the adoption test below is a
    # statement about the DISTINCT-sample count — knowable without fitting. Below the
    # floor the fit is discarded unread (the cold branch reads only ``origin_obs``), so
    # skip it: a cold ruler re-attempts every round and an L4 inner cycle never clears
    # the floor at all, which had it paying a 1PL + 2PL + 5-fold-CV storm each round to
    # throw the result away.
    if len({o.sample_id for o in obs}) < n_min:
        origin_row = fit_theta_given_delta(origin_obs, {}).get(ORIGIN_ABILITY_ID)
        return {}, (origin_row[0] if origin_row is not None else None), None
    model, post = graduate_ruler_model(obs, enable=enable_2pl)
    if len(post.delta) >= n_min:
        if model == "2PL":
            logger.info("δ ruler graduated to 2PL (%d samples discrimination-fit)", len(post.delta))
        return post.ruler(), post.theta.get(ORIGIN_ABILITY_ID), model
    # Cold: too few banked samples to trust a fitted ruler → flat ruler; origin θ = logit-accuracy.
    origin_row = fit_theta_given_delta(origin_obs, {}).get(ORIGIN_ABILITY_ID)
    return {}, (origin_row[0] if origin_row is not None else None), None


_FRONTIER_ABILITY_ID = "_frontier"


def _cumulative_theta(
    results: list[dict[str, Any]], delta_scale: dict[int, RulerEntry] | None
) -> float | None:
    """Ability θ of the cumulative frontier ``results`` on the fixed ruler ``delta_scale``.

    The θ-space peer of the cumulative composite — what the stall ladder compares
    round-over-round. One virtual candidate (the frontier) fit against the fixed δ (flat
    where the ruler is cold), so successive rounds land on one scale even once per-round
    subsets drift. ``None`` only when no non-error result remains to fit."""
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
    fit = fit_theta_given_delta(obs, delta_scale or {})
    row = fit.get(_FRONTIER_ABILITY_ID)
    return row[0] if row is not None else None


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
    # The origin's per-evaluator breakdown (same namespace every L1 candidate carries) —
    # computed at ``start`` and kept so C0 shows the same evaluator columns as C1+ rather
    # than blank. ``{}`` for a degenerate empty origin.
    origin_evaluators: dict[str, float] = field(default_factory=dict)
    origin_per_sample_results: list[dict[str, Any]] = field(default_factory=list)
    # The frozen C0 origin's ability θ on the cycle's fixed δ ruler (slice 2) —
    # the θ-space peer of ``origin_accuracy``, the cross-round floor ``c0_ok``
    # compares each round's winner against. None when the ruler is cold-started
    # (thin grade-A archive); ``c0_ok`` then falls back to ``origin_accuracy``.
    origin_theta: float | None = None
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

    rounds: list[RoundResult] = field(default_factory=list)
    # Round-0 (origin) degradation verdict, stashed at origin close. ``cycle.rounds``
    # is the 1-indexed L1 trajectory and omits the origin, but the origin's verdict
    # still belongs to every L1 round's track record (``prior_clean`` / consecutive-
    # degraded counting). ``close_round`` folds this in for L1 rounds; on resume,
    # ``replay_priors`` repopulates it from the persisted round-0 file.
    origin_health: DegradationHealth | None = None
    # Critique generated over round 0 (origin) so round 1's L1 opens on the real
    # per-sample failure signal instead of flying blind (the loop runs critique at
    # round end, so without this seed round 1 has no prior critique). Same lifecycle
    # as ``origin_health``: stashed at origin close, lives outside the 1-indexed
    # ``cycle.rounds`` trajectory, rebuilt by ``replay_priors`` from the round-0 file.
    origin_critique: CritiqueReadout | None = None
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
        origin_accuracy: float,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema | None,
        origin_results: list[dict[str, Any]] | None = None,
        round_scorer: RoundScorer | None = None,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        """Construct a fresh Cycle from a scored origin."""
        if origin_results and schema is not None:
            origin_score = compute_composite_fitness(
                origin_results,  # type: ignore[arg-type]
                schema,
                round_scorer=round_scorer,
            )
            composite_fitness = origin_score["composite_fitness"]
            origin_evaluators = dict(origin_score["evaluators"])
        else:
            composite_fitness = origin_accuracy
            origin_evaluators = {}
        opt_sp = _build_initial_opt_sp(resolved_origin, task_context)
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
            session,
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
                origin_evaluators=origin_evaluators,
                origin_per_sample_results=list(origin_results or []),
                origin_theta=origin_theta,
                best_theta=origin_theta,
            ),
            opt_sp=opt_sp,
            archive_observations=archive_obs,
            origin_sp_hash=origin_sp_hash,
            delta_scale=delta_scale,
            calibration_model=calibration_model,
        )

    def _cumulative_scores(
        self,
        results: list[dict[str, Any]],
        schema: PipelineSchema | None,
        fallback: tuple[float, float],
    ) -> tuple[float, float]:
        """``(accuracy, composite_fitness)`` over the cumulative frontier — the one
        scoring call the L1 trajectory folds through (``replay_priors`` +
        ``absorb_round``). ``fallback`` (the round's own subset scalars) stands in
        when the cycle has no schema. ``opt_sp=None``: the cumulative pool mixes
        multiple searchpoints, so opt_sp-aware evaluators take their vacuous fallback
        (per the ``matched_origin_stats`` convention)."""
        if schema is None:
            return fallback
        scores = compute_composite_fitness(
            cast("list[QueryMeasurement]", results),
            schema,
            opt_sp=None,
            round_scorer=self.session.scoring.round_scorer,
        )
        return scores["accuracy"], scores["composite_fitness"]

    def replay_priors(self, priors: list[RoundResult]) -> None:
        """Reconstruct round-loop state from persisted prior rounds (in-place).

        ``EscalationFSM`` is NOT touched — caller rebuilds via ``from_ledger``.

        The origin (round 0) is the floor, not an L1 round: it seeds the cumulative
        base + the verdict track record but stays OUT of ``cycle.rounds`` (the
        1-indexed L1 trajectory), so a resumed cycle reconstructs exactly what a
        fresh ``Cycle.start`` + ``absorb_round`` holds. Resume loads round 0 in
        ``priors`` (origin is a persisted round file post-unification), so it's
        peeled off here rather than silently swelling the trajectory (which would
        drift ``best_round`` by one and double-count the origin).
        """
        if not priors:
            return
        schema = self.session.pipeline_schema
        tr = self.tracking
        origin_results: list[dict[str, Any]] = []
        l1_rounds: list[RoundResult] = []
        for rr in priors:
            if rr.round == 0:
                self.origin_health = rr.health
                self.origin_critique = rr.critique
                origin_results = list(rr.results)
            else:
                self.rounds.append(rr)
                l1_rounds.append(rr)
        if not l1_rounds:
            # Resumed right after origin — no L1 trajectory to replay; the origin
            # floor was already seeded by Cycle.start (its verdict captured above).
            return
        last_rr = l1_rounds[-1]
        if last_rr.opt_search_point is None or last_rr.pipeline_params is None:
            raise ValueError(
                f"round {last_rr.round} closed without an opt_search_point / pipeline_params — "
                "the round file cannot seed a resume."
            )
        # Deep copy: the cycle mutates its working OSP (wounds, memory), and the round
        # is a record of what ran, not a scratchpad.
        self.opt_sp = last_rr.opt_search_point.model_copy(deep=True)
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, last_rr.prompt_fields.get(f, ""))
        # The winner's OWN resolved params — not the origin's. Reading them off the round
        # is only possible because the round file records them; while it didn't, resume
        # reverted every config axis L1 had won back to the origin floor.
        tr.current_sp = self.opt_sp.to_job_search_point(
            base_pipeline_params=last_rr.pipeline_params, schema=schema
        )
        # best_round = cumulative-state high-water-mark over the L1 trajectory,
        # seeded from the origin floor (mirrors absorb_round). best_round is the
        # round NUMBER, not a positional index — origin sits outside cycle.rounds, so
        # a positional counter would drift one ahead of the real round.
        acc_cum: list[dict[str, Any]] = list(origin_results)
        for rr in self.rounds:
            acc_cum = _merge_into_cumulative(acc_cum, list(rr.results))
            cum_acc, cum_comp = self._cumulative_scores(
                acc_cum, schema, (rr.accuracy, rr.composite_fitness)
            )
            if cum_comp > tr.best_composite_fitness:
                tr.best_composite_fitness = cum_comp
                tr.best_accuracy = cum_acc
                tr.best_round = rr.round
                # Build best_sp from THIS round's prompts, not self.opt_sp (pinned to the last
                # prior above) — else a resumed best≠last cycle pairs best params with last text.
                best_osp = self.opt_sp.model_copy(
                    update={f: rr.prompt_fields.get(f, "") for f in PROMPT_STRING_FIELDS}
                )
                tr.best_sp = best_osp.to_job_search_point(
                    base_pipeline_params=rr.pipeline_params, schema=schema
                )
            # best_theta is a running max over the same cumulative frontier as
            # best_composite — re-maxed here so a resumed cycle reconstructs exactly
            # what fresh absorb_round held (seeded from origin_theta by Cycle.start).
            cum_theta = _cumulative_theta(acc_cum, self.delta_scale)
            if cum_theta is not None and (tr.best_theta is None or cum_theta > tr.best_theta):
                tr.best_theta = cum_theta
        tr.current_results = acc_cum
        tr.current_accuracy, tr.current_composite_fitness = self._cumulative_scores(
            tr.current_results, schema, (last_rr.accuracy, last_rr.composite_fitness)
        )

    def _maybe_warm_ruler(self) -> None:
        """Warm the δ ruler from a cold start, then LOCK it. While ``delta_scale`` is
        still flat (a fresh dataset with no grade-A archive), re-attempt calibration
        from the round observations accumulated so far; the first fit that clears the
        ``elimination_n_min`` warmth floor is adopted and never re-fit — so warm rounds
        all compare on one shared ruler (comparability preserved), while the cold rounds
        that preceded it already compared on the frozen subset's raw accuracy. Repeat/
        reference-backed campaigns start warm at ``Cycle.start`` and skip this entirely;
        L4 inner cycles stay thin, never clear the floor, and stay frozen — all correct.

        Re-reading the archive is what re-warms it: this fires from ``absorb_round`` *after* the
        round closed, so every candidate it scored is already banked grade-A — under its own
        searchpoint identity. Feeding the in-cycle rounds in beside the archive read would only
        re-enter the same measurements under a second name.
        """
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )

        if self.delta_scale:
            return
        delta_scale, origin_theta, calibration_model = _calibrate_delta_ruler(
            self.session,
            self.tracking.origin_per_sample_results,
            self.config.optimization.elimination_n_min,
            enable_2pl=self.config.optimization.enable_2pl_graduation,
            archive_obs=build_archive_observations(
                self.session.store,
                dataset_name=self.session.dataset_name,
                origin_sp_hash=self.origin_sp_hash,
            ),
        )
        if delta_scale:  # warmed — lock the ruler + re-read origin θ on it
            self.delta_scale = delta_scale
            self.calibration_model = calibration_model
            self.tracking.origin_theta = origin_theta

    def adopt(self, new_incumbent: OptSearchPoint, *, advanced: dict[str, Any]) -> None:
        """Make ``new_incumbent`` the cycle's searchpoint — the ONE adoption seam for
        an L1 win and an L2/L3 transition alike. Identity advances: ``new_incumbent``
        already carries its own lineage (parent = the outgoing incumbent). The
        persistent L2/L3 memory (wounds, l1_layout, l1_overrides, task_context) carries
        forward from the outgoing incumbent via ``copy_memory_to``; only the surfaces in
        ``advanced`` are then taken from ``new_incumbent`` — the layer that owns them
        (L1 → task_context; L2 → task_context/l1_layout). The differentiated carry lives
        here, not in two hand-coded call sites."""
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
        self._maybe_warm_ruler()
        # Advance the incumbent to the elected winner. The winner OSP already carries
        # its own lineage (parent = this incumbent), so identity — not just the six
        # prompt strings — moves forward, and next round's candidates descend from the
        # winner. A HELD round returns the incumbent itself as the "winner" (origin.osp),
        # so the lineage ids match and nothing is adopted: the incumbent is unchanged and
        # mints no node. The winner's task_context (an L1 child can win on a framing
        # override, its 3rd mutation slot) rides along; wounds + l1_layout carry from the
        # outgoing incumbent — all through the single `adopt` seam.
        winner_osp = rr.opt_search_point
        if winner_osp is not None and winner_osp.lineage.id != self.opt_sp.lineage.id:
            self.adopt(winner_osp, advanced={"task_context": winner_osp.memory.task_context})
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        tr.current_results = _merge_into_cumulative(tr.current_results, list(rr.results))
        tr.current_accuracy, tr.current_composite_fitness = self._cumulative_scores(
            tr.current_results, schema, (rr.accuracy, rr.composite_fitness)
        )
        if tr.current_composite_fitness > tr.best_composite_fitness:
            tr.best_composite_fitness = tr.current_composite_fitness
            tr.best_accuracy = tr.current_accuracy
            tr.best_round = round_num
            tr.best_sp = tr.current_sp
        cur_theta = _cumulative_theta(tr.current_results, self.delta_scale)
        if cur_theta is not None and (tr.best_theta is None or cur_theta > tr.best_theta):
            tr.best_theta = cur_theta

        rr.cumulative_accuracy = tr.current_accuracy
        rr.cumulative_theta = cur_theta
        rr.calibration_model = self.calibration_model
        rr.opt_search_point = self.opt_sp
        return rr

    def parent_for_round(self, round_num: int) -> RoundParent:
        """Build the round's parent — the current best, carried as-is."""
        tr = self.tracking
        return RoundParent(
            accuracy=tr.current_accuracy,
            composite_fitness=tr.current_composite_fitness,
            osp=self.opt_sp,
            results=list(tr.current_results),
            label=f"round_{round_num}" if round_num > 0 else "origin",
        )
