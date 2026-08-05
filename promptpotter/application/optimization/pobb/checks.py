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
from promptpotter.application.scoring.metrics import elimination_p_best
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.rendering import classify_result
from promptpotter.domain.scoring import is_answer_collapsed
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.exploration import RulerEntry
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
    """Stop measuring — posterior clears ``lock_in`` against every prior."""
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
    """One candidate's mid-round PoBB standing after ``n_samples`` cells.

    ``p_best`` is a SCALAR and the whole snapshot is about ``current_id`` alone. It was a
    ``dict`` merging two different quantities under one name — ``[current_id]`` meant this
    candidate's own P(best) while ``[prior_id]`` meant *P(current beats that prior)*, a fact
    about ``current_id`` filed under someone else's key. Every round-wide consumer read the
    whole dict as "each candidate's standing": the dashboard's top-5 leaderboard was built
    from ONE candidate's snapshot (so it listed that candidate plus the priors it was measured
    against, and the last candidate to score overwrote it), ``leader_prob`` maxed over the
    mixture, and the p_best stream wrote a trajectory per prior that ``log.md`` then rendered
    as a candidate line. A round winner holding P(best) 0.755 printed at 22.9%.

    The per-prior numbers are not lost — they are in :attr:`paired_breakdown`, under a name
    that says what they are, which is where they already were. Anything round-wide aggregates
    across candidates; a snapshot cannot answer a question about a population of one.
    """

    p_best: float
    current_id: str
    n_samples: int
    paired_breakdown: dict[str, dict[str, float]]


@dataclass(frozen=True)
class PoBBConfig:
    """Bundled PoBB tuning knobs — passed through l1_score → score_population → PoBBCheck."""

    n_min: int = 6
    epsilon: float = POBB_DEFAULT_EPSILON
    lock_in: float = 0.95  # threshold only; leader_lock_in owns on/off
    lock_in_n_min: int = 8
    # Mechanism toggles (OptimizationConfig.mechanisms.elimination.*).
    epsilon_elimination: bool = True
    leader_lock_in: bool = False


class PoBBCheck:
    """Paired-sample PoBB stop rule. ``backfill_fn`` aligns leader's history to
    candidate's sample set so comparison is always on identical sample IDs.
    See ``docs/concepts/paired-sample-pobb.md``.
    """

    name = "elimination"

    def __init__(
        self,
        config: PoBBConfig,
        *,
        n_samples: int,
        delta_scale: dict[int, RulerEntry],
        backfill_fn: BackfillFn | None = None,
    ) -> None:
        # The cycle's FIXED δ ruler — the SAME scale the round-winner election reads, so
        # elimination θ and election θ agree (flat where the ruler is cold). Empty ⇒ flat.
        self.delta_scale = dict(delta_scale)
        self.n_min = config.n_min
        self.epsilon = config.epsilon
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
        """Bind the candidate-under-evaluation; reset per-candidate snapshot state."""
        self._current_id = candidate_id
        self._on_snapshot = on_snapshot

    def register_completed(
        self,
        results: list[QueryMeasurement],
        *,
        candidate_id: str,
        sp: JobSearchPoint,
    ) -> None:
        """Add a completed candidate's per-sample graded-response map to the priors pool.

        ``sp`` is retained so missing (prior, sample) pairs can be backfilled
        on demand when a future candidate touches samples this prior never saw.
        Error/deprecated samples are excluded — they carry no outcome for the
        θ fit, matching how the round-winner election builds its observations.
        """
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
        """Catch each prior up on ``sample``; score on miss. Returns priors that gained a measurement.

        Idempotent: priors already covering ``sample.id`` are skipped. When
        ``backfill_fn`` is None (e.g. unit tests), this is a no-op — paired
        ``check()`` will see incomplete priors and skip them, surfacing the
        gap rather than silently substituting 0. Fired per-sample by the
        query loop's ``on_sample_pre_check`` hook so paired comparison sees
        caught-up priors without an upfront full-dataset wall.
        """
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
        """Return the per-prior graded-response map over ``sample_ids``; for decision archival.

        Only sample IDs the prior actually covers are emitted (the caller
        is asking "what did we know at decision time?"); missing entries
        are omitted rather than substituted. The resume replayer re-fits θ
        from exactly these recorded grades, so it must store the same outcomes
        the live elimination read.
        """
        keys = [str(sid) for sid in sample_ids]
        out: dict[str, dict[str, float]] = {}
        for cid in self.prior_ids:
            prior_map = self.priors_by_sample.get(cid) or {}
            out[cid] = {sid: prior_map[sid] for sid in keys if sid in prior_map}
        return out

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
                    "queries_scored": n,
                    "total_samples": self.n_samples,
                    "answer_collapsed": True,
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
            candidate_grades, paired_priors, candidate_sample_ids, self.delta_scale
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

        # ε is the ONLY futility gate now, and it tests a slightly lower bar than adoption does.
        # ``elimination_p_best`` compares strictly better-than-prior (no margin), while crowning
        # needs the winner to clear the matched parent by ``improvement_threshold``. The prior
        # set includes the incumbent (``l1/score/loop.py`` registers it as ``R{n}_winner``), so
        # ε does ask "can this beat the parent" — it just cannot ask "by enough to be adopted",
        # which leaves a thin band of arms that keep buying panel they can never be crowned on.
        #
        # A paired-margin futility gate that tested exactly that bar existed and was live-
        # validated (2026-07-04: tie cut q17/20, losers q10/q13); it was dropped in ``2ee23d40``
        # alongside the crowning rework. Raising ε absorbs most of its job. If kills still land
        # late, try the BAR before the mechanism — a margin argument inside ``elimination_p_best``
        # is a parameter, not a subsystem. **If the optimizer cannot be made to work and late
        # kills are implicated, bringing that gate back is the considered fallback**; the full
        # implementation is recoverable from ``2ee23d40``.
        if not self.epsilon_elimination or p_best_current >= self.epsilon:
            return None

        return _eliminate(
            self.name,
            {
                "queries_scored": n,
                "total_samples": self.n_samples,
                "n_priors": len(paired_priors),
                "p_best": float(p_best_current),
                "epsilon": float(self.epsilon),
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
    delta_scale: dict[int, RulerEntry],
    backfill_fn: BackfillFn | None,
) -> PoBBCheck:
    """Build the round's leader-elimination check. Today: paired-sample PoBB.

    **Swap point for alternative elimination strategies.** The mid-round
    contract is the ``StopRule`` Protocol (``domain/validators.py``), but
    PoBBCheck also exposes the per-candidate lifecycle the round loop drives
    (``register_completed``, ``set_current``,
    ``backfill_for_sample``, ``snapshot_priors``, ``priors_by_sample``).
    A fundamentally different strategy may not match that lifecycle shape;
    when one ships, this builder branches on config and the round loop gains
    the per-strategy consumer split. Today there's one strategy so the body
    + return type are direct.
    """
    return PoBBCheck(
        config,
        n_samples=n_samples,
        delta_scale=delta_scale,
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
