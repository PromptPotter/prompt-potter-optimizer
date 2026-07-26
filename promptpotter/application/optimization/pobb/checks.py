"""Mid-round elimination — DegradationCheck (fatal/rate) + PoBBCheck (Russo 2016 stop rule)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import graded_response
from promptpotter.application.optimization.pobb.classification import (
    extract_warning_types,
    is_deprecated,
)
from promptpotter.application.scoring.metrics import binom_sf, elimination_p_best
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.rendering import classify_result
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.application.intelligence.exploration import RulerEntry
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint

    BackfillFn = Callable[[JobSearchPoint, list[Sample]], Awaitable[list[QueryMeasurement]]]


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
    """Per-sample PoBB snapshot. ``p_best[current_id] = min(per-prior values)``.

    ``margin`` carries the paired-margin gate's running stats (wins/losses/net/
    opportunities/p_clear …) so the stream shows a candidate's approach to the
    kill, not just the θ posterior. ``None`` when the gate's preconditions
    aren't met (no seed / no universe)."""

    p_best: dict[str, float]
    current_id: str
    n_samples: int
    paired_breakdown: dict[str, dict[str, float]]
    margin: dict[str, float] | None = None


@dataclass(frozen=True)
class PoBBConfig:
    """Bundled PoBB tuning knobs — passed through l1_score → score_population → PoBBCheck."""

    n_min: int = 6
    epsilon: float = POBB_DEFAULT_EPSILON
    lock_in: float = 0.95  # threshold only; leader_lock_in owns on/off
    lock_in_n_min: int = 8
    # Mechanism toggles (OptimizationConfig.mechanisms.elimination.*). Defaults
    # preserve today's behavior: ε + margin on, lock-in off.
    epsilon_elimination: bool = True
    leader_lock_in: bool = False
    margin_elimination: bool = True
    # The round's ADOPTION bar delta (OptimizationConfig.improvement_threshold): a
    # candidate must beat the seed by this to be crowned, so a candidate that
    # probably won't reach seed+this is futile. 0.0 ⇒ bar == seed (equivalence gate
    # reduces to "probably can't beat the seed").
    improvement_threshold: float = 0.0


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
        self.margin_elimination = config.margin_elimination
        self.improvement_threshold = config.improvement_threshold
        self.n_samples = n_samples
        # Per-prior per-sample GRADED response (fitness clamped to [0,1], via
        # ``graded_response``) — the θ ε-gate fits on it directly (bit-identical to the
        # old hit vector on binary datasets, discriminating on graded backends where
        # hit is degenerate). The counting gates derive binary as ``grade >= 1.0`` —
        # the same hit definition ``rescore`` applies — so they stay integer-exact, and
        # abstain once any grade is fractional (``_margin_stats``).
        self.priors_by_sample: dict[str, dict[str, float]] = {}
        self.prior_sps: dict[str, JobSearchPoint] = {}
        self.prior_ids: list[str] = []
        self._current_id: str = ""
        self._on_snapshot: Callable[[PoBBSnapshot], None] | None = None
        self._backfill_fn = backfill_fn
        # Candidate's sample budget; set per candidate via ``set_sample_universe``.
        self._sample_universe: frozenset[str] = frozenset()

    def set_current(
        self,
        candidate_id: str,
        on_snapshot: Callable[[PoBBSnapshot], None] | None = None,
    ) -> None:
        """Bind the candidate-under-evaluation; reset per-candidate snapshot state."""
        self._current_id = candidate_id
        self._on_snapshot = on_snapshot

    def set_sample_universe(self, sample_ids: Iterable[int | str] | None) -> None:
        """Bind the candidate's sample budget (unordered set of ids).

        Called by ``score_population`` once per candidate so the
        dominance check inside ``check()`` knows the candidate's
        intended budget (``len(_sample_universe)``) and which samples
        the seed prior must cover for the gate to fire. Passing
        ``None`` clears the universe — the dominance check then
        short-circuits to ``None`` (no abort), matching the
        no-explicit-budget unit-test path.
        """
        self._sample_universe = frozenset(str(sid) for sid in (sample_ids or []))

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
            new_results = await self._backfill_fn(self.prior_sps[cid], [sample])
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
        if not self.priors_by_sample:
            return None
        n = len(results)
        if n < self.n_min:
            return None

        # Exclude error/deprecated samples from the θ fit — a backend hiccup is not
        # evidence of inability, the same exclusion the round-winner election applies.
        fit_results = [r for r in results if not is_error_result(r)]
        if not fit_results:
            return None
        candidate_samples = [str(r.get("sample_id", "")) for r in fit_results]
        candidate_sample_ids = [int(r.get("sample_id", 0)) for r in fit_results]
        candidate_grades = [graded_response(r) for r in fit_results]

        # Paired-margin gate: wins/losses vs the SEED on the shared universe.
        # Order-agnostic (pure function of the outcome multiset + seed map), so
        # it stays honest under any scoring order — the fix for the easy-prefix
        # rate inflation that kept the old dominance/equivalence pair silent.
        attempted_ids = {str(r.get("sample_id")) for r in results if r.get("sample_id") is not None}
        margin_eval = self._margin_stats(attempted_ids, candidate_samples, candidate_grades)
        margin_stats = margin_eval[0] if margin_eval is not None else None
        if self.margin_elimination and margin_eval is not None:
            stats, seed_hit_ids, seed_miss_ids = margin_eval
            if stats["need"] > 0 and stats["p_clear"] < self.epsilon:
                return _eliminate(
                    self.name,
                    {
                        "queries_scored": n,
                        "total_samples": self.n_samples,
                        "n_priors": len(self.prior_ids),
                        "p_best": 0.0,
                        "epsilon": float(self.epsilon),
                        "p_best_snapshot": {},
                        "leader_id": self.prior_ids[0],
                        "gate": "margin",
                        "margin": {
                            **stats,
                            "seed_hit_ids": seed_hit_ids,
                            "seed_miss_ids": seed_miss_ids,
                            "universe_ids": sorted(self._sample_universe),
                        },
                    },
                    candidate_idx,
                    n_total_candidates,
                )

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

        snapshot_dict: dict[str, float] = {**p_better, cid: float(p_best_current)}
        snap = PoBBSnapshot(
            p_best=snapshot_dict,
            current_id=cid,
            n_samples=n,
            paired_breakdown=paired_breakdown,
            margin=margin_stats,
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
                    "p_best_snapshot": snapshot_dict,
                    "leader_id": hardest_prior_id,
                    "paired_breakdown": paired_breakdown,
                },
                candidate_idx,
                n_total_candidates,
            )

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
                "p_best_snapshot": snapshot_dict,
                "leader_id": hardest_prior_id,
                "paired_breakdown": paired_breakdown,
            },
            candidate_idx,
            n_total_candidates,
        )

    def _margin_stats(
        self,
        attempted_ids: set[str],
        valid_sample_ids: list[str],
        valid_grades: list[float],
    ) -> tuple[dict[str, float], list[str], list[str]] | None:
        """Running paired-margin stats vs the SEED (``prior_ids[0]`` — origin R1,
        prior winner R2+) on the shared ``_sample_universe``.

        The adoption question, asked on the pairing itself: a candidate is crowned
        only if it nets ≥ ``margin`` more hits than the seed, and net movement can
        only come from discordant pairs — ``wins`` (candidate HIT where the seed
        missed) minus ``losses`` (candidate MISS where the seed hit). Ties carry
        nothing, so a front-loaded block of seed-hit ties can no longer inflate
        the futility estimate: ``p_w`` is the Laplace-smoothed win rate on the
        MEASURED SEED-MISS STRATUM alone, extrapolated over the remaining win
        opportunities via ``binom_sf``. ``need > opportunities`` ⇒ ``binom_sf``
        is exactly 0 — the deterministic "cannot clear the bar" corner rides the
        same formula (no second code path). Losses enter through banked ``net``
        only (one-sided bound: future losses only hurt, so the gate never kills a
        candidate the full two-stratum convolution would keep).

        Universe samples the seed hasn't been backfilled on yet are counted as
        win opportunities (conservative upper bound — converges to exact as the
        per-sample backfill completes). Attempted-but-errored samples count in
        neither stratum and forfeit their opportunity, matching how errors score.

        Hits derive from grades as ``grade >= 1.0``, so this is an INTEGER gate and it
        abstains on a fractional grade. It does not self-weaken there, it self-CONDEMNS:
        no grade reaches 1.0, so ``wins`` is pinned at 0 and ``p_clear`` falls under ε for
        every candidate. The θ ε-gate fits the graded response directly and carries the
        load alone.

        Returns ``(stats, seed_hit_ids, seed_miss_ids)`` or ``None`` when preconditions
        (a seed, a universe, a binary grade scale) are unmet.
        """
        if not self.prior_ids:
            return None
        seed_id = self.prior_ids[0]
        seed_full = self.priors_by_sample.get(seed_id, {})
        universe = self._sample_universe
        if not universe:
            return None
        if any(0.0 < g < 1.0 for g in (*seed_full.values(), *valid_grades)):
            return None
        budget = len(universe)
        margin = math.ceil(self.improvement_threshold * budget)
        seed_hit_ids = sorted(sid for sid in universe if sid in seed_full and seed_full[sid] >= 1.0)
        seed_miss_ids = sorted(sid for sid in universe if sid in seed_full and seed_full[sid] < 1.0)
        seed_hit_set = set(seed_hit_ids)
        seed_miss_set = set(seed_miss_ids)

        wins = 0
        losses = 0
        measured_miss = 0
        measured_hit = 0
        for sid, grade in zip(valid_sample_ids, valid_grades, strict=True):
            if sid in seed_miss_set:
                measured_miss += 1
                if grade >= 1.0:
                    wins += 1
            elif sid in seed_hit_set:
                measured_hit += 1
                if grade < 1.0:
                    losses += 1
        net = wins - losses
        need = margin - net
        # Unattempted seed-misses + unattempted unclassified = remaining win chances.
        opportunities = sum(
            1 for sid in universe if sid not in attempted_ids and sid not in seed_hit_set
        )
        p_w = (wins + 1) / (measured_miss + 2)
        p_clear = 1.0 if need <= 0 else binom_sf(opportunities, need, p_w)
        stats: dict[str, float] = {
            "wins": wins,
            "losses": losses,
            "net": net,
            "margin": margin,
            "need": need,
            "opportunities": opportunities,
            "measured_miss": measured_miss,
            "measured_hit": measured_hit,
            "p_w": p_w,
            "p_clear": p_clear,
            "budget": budget,
            "seed_hits": len(seed_hit_ids),
            "deterministic": float(need > 0 and need > opportunities),
        }
        return stats, seed_hit_ids, seed_miss_ids


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
    (``register_completed``, ``set_current``, ``set_sample_universe``,
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
