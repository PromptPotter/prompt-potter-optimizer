from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.adaptive_queue_mechanism import build_round_order
from promptpotter.application.optimization.l1.score.candidate import (
    conclude_candidate,
    open_candidate,
)
from promptpotter.application.optimization.l1.score.signal_effect import CandidateOutcome
from promptpotter.application.optimization.pobb.checks import (
    PoBBCheck,
    PoBBConfig,
    build_elimination_check,
)
from promptpotter.application.optimization.resume_and_fork.decisions import ResumeCheckpointRecord
from promptpotter.application.scoring.query_loop import Walk, run_walks
from promptpotter.application.scoring.search_point_scorer import close_walk, open_walk
from promptpotter.domain.escalation_signals import EscalationSignal
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import CandidateProposal, ScoredCandidate
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.instrument import (
    NO_ROUND_SLOT,
    MeasuredCandidate,
    MeasurementRole,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.pobb.checks import Backfill
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.search_point import JobSearchPoint


async def score_population(
    cycle: Cycle,
    population: list[OptSearchPoint],
    effective_pipeline_params: list[dict[str, Any] | None],
    proposals: list[CandidateProposal],
    dataset: list[Sample],
    *,
    degradation_checks: list[StopRule] | None = None,
    callbacks: RunCallbacks,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    decisions: list[ResumeCheckpointRecord] | None = None,
    l1_diversity: float = 1.0,
) -> tuple[
    dict[str, list[QueryMeasurement]],
    list[ScoredCandidate],
    EscalationSignal | None,
]:
    n = len(population)

    all_candidate_results: dict[str, list[QueryMeasurement]] = {}
    candidate_scores: list[ScoredCandidate] = []
    escalation_signal: EscalationSignal | None = None

    def _pobb_backfill(sp: JobSearchPoint, sample: Sample, prior_id: str) -> Backfill:
        """Measure a PRIOR on one cell for paired fill-in — so it fires NO per-sample display
        callbacks, which would mint a bogus ``C{round}.0`` row, and runs no stop rule, which would
        recurse into PoBB. The call starts now; its row is written when the commit takes it."""
        walk = open_walk(
            sp,
            [sample],
            cycle.session,
            label="pobb_backfill",
            # A backfill catches a PRIOR up on a sample the current candidate reached; it
            # feeds the paired posterior, not that prior's own report. Its opt_sp-aware
            # evaluators would describe optimizer state from the round it was scored in.
            opt_sp=None,
            axes=cycle.axes,
            on_sample_scored=None,
            on_sample_starting=None,
            # The PRIOR being caught up — never the foreground candidate whose sample set
            # triggered this. Inheriting that binding is what filed C1.1's backfills under
            # C1.2. ``role`` is what tells a reader this row was measured for a paired
            # comparison, outside the round's shared order.
            measured=MeasuredCandidate(
                idx=NO_ROUND_SLOT,
                candidate_id=prior_id,
                label=f"prior:{prior_id[:8]}",
                role=MeasurementRole.BACKFILL,
            ),
        )
        walk.release()
        _, cell = walk.launch(1, None)

        def commit() -> list[QueryMeasurement]:
            walk.collect()
            walk.end(walk.take(cell) or walk.judge())
            return close_walk(walk).results

        return cell, commit

    elim_check = build_elimination_check(
        pobb_config,
        n_samples=len(dataset),
        ruler=cycle.ruler,
        backfill_fn=_pobb_backfill,
    )

    # Prime PoBB priors so candidate #1 has a comparator — without it, PoBB short-circuits on empty
    # priors and round-1 cand-1 was un-eliminable. `current_results` = best-so-far per-sample
    # history; `current_sp` is the leader, backfill-able on the candidate's hard samples. This is
    # the round's PARENT (`RoundParent` — the origin at round 0, the prior winner after it); the
    # repo spends `seed` on `CycleSeed`, the `seed-screen` verb and an L4 inner cell.
    parent_results = cycle.tracking.current_results
    parent_sp = cycle.tracking.current_sp
    parent_grades: dict[int, float] = {}
    if parent_results and parent_sp is not None:
        parent_id = f"R{cycle.rounds[-1].round}_winner"
        elim_check.register_completed(
            cast("list[QueryMeasurement]", parent_results), candidate_id=parent_id, sp=parent_sp
        )
        # CORRECTNESS, not the composite. `priors_by_sample` holds `graded_response` — the
        # `objective` θ is fit on — and `build_round_order` thresholds these with `is_hit`, which
        # `domain/scoring.py::CellScorer` declares a predicate on `fitness` and names the
        # difficulty stratification as one of its readers. Under any `per_cell` composite the two
        # differ, and where the composite scales below 1.0 (a latency or token penalty) EVERY cell
        # reads as a miss: the hit stratum empties and the parent-HIT regression probe never fires.
        parent_grades = {
            int(sid): float(r["fitness"])
            for r in parent_results
            if (sid := r.get("sample_id")) is not None and not is_error_result(r)
        }

    # ONE deterministic shared order per round — parent-MISS samples front-loaded, a parent-HIT
    # regression probe every 4th slot, cells the parent never answered ordered by discrimination —
    # so the ε-gate sees discriminating evidence immediately instead of a zero-information tie
    # prefix. Every candidate walks the same order: shared prefixes keep paired stats comparable
    # and the running display honest.
    order = build_round_order(parent_grades, cycle.ruler, [int(s.id) for s in dataset])
    samples_by_id = {int(s.id): s for s in dataset}
    dataset = [samples_by_id[sid] for sid in order]

    ids = [opt_sp_c.lineage.id for opt_sp_c in population]
    # Single merge site: build each candidate's frozen searchpoint once and share it with both
    # consumers — the in-flight dashboard seed (resolved config-only) and the candidate's walk and
    # report.
    sps = [
        opt_sp_c.to_job_search_point(
            base_pipeline_params=effective_pipeline_params[idx],
            schema=cycle.session.pipeline_schema,
        )
        for idx, opt_sp_c in enumerate(population)
    ]
    walks: list[Walk | None] = []
    for idx, opt_sp_c in enumerate(population):
        # The candidates before this one that are still being walked may yet become priors.
        ahead = _PriorsAhead(elim_check, list(zip(ids, walks, strict=False)))
        walks.append(
            open_candidate(
                idx=idx,
                opt_sp_c=opt_sp_c,
                candidate_sp=sps[idx],
                cycle=cycle,
                dataset=dataset,
                n_total=n,
                round_num=round_num,
                callbacks=callbacks,
                checks=[*(degradation_checks or []), ahead],
                l1_diversity=l1_diversity,
            )
        )

    def on_turn(idx: int) -> None:
        # Bind PoBBCheck so this candidate's per-sample snapshot rides the telemetry stream
        # tagged. Before the announcement, which quotes the prior count it holds.
        elim_check.set_current(
            ids[idx],
            on_snapshot=partial(callbacks.on_p_best_update, round_num, idx, n),
            on_backfill=partial(callbacks.on_pobb_backfill, round_num, idx, n),
        )
        # What this arm IS and which cells it will walk — one obligation, one call, shared
        # with the origin pass (`run_observers.py::announce_candidate`). The order is read off
        # the ledger by the console, over SSE by the chat's run card for "next in line", and
        # absorbed into the dashboard projection as `declared_sample_order`, which is what
        # lets a reader that missed the event still see forward.
        callbacks.announce_candidate(
            round_num,
            idx,
            n,
            opt_sp=population[idx],
            resolved_pipeline_params=sps[idx].config_params,
            sample_order=order,
            n_priors=len(elim_check.priors_by_sample),
            pipeline_overlay=proposals[idx].pipeline_overlay or None,
        )

    def on_decided(idx: int) -> bool:
        nonlocal escalation_signal
        opt_sp_c = population[idx]
        cr_result = conclude_candidate(
            idx=idx,
            opt_sp_c=opt_sp_c,
            candidate_sp=sps[idx],
            walk=walks[idx],
            pipeline_overlay=proposals[idx].pipeline_overlay or None,
            cycle=cycle,
            dataset=dataset,
            effective_pipeline_params=effective_pipeline_params[idx],
            elim_check=elim_check,
            decisions=decisions,
            candidate_scores=candidate_scores,
            round_num=round_num,
            l1_diversity=l1_diversity,
        )
        all_candidate_results[ids[idx]] = cr_result.results
        if cr_result.runtime_failure is not None:
            opt_sp_c.memory.wounds.runtime_failures = [
                *opt_sp_c.memory.wounds.runtime_failures,
                cr_result.runtime_failure,
            ]
        candidate_scores.append(cr_result.report)
        callbacks.on_candidate_scored(idx, n, cr_result.report.model_dump())

        if cr_result.outcome == CandidateOutcome.ESCALATED:
            escalation_signal = cr_result.escalation_signal
            return True  # true degradation — abort remaining candidates
        return False

    await run_walks(
        walks, cycle.session, backfills=elim_check, on_turn=on_turn, on_decided=on_decided
    )
    return all_candidate_results, candidate_scores, escalation_signal


@dataclass
class _PriorsAhead:
    """The round's PoBB check as one candidate's walk reads it: the candidates before it that are
    still being walked may yet become priors, so its horizon allows for them, as far as their
    returned cells say."""

    pobb: PoBBCheck
    ahead: list[tuple[str, Walk | None]]
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.pobb.name

    def check(self, results: list[QueryMeasurement]) -> EscalationSignal | None:
        return self.pobb.check(results)

    def earliest_stop(
        self,
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
    ) -> int | None:
        unresolved = {
            pid: walk.rows()
            for pid, walk in self.ahead
            if walk is not None and walk.outcome is None
        }
        return self.pobb.earliest_stop(results, upcoming, unresolved=unresolved)


__all__ = ["score_population"]
