"""Cycle state — round-loop mutable orchestration object.

Resume-checkpoint records, the divergence walker, and the fork-mint
helpers live in :mod:`promptpotter.application.optimization.resume_and_fork`
— this file only owns the in-memory ``Cycle`` dataclass + per-round
mutation. Escalation FSM (``EscalationState``, ``NextAction``,
``EscalationEvent``) lives in
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

from promptpotter.application.optimization.escalation.state import EscalationState
from promptpotter.application.optimization.pobb.elimination import extract_warning_types
from promptpotter.application.scoring.metrics import compute_composite_fitness
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.escalation_signals import RuntimeFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import RoundOrigin, RoundResult
from promptpotter.domain.run_records import ResumeCheckpointRecord
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
                "matched_origin_accuracy": c.get("matched_origin_accuracy", 0.0),
                "matched_origin_hits": c.get("matched_origin_hits", 0),
                "matched_origin_composite": c.get("matched_origin_composite"),
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


def _merge_into_cumulative(prior: list[dict], incoming: list[dict]) -> list[dict]:
    """Merge ``incoming`` per-sample measurements into ``prior``, keyed by sample_id.

    Winner overwrites for shared samples (the winner's measurement is the
    most-recent under the most-relevant searchpoint); prior is preserved
    for samples the winner didn't touch. Used by ``absorb_round`` and
    ``replay_priors`` so ``tr.current_results`` is a non-shrinking
    cumulative origin across rounds — fair to PoBB priors, best-tracking,
    probe-round filtering, and L2/L3 stall detection. Entries without a
    ``sample_id`` are dropped (the cumulative pool is sample-keyed).
    """
    by_sid: dict = {r.get("sample_id"): r for r in prior if r.get("sample_id") is not None}
    for r in incoming:
        sid = r.get("sample_id")
        if sid is not None:
            by_sid[sid] = r
    return list(by_sid.values())


@dataclass
class TrackingState:
    """Current/best searchpoint trajectory + frozen origin scores.

    ``origin_*`` fields snapshot the round-0 baseline and are never
    overwritten — ``current_*`` and ``best_*`` move with the round
    loop, but the operator-facing "origin=" banner must point back
    to the actual round-0 measurement even on resume.

    ``origin_per_sample_results`` snapshots the round-0 per-sample
    measurement dicts (the same shape ``current_results`` carries) and
    is never mutated after ``Cycle.start``. Feeds the dispatch hub's
    ``origin_strengths`` injection so L1 can see which samples the
    origin already converts and preserve the scaffolding that earns
    them — independent of how ``current_results`` evolves as later
    rounds fold in.
    """

    current_sp: JobSearchPoint | None = None
    current_accuracy: float = 0.0
    current_composite_fitness: float = 0.0
    current_results: list[dict] = field(default_factory=list)
    best_accuracy: float = 0.0
    best_composite_fitness: float = 0.0
    best_round: int = -1
    best_sp: JobSearchPoint | None = None
    origin_accuracy: float = 0.0
    origin_composite_fitness: float = 0.0
    origin_per_sample_results: list[dict] = field(default_factory=list)


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
    # Cumulative set of queries with at least one warning in any prior
    # round — drives probe-round subset selection. Rebuilt from results
    # in ``absorb_round``; not persisted to OSP.
    warned_queries: set[str] = field(default_factory=set)
    axes: AxisIndex | None = None
    escalation: EscalationState = field(default_factory=EscalationState)
    # Flushed into the next round_data's `decisions` before campaign_store.save_round_file.
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    state_version: int = 1
    # Round-end Rasch posterior; one fit per round, reused by finalize.
    last_rasch_posterior: Any = None
    # Cross-cycle observations harvested once at Cycle.start. Threaded into
    # the round-end workspace hard-samples JSON (always written for parity),
    # plus the per-candidate sorter / initial-scoring-set picker when the
    # respective `exploration.seed_*_from_archive` flags are on.
    archive_observations: list[Observation] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        origin_osp: OptSearchPoint,
        origin_accuracy: float,
        *,
        task_context: TaskDecomposition,
        schema: PipelineSchema | None,
        origin_results: list[dict] | None = None,
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
        opt_sp = origin_osp.model_copy(
            update={
                "task_context": task_context,
                "l1_overrides": dict(origin_osp.l1_overrides),
            }
        )
        # session.pipeline_params already carries the dataset overlay merged in by
        # configure_and_apply_pipeline (provider/model/temperature/reasoning_effort,
        # anything in nodes.{name}.config). Use it directly — schema.to_pipeline_params()
        # is intentionally sparse ({"steps": [...]}) and would strip the operator-fixed
        # config, causing the cycle's content_hash to ignore overlay edits and the wire
        # payload to fall back to backend defaults.
        sp = opt_sp.to_job_search_point(
            base_pipeline_params=session.pipeline_params or None,
            schema=schema,
        )
        # Invariant: every key the operator set under nodes.{name}.config in
        # the dataset overlay must survive into sp.pipeline_params, so that
        # (a) JobSearchPoint.content_hash flips on overlay edits → auto-fork,
        # and (b) the wire adapter forwards the overlay to the backend instead
        # of falling back to backend defaults. If this trips, a caller
        # regressed to schema.to_pipeline_params() (sparse) somewhere.
        _sp_pp = sp.pipeline_params or {}
        for _node, _cfg in (session.pipeline_params or {}).items():
            if _node == "steps" or not isinstance(_cfg, dict):
                continue
            _missing = set(_cfg) - set(_sp_pp.get(_node, {}))
            assert not _missing, (
                f"overlay keys stripped from {_node}: {sorted(_missing)} — "
                "Cycle.start must pass session.pipeline_params, not a sparse schema view"
            )
        # Always populated — the per-round display projection downstream
        # writes a workspace hard-samples artifact (cycle obs + archive obs)
        # regardless of any seed_* flag, so this list must be available.
        # Disk walk is cheap (same pattern the FastAPI dataset preview uses
        # on every request).
        from promptpotter.application.intelligence.hard_sample_archive import (
            build_archive_observations,
        )
        from promptpotter.application.intelligence.sibling_wounds import (
            gather_sibling_runtime_failures,
        )
        from promptpotter.infrastructure.store import root_cycle_id as _root_cycle_id

        archive_obs = build_archive_observations(
            session.store,
            session.backend_id,
            dataset_name=session.dataset_name,
        )

        # Inherit runtime_failures from sibling forks of this cycle's family
        # root. A fresh fork otherwise starts with an empty failure list and
        # L1 has no signal about configs that prior siblings already proved
        # to fail (e.g. max_tokens=1800 → empty_response). The wire-level
        # filter in `_r_runtime_failures` still drops entries that don't
        # match the new pipeline config, so inheriting them is safe.
        inherited_failures: list[RuntimeFailure] = []
        if session.state.cycle_id:
            try:
                root_id = _root_cycle_id(session.state.cycle_id)
                inherited_failures = gather_sibling_runtime_failures(
                    session.store,
                    root_id,
                    session.backend_id,
                    exclude_cycle_id=session.state.cycle_id,
                )
            except Exception:
                logger.debug("sibling runtime_failures inheritance skipped", exc_info=True)
        if inherited_failures:
            opt_sp.wounds.runtime_failures.extend(inherited_failures)
            logger.info(
                "inherited %d runtime_failures from sibling forks of %s",
                len(inherited_failures),
                session.state.cycle_id,
            )
        return cls(
            session=session,
            config=config,
            tracking=TrackingState(
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
            archive_observations=archive_obs,
        )

    def replay_priors(self, priors: list[dict[str, Any]]) -> None:
        """Reconstruct round-loop state from persisted prior rounds (in-place).

        Rebuilds ``rounds`` (typed :class:`RoundResult`), ``tracking``
        (current = last round, best = highest-composite across priors),
        ``opt_sp`` (last round's snapshot), and ``warned_queries``
        (accumulated across all priors). Callers feed the full ordered
        prior list — resume passes ``prior``; divergence-fork passes the
        ``survivors`` slice. Empty ``priors`` is a no-op (fresh cycle).

        ``EscalationState`` is NOT touched — it's a projection of the
        ledger and the caller rebuilds it via
        ``EscalationState.from_ledger`` after this returns.
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
        # Walk every prior round, accumulating per-sample measurements +
        # recomputing cumulative composite — mirrors the per-round folding
        # ``absorb_round`` does live. ``best_round`` is the cumulative-state
        # high-water-mark, not whichever round happened to land the hardest
        # leader-locked subset. Without this, resume/fork after a lock-in
        # would reseed PoBB priors with whatever subset the last winner saw.
        acc_cum: list[dict] = []
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

        # 1. opt_sp memory — runtime failures + cumulative warned-query set.
        all_results: list = [r for rs in rr.all_candidate_results.values() for r in rs]
        for r in all_results:
            if extract_warning_types(r) and (q := r.get("query")):
                self.warned_queries.add(q)
        existing_keys = {_rf_dedup_key(rf.to_dict()) for rf in self.opt_sp.wounds.runtime_failures}
        for cs in rr.candidate_scores:
            for rf_dict in cs.get("runtime_failures") or []:
                k = _rf_dedup_key(rf_dict)
                if k in existing_keys:
                    continue
                existing_keys.add(k)
                self.opt_sp.wounds.runtime_failures.append(RuntimeFailure(**rf_dict))

        # 2. round + tracking. Per-round trajectory lives on ``self.rounds``
        # (transient); persistent trajectory derives from ledger events.
        self.rounds.append(rr)
        for f in PROMPT_STRING_FIELDS:
            setattr(self.opt_sp, f, rr.prompt_fields.get(f, ""))
        assert tr.current_sp is not None
        _pp = (
            rr.pipeline_params if rr.pipeline_params is not None else tr.current_sp.pipeline_params
        )
        tr.current_sp = self.opt_sp.to_job_search_point(base_pipeline_params=_pp, schema=schema)
        # Cumulative origin: merge the winner's per-sample measurements onto
        # the prior pool, recompute aggregates on the union. Previous code did
        # ``tr.current_results = list(rr.results)`` which collapsed origin to
        # the leader-locked subset (q8/20) and shrank for every future round.
        # Cumulative pool stays fair to PoBB priors, best-tracking, probe-round
        # filtering, and L2/L3 stall detection — all of which read from here.
        tr.current_results = _merge_into_cumulative(tr.current_results, list(rr.results))
        if schema is not None:
            # opt_sp=None: cumulative mixes measurements taken under multiple
            # searchpoints, so opt_sp-aware evaluators (prompt_compactness +
            # wound rates) take their vacuous fallback — same convention as
            # ``matched_origin_stats``. Decouples cumulative composite from
            # any one candidate's lineage.
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

        # Stamp cumulative-pool size + accuracy on the round so disk readers
        # don't need to re-walk priors to render "Current best on N samples."
        rr.cumulative_total = len(tr.current_results)
        rr.cumulative_accuracy = tr.current_accuracy

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
            "candidates_scored": rr.candidates_scored,
            "candidate_scores": list(rr.candidate_scores),
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
        return RoundOrigin(
            accuracy=accuracy,
            composite_fitness=composite_fitness,
            osp=self.opt_sp,
            results=results,
            label=f"round_{round_num}" if round_num > 0 else "origin",
        )
