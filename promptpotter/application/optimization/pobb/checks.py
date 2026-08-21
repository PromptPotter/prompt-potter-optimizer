"""Mid-round elimination — DegradationCheck (fatal/rate) + PoBBCheck (Russo 2016 stop rule)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import graded_response
from promptpotter.application.optimization.pobb.classification import (
    extract_warning_types,
    is_deprecated,
)
from promptpotter.application.scoring.selection import elimination_p_best
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.rendering import classify_result
from promptpotter.domain.results import EliminationGate
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint

    # The prior's id rides along so the backfill can stamp WHOSE catch-up this is. Without
    # it the pass inherited the foreground candidate's identity and recorded the prior's
    # measurement under it.
    BackfillFn = Callable[[JobSearchPoint, list[Sample], str], Awaitable[list[QueryMeasurement]]]


def _eliminate(
    name: str, check_result: dict[str, Any], candidate_idx: int, n_total_candidates: int
) -> EscalationSignal:
    return EscalationSignal(
        check_name=name,
        target=EscalationTarget.ELIMINATE_CANDIDATE,
        check_result=check_result,
        candidate_idx=candidate_idx,
        candidates_scored=candidate_idx + 1,
        candidates_skipped=n_total_candidates - candidate_idx - 1,
    )


def _leader_locked(
    name: str, check_result: dict[str, Any], candidate_idx: int, n_total_candidates: int
) -> EscalationSignal:
    return EscalationSignal(
        check_name=name,
        target=EscalationTarget.LEADER_LOCKED,
        check_result=check_result,
        candidate_idx=candidate_idx,
        candidates_scored=candidate_idx + 1,
        candidates_skipped=n_total_candidates - candidate_idx - 1,
    )


class DegradationCheck:
    """Fatal-classification fast-path (one sighting ends candidate) + rate-based check."""

    name = "degradation"

    def __init__(
        self, threshold: float = 0.4, min_samples: int = 3, *, fatal_fastpath: bool = True
    ) -> None:
        self.threshold = threshold
        self.min_samples = min_samples
        self.fatal_fastpath = fatal_fastpath

    def check(
        self, results: list[QueryMeasurement], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
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
                    candidate_idx,
                    n_total_candidates,
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
            candidate_idx,
            n_total_candidates,
        )


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
        self._backfill_fn = backfill_fn

    def set_current(
        self,
        candidate_id: str,
        on_snapshot: Callable[[PoBBSnapshot], None] | None = None,
    ) -> None:
        self._current_id = candidate_id
        self._on_snapshot = on_snapshot

    def register_completed(
        self,
        results: list[QueryMeasurement],
        *,
        candidate_id: str,
        sp: JobSearchPoint,
    ) -> None:
        """Add a completed candidate's per-sample grades to the priors pool; ``sp`` is retained so an unseen
        (prior, sample) pair can be backfilled later. Error/deprecated rows carry no outcome for the θ fit."""
        grades_by_sample: dict[str, float] = {}
        for r in results:
            sid = r.get("sample_id")
            if sid is None or is_error_result(r):
                continue
            grades_by_sample[str(sid)] = graded_response(r)
        self.priors_by_sample[candidate_id] = grades_by_sample
        self.prior_sps[candidate_id] = sp
        if candidate_id not in self.prior_ids:
            self.prior_ids.append(candidate_id)

    async def backfill_for_sample(self, sample: Sample) -> list[str]:
        """Catch each prior up on ``sample``, scoring on a miss; idempotent. With no ``backfill_fn`` this
        no-ops and paired ``check()`` skips the incomplete prior — surfacing the gap, never substituting 0."""
        if not self._backfill_fn:
            return []
        key = str(sample.id)
        fresh: list[str] = []
        for cid in self.prior_ids:
            existing = self.priors_by_sample[cid]
            if key in existing:
                continue
            new_results = await self._backfill_fn(self.prior_sps[cid], [sample], cid)
            for r in new_results:
                sid_new = r.get("sample_id")
                if sid_new is None or is_error_result(r):
                    continue
                existing[str(sid_new)] = graded_response(r)
            if key in existing:
                fresh.append(cid)
        return fresh

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
        """The ε bar at depth *n*: ``epsilon_floor`` at exactly ``n_min``, ramping linearly to
        ``epsilon`` by ``2 * n_min``, and flat wherever the floor is not below ``epsilon`` — the
        default, so an untouched config eliminates exactly as it did. At ``n_min`` one discordant
        sample already puts ``p_best`` near 0.2, so a single scalar ε is either too eager there or
        too permissive deep. The reprieve is cheap: an arm that stays behind is cut a few samples
        later rather than at full budget."""
        if self.epsilon <= self.epsilon_floor or n >= 2 * self.n_min:
            return self.epsilon
        return self.epsilon_floor + (self.epsilon - self.epsilon_floor) * (
            (n - self.n_min) / max(self.n_min, 1)
        )

    def check(
        self, results: list[QueryMeasurement], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
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
                candidate_idx,
                n_total_candidates,
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
            pid: {"p_better": float(p_better[pid]), "n_paired": float(n)} for pid in paired_priors
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
            return _leader_locked(
                self.name,
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
                candidate_idx,
                n_total_candidates,
            )

        # ε is the ONLY futility gate, and it now tests the SAME bar adoption does:
        # ``elimination_p_best`` compares strictly better-than-prior (no margin) and crowning
        # needs a strictly positive θ lift over the parent. The prior set includes the incumbent
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
            candidate_idx,
            n_total_candidates,
        )


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
