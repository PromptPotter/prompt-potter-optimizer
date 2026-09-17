"""Mid-round elimination — DegradationCheck (fatal/rate) + PoBBCheck (Russo 2016 stop rule)."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import graded_response
from promptpotter.application.optimization.pobb.classification import (
    extract_warning_types,
    is_deprecated,
)
from promptpotter.application.scoring.selection import (
    elimination_p_best,
    elimination_p_best_bounds,
)
from promptpotter.config.settings import NO_RESULT, POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.results import EliminationGate
from promptpotter.domain.results_health import classify_result
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import discordant_counts

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint

    # A catch-up is a call already started and the commit that takes it: the commit writes the
    # prior's row and hands it back, and runs only when a walk takes the cell it pairs. The prior's
    # id rides along so the call stamps WHOSE catch-up it is — inheriting the foreground candidate's
    # identity once recorded the prior's measurement under it.
    Backfill = tuple[asyncio.Future[Any], Callable[[], list[QueryMeasurement]]]
    BackfillFn = Callable[[JobSearchPoint, Sample, str], Backfill]


def _graded(rows: Iterable[QueryMeasurement]) -> dict[str, float]:
    """Each cell's grade; an error row carries no outcome for the θ fit."""
    return {
        str(sid): graded_response(r)
        for r in rows
        if (sid := r.get("sample_id")) is not None and not is_error_result(r)
    }


def _eliminate(name: str, check_result: dict[str, Any]) -> EscalationSignal:
    return EscalationSignal(name, EscalationTarget.ELIMINATE_CANDIDATE, check_result)


class DegradationCheck:
    """Fatal-classification fast-path (one sighting ends candidate) + rate-based check."""

    name = "degradation"

    def __init__(
        self, threshold: float = 0.4, min_samples: int = 3, *, fatal_fastpath: bool = True
    ) -> None:
        self.threshold = threshold
        self.min_samples = min_samples
        self.fatal_fastpath = fatal_fastpath

    def check(self, results: list[QueryMeasurement]) -> EscalationSignal | None:
        if self.fatal_fastpath and results:
            classification = classify_result(results[-1])
            fatal = classification.dominant_fatal
            if fatal is not None:
                n = len(results)
                return _eliminate(
                    self.name,
                    {
                        "degraded_rate": 1.0,
                        "degraded_count": n,
                        "total_scored": n,
                        "warning_types": dict.fromkeys(classification.fatal_codes, 1),
                        "dominant_warning": fatal,
                        "fatal": True,
                    },
                )

        n = len(results)
        if n < self.min_samples:
            return None
        # Count only genuinely-deprecated samples (fatal + infra/truncation) toward
        # elimination — NOT advisory transients. A non-fatal advisory warning (e.g.
        # web_search:low_document_count, which fires whenever fewer than max_sites docs
        # are gathered) must not eliminate a candidate that is otherwise scoring well;
        # classification.py is explicit that low-document-count is not a deprecation.
        degraded = sum(1 for r in results if is_deprecated(r))
        rate = degraded / n
        if rate < self.threshold:
            return None

        wtypes: Counter[str] = Counter()
        for r in results:
            wtypes.update(extract_warning_types(r))
        dominant = max(wtypes, key=wtypes.get) if wtypes else "unknown"  # type: ignore[arg-type]
        return _eliminate(
            self.name,
            {
                "degraded_rate": rate,
                "degraded_count": degraded,
                "total_scored": n,
                "warning_types": dict(wtypes),
                "dominant_warning": dominant,
            },
        )

    def earliest_stop(
        self,
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
    ) -> int | None:
        """The RATE alone — the fatal fast-path fires on one row's content."""
        degraded = sum(1 for r in results if is_deprecated(r))
        for m, (_, row) in enumerate(upcoming, start=len(results) + 1):
            degraded += row is None or is_deprecated(row)
            if m >= self.min_samples and degraded / m >= self.threshold:
                return m
        return None


@dataclass(frozen=True)
class PoBBSnapshot:
    """One candidate's mid-round PoBB standing. ``p_best`` is a SCALAR about ``current_id`` ALONE — a
    snapshot cannot answer a round-wide question; the per-prior numbers are in :attr:`paired_breakdown`."""

    p_best: float
    current_id: str
    n_samples: int
    paired_breakdown: dict[str, dict[str, float]]


@dataclass(frozen=True)
class PoBBConfig:
    n_min: int = 6
    epsilon: float = POBB_DEFAULT_EPSILON
    epsilon_floor: float = POBB_DEFAULT_EPSILON
    lock_in: float = 0.95  # threshold only; leader_lock_in owns on/off
    lock_in_n_min: int = 8
    # Mechanism toggles (OptimizationConfig.mechanisms.elimination.*).
    epsilon_elimination: bool = True
    leader_lock_in: bool = False


class PoBBCheck:
    """Paired-sample PoBB stop rule; ``backfill_fn`` aligns the leader's history onto the candidate's
    sample set so every comparison is on identical sample IDs. ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        config: PoBBConfig,
        *,
        n_samples: int,
        ruler: DeltaRuler | None,
        backfill_fn: BackfillFn | None = None,
    ) -> None:
        # The cycle's FIXED δ ruler — the SAME scale the round-winner election reads, so
        # elimination θ and election θ agree (``None`` ⇒ flat, where the ruler is still cold).
        self.ruler = ruler
        self.n_min = config.n_min
        self.epsilon = config.epsilon
        self.epsilon_floor = config.epsilon_floor
        self.lock_in = config.lock_in
        self.lock_in_n_min = config.lock_in_n_min
        self.epsilon_elimination = config.epsilon_elimination
        self.leader_lock_in = config.leader_lock_in
        self.n_samples = n_samples
        # Per-prior per-sample GRADED response (fitness clamped to [0,1], via
        # ``graded_response``) — the θ ε-gate fits on it directly (bit-identical to the
        # old hit vector on binary datasets, discriminating on graded backends where
        # hit is degenerate). The counting gates derive binary as ``grade >= 1.0`` —
        # the same hit definition ``rescore`` applies — so they stay integer-exact, and
        self.priors_by_sample: dict[str, dict[str, float]] = {}
        self.prior_sps: dict[str, JobSearchPoint] = {}
        self.prior_ids: list[str] = []
        self._current_id: str = ""
        self._on_snapshot: Callable[[PoBBSnapshot], None] | None = None
        self._on_backfill: Callable[[int, list[str]], None] | None = None
        self._backfill_fn = backfill_fn
        # One measurement per (prior, cell) however many walks reach the cell: started in a free
        # slot, committed when a walk takes the cell, and dropped unwritten if none ever does.
        self._pending: dict[tuple[str, int], Backfill] = {}

    def set_current(
        self,
        candidate_id: str,
        on_snapshot: Callable[[PoBBSnapshot], None] | None = None,
        on_backfill: Callable[[int, list[str]], None] | None = None,
    ) -> None:
        self._current_id = candidate_id
        self._on_snapshot = on_snapshot
        self._on_backfill = on_backfill

    def register_completed(
        self,
        results: list[QueryMeasurement],
        *,
        candidate_id: str,
        sp: JobSearchPoint,
    ) -> None:
        """Add a completed candidate's per-sample grades to the priors pool; ``sp`` is retained so an unseen
        (prior, sample) pair can be backfilled later."""
        self.priors_by_sample[candidate_id] = _graded(results)
        self.prior_sps[candidate_id] = sp
        if candidate_id not in self.prior_ids:
            self.prior_ids.append(candidate_id)

    def _unstarted(self, sample: Sample) -> list[str]:
        if not self._backfill_fn:
            return []
        return [
            cid
            for cid in self.prior_ids
            if str(sample.id) not in self.priors_by_sample[cid]
            and (cid, sample.id) not in self._pending
        ]

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        """Start measuring up to ``room`` of the priors that lack ``sample``, so the calls overlap
        the cell's own. Nothing is written or graded until :meth:`commit_backfills` takes them."""
        measure = self._backfill_fn
        if measure is None:
            return []
        started: list[asyncio.Future[Any]] = []
        for cid in self._unstarted(sample)[: max(room, 0)]:
            backfill = measure(self.prior_sps[cid], sample, cid)
            self._pending[(cid, sample.id)] = backfill
            started.append(backfill[0])
        return started

    def owed_backfills(self, sample: Sample) -> int:
        """Catch-up calls ``sample`` still needs that nothing has started. With no ``backfill_fn``
        there are none, and paired ``check()`` skips the incomplete prior — surfacing the gap,
        never substituting 0."""
        return len(self._unstarted(sample))

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return [call for call, _ in self._pending.values() if not call.done()]

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]:
        """The catch-up calls started for ``sample`` and not yet committed."""
        return [call for (_, sid), (call, _) in self._pending.items() if sid == sample.id]

    def commit_backfills(self, sample: Sample) -> None:
        """Take every started catch-up on ``sample``, in prior order — the moment a serial round
        would have measured them, so what reaches disk, and in what order, is the serial round's."""
        key = str(sample.id)
        fresh: list[str] = []
        for cid in self.prior_ids:
            backfill = self._pending.pop((cid, sample.id), None)
            if backfill is None:
                continue
            self.priors_by_sample[cid].update(_graded(backfill[1]()))
            if key in self.priors_by_sample[cid]:
                fresh.append(cid)
        if fresh and self._on_backfill is not None:
            self._on_backfill(sample.id, fresh)

    def bank_backfills(self, samples: Sequence[Sample]) -> None:
        """Write, ungraded, the catch-ups back for *samples* — cells a stopped round's walks were
        sure to take, so the resumed round replays these rather than paying for them again."""
        wanted = {s.id for s in samples}
        for key, (call, commit) in list(self._pending.items()):
            landed = call.done() and not call.cancelled() and call.exception() is None
            if key[1] in wanted and landed:
                del self._pending[key]
                commit()

    def discard_backfills(self) -> None:
        """Drop every measurement no walk took — paid, and never written, as a serial round would
        never have made it — save what :meth:`bank_backfills` kept."""
        for call, _ in self._pending.values():
            call.cancel()
            # Retrieved, so a discarded call's own failure is not reported as unhandled.
            call.add_done_callback(lambda done: done.cancelled() or done.exception())
        self._pending.clear()

    def snapshot_priors(self, sample_ids: Sequence[int | str]) -> dict[str, dict[str, float]]:
        """The per-prior grades over ``sample_ids``, for decision archival — uncovered IDs are omitted, not
        substituted. The resume replayer re-fits θ from exactly these, so they must be what live read."""
        keys = [str(sid) for sid in sample_ids]
        out: dict[str, dict[str, float]] = {}
        for cid in self.prior_ids:
            prior_map = self.priors_by_sample.get(cid) or {}
            out[cid] = {sid: prior_map[sid] for sid in keys if sid in prior_map}
        return out

    def epsilon_at(self, n: int) -> float:
        """The ε bar at depth *n*: ``epsilon_floor`` at both ends, ``epsilon`` in the middle,
        ramping linearly over ``n_min`` cells on each side — and flat wherever the floor is not
        below ``epsilon``, so a config that sets neither eliminates on one scalar.

        The bar tracks WHAT CUTTING STILL SAVES, which is the cells remaining. The ramp OUT lands
        on the floor exactly where the tail guard in :meth:`check` begins, so the two meet rather
        than cliff."""
        if self.epsilon <= self.epsilon_floor:
            return self.epsilon
        span = max(self.n_min, 1)
        ramp_in = (n - self.n_min) / span
        ramp_out = (self.n_samples - n - self.n_min) / span
        scale = min(1.0, max(0.0, min(ramp_in, ramp_out)))
        return self.epsilon_floor + (self.epsilon - self.epsilon_floor) * scale

    def check(self, results: list[QueryMeasurement]) -> EscalationSignal | None:
        n = len(results)
        if n < self.n_min:
            return None
        # A constant answerer is cut HERE, not at the election. ``is_answer_collapsed`` is the
        # absence of a measurement, not a low score, and the two are not interchangeable: an arm
        # answering one label to everything scores whatever share of the subset carries that
        # label — on a three-way task that can sit near 0.33, comfortably above the ε floor — so
        # the posterior never fires and the arm measures its full budget before ``l1_score``
        # drops it from ``electable`` anyway. Measured live 2026-07-28: an arm answering
        # "Uncertain" 12/12 against 6 TRUE / 6 FALSE spent twelve samples to establish something
        # the fourth had already shown. Asking the question at ``n_min`` (the same evidence floor
        # the posterior waits for — no second constant, and by then a genuine reasoner emitting
        # one label while truths vary is unlikely) turns it into what a human does: see a
        # candidate that has stopped answering the question, and move on.
        #
        # The collapse is still CHARGED, not hidden — the arm keeps its rows, so
        # the outer loop sees it structurally, via elimination rather than a graded charge.
        if is_answer_collapsed(results):
            return _eliminate(
                self.name,
                {
                    "gate": EliminationGate.COLLAPSED,
                    "queries_scored": n,
                    "total_samples": self.n_samples,
                },
            )
        if not self.priors_by_sample:
            return None

        # Exclude error/deprecated samples from the θ fit — a backend hiccup is not
        # evidence of inability, the same exclusion the round-winner election applies.
        fit_results = [r for r in results if not is_error_result(r)]
        if not fit_results:
            return None
        candidate_samples = [str(r.get("sample_id", "")) for r in fit_results]
        candidate_sample_ids = [int(r.get("sample_id", 0)) for r in fit_results]
        candidate_grades = [graded_response(r) for r in fit_results]

        # Exclude priors with sample-set gaps rather than substitute — the θ comparison
        # pairs each prior to the candidate on the candidate's exact samples.
        paired_priors: dict[str, list[float]] = {}
        for cid_p in self.prior_ids:
            prior_map = self.priors_by_sample[cid_p]
            if all(sid in prior_map for sid in candidate_samples):
                paired_priors[cid_p] = [prior_map[sid] for sid in candidate_samples]
        if not paired_priors:
            return None

        cid = self._current_id or "__current__"
        # P(best) = difficulty-adjusted θ ability, bounded above by min over priors of
        # P(θ_cand > θ_prior_i) — the same metric the round-winner election ranks by.
        p_best_current, p_better = elimination_p_best(
            candidate_grades, paired_priors, candidate_sample_ids, self.ruler
        )
        hardest_prior_id = min(p_better, key=lambda k: p_better[k])

        paired_breakdown: dict[str, dict[str, float]] = {
            pid: {
                "p_better": float(p_better[pid]),
                # `len(fit_results)`, NOT `n`: the pair is the width `p_better` was measured over,
                # and `p_better` is fit on `candidate_grades`, which excludes error/deprecated
                # rows. `n` is `queries_scored` and counts them — it rides the snapshot as
                # `n_samples`, one field down, which is where "how far did this candidate get"
                # belongs. Two denominators under one name made `n_paired - n_discordant` — the
                # concordant count the comment below invites a reader to take — wrong by the
                # errored rows on exactly the candidates least worth trusting.
                "n_paired": float(len(fit_results)),
                # The width `p_better` was actually entitled to, beside the width it was measured
                # over. Concordant cells cannot say which arm is better, so a round read on
                # `n_paired` alone cannot show why one cut was licensed and another was not.
                "n_discordant": float(sum(discordant_counts(candidate_grades, paired_priors[pid]))),
            }
            for pid in paired_priors
        }

        snap = PoBBSnapshot(
            p_best=float(p_best_current),
            current_id=cid,
            n_samples=n,
            paired_breakdown=paired_breakdown,
        )
        if self._on_snapshot is not None:
            self._on_snapshot(snap)

        # Leader lock-in: stop measuring when P(cand > every prior) ≥ lock_in.
        if self.leader_lock_in and n >= self.lock_in_n_min and p_best_current >= self.lock_in:
            return EscalationSignal(
                self.name,
                EscalationTarget.LEADER_LOCKED,
                {
                    "gate": EliminationGate.LOCK_IN,
                    "queries_scored": n,
                    "total_samples": self.n_samples,
                    "n_priors": len(paired_priors),
                    "p_best": float(p_best_current),
                    "lock_in": float(self.lock_in),
                    "lock_in_n_min": int(self.lock_in_n_min),
                    "leader_id": hardest_prior_id,
                    "paired_breakdown": paired_breakdown,
                },
            )

        # ε is the ONLY futility gate, and it now tests the SAME bar adoption does:
        # ``elimination_p_best`` compares strictly better-than-prior (no margin) and crowning
        # needs a strictly positive θ lift over the parent. The prior set includes the parent
        # (``l1/score/loop.py`` registers it as ``R{n}_winner``), so ε asks exactly "can this beat
        # the parent". The band of arms that survived ε yet could never be crowned closed with the
        # accuracy-recalibrated bar that opened it.
        #
        # A paired-margin futility gate that tested exactly that bar existed and was live-
        # validated (2026-07-04: tie cut q17/20, losers q10/q13); it was dropped in ``2ee23d40``
        # alongside the crowning rework. Raising ε absorbs most of its job. If kills still land
        # late, try the BAR before the mechanism — a margin argument inside ``elimination_p_best``
        # is a parameter, not a subsystem. **If the optimizer cannot be made to work and late
        # kills are implicated, bringing that gate back is the considered fallback**; the full
        # implementation is recoverable from ``2ee23d40``.
        # The TAIL guard, and it is `n_min` at the other end: an arm may not be JUDGED on fewer
        # than `n_min` cells, nor DISCARDED with fewer than `n_min` left — same knob, both ends.
        # Cutting in the tail saves almost nothing and costs the comparison: `matched_parent_stats`
        # needs EVERY cell the parent measured, so an arm stopped one cell short is unrankable
        # against the parent for the rest of the round.
        if self.n_samples - n < self.n_min:
            return None

        bar = self.epsilon_at(n)
        if not self.epsilon_elimination or p_best_current >= bar:
            return None

        return _eliminate(
            self.name,
            {
                "gate": EliminationGate.EPSILON,
                "queries_scored": n,
                "total_samples": self.n_samples,
                "n_priors": len(paired_priors),
                "p_best": float(p_best_current),
                "epsilon": float(bar),
                "leader_id": hardest_prior_id,
                "paired_breakdown": paired_breakdown,
            },
        )

    def earliest_stop(
        self,
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
        *,
        unresolved: Mapping[str, Iterable[QueryMeasurement]] | None = None,
    ) -> int | None:
        """Each gate of :meth:`check`, asked of every completion at once. Priors already short of a
        measured cell stay out, as they do there: the backfill that could cover it has run.
        ``unresolved`` are candidates ahead of this one still being walked, with the rows each has
        back so far. Each may yet become a prior or never become one, so it is graded where its
        rows say and anything anywhere else, and never counted on."""
        measured = [r for r in results if not is_error_result(r)]
        grades = {int(r.get("sample_id", 0)): graded_response(r) for r in measured}
        cells = list(grades)
        said = {str(r.get("predicted") or "") for r in measured}
        priors = {
            pid: {int(s): g for s, g in self.priors_by_sample[pid].items()}
            for pid in self.prior_ids
            if all(str(s) in self.priors_by_sample[pid] for s in cells)
        }
        pending = {
            pid: {int(s): g for s, g in _graded(rows).items()}
            for pid, rows in (unresolved or {}).items()
            if pid not in self.priors_by_sample
        }
        priors.update(pending)
        for m, (sample, row) in enumerate(upcoming, start=len(results) + 1):
            if row is None or not is_error_result(row):
                cells.append(sample.id)
            if row is not None and not is_error_result(row):
                grades[sample.id] = graded_response(row)
                said.add(str(row.get("predicted") or ""))
            if m < self.n_min:
                continue
            # Collapse needs one answer everywhere; a second, or an empty one, rules it out for good.
            if len(said) <= 1 and not said & {"", NO_RESULT}:
                return m
            if not priors:
                continue
            settled = [
                pid
                for pid, g in priors.items()
                if pid not in pending and all(s in g for s in cells)
            ]
            low, high = elimination_p_best_bounds(
                cells, grades, priors, self.ruler, settled=settled
            )
            if self.leader_lock_in and m >= self.lock_in_n_min and high >= self.lock_in:
                return m
            if (
                self.epsilon_elimination
                and self.n_samples - m >= self.n_min
                and low < self.epsilon_at(m)
            ):
                return m
        return None


def build_degradation_checks(config: CampaignConfig) -> list[StopRule]:
    """Per-sample checks (degradation). PoBBCheck is built by the runner."""
    opt = config.optimization
    checks: list[StopRule] = []
    if opt.degradation_threshold > 0:
        checks.append(
            DegradationCheck(
                threshold=opt.degradation_threshold,
                fatal_fastpath=opt.mechanisms.elimination.degradation_fatal_fastpath,
            )
        )
    return checks


def build_elimination_check(
    config: PoBBConfig,
    *,
    n_samples: int,
    ruler: DeltaRuler | None,
    backfill_fn: BackfillFn | None,
) -> PoBBCheck:
    """Build the round's leader-elimination check — the swap point for alternative strategies. The
    mid-round contract is the ``StopRule`` Protocol, but the round loop also drives PoBB's lifecycle."""
    return PoBBCheck(
        config,
        n_samples=n_samples,
        ruler=ruler,
        backfill_fn=backfill_fn,
    )


__all__ = [
    "DegradationCheck",
    "PoBBCheck",
    "PoBBConfig",
    "PoBBSnapshot",
    "build_degradation_checks",
    "build_elimination_check",
]
