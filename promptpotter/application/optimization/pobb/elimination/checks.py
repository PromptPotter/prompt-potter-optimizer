"""Mid-round elimination checks — DegradationCheck + PoBBCheck.

``DegradationCheck`` is the per-candidate technical-failure threshold:
fatal-classification fast-path (one sighting ends a candidate) plus a
rate-based check. ``PoBBCheck`` is the Bayesian Posterior-of-Being-Best
stop rule (Russo 2016): joint Normal-CLT posterior, MC argmax, stop when
the candidate's P(best) < ε — the actual abort-and-continue mechanism.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.pobb.elimination.classification import (
    classify_result,
    extract_warning_types,
)
from promptpotter.application.optimization.pobb.seeding import pobb_rng
from promptpotter.application.scoring.metrics import count_degraded_samples
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.validators import StopRule
from promptpotter.shared.statistics import (
    paired_better_probabilities,
    paired_diff_ci_overlaps_zero,
    paired_diff_posterior,
    pobb_should_stop,
)

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
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
    """Stop measuring this candidate — its posterior clears ``lock_in`` against
    every prior."""
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

    def __init__(self, threshold: float = 0.4, min_samples: int = 3) -> None:
        self.threshold = threshold
        self.min_samples = min_samples

    def check(
        self, results: list[QueryMeasurement], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
        if results:
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
        degraded = count_degraded_samples(results)
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
    """Per-sample PoBB snapshot for telemetry.

    ``p_best`` carries the per-prior ``P(cand > prior)`` map plus a
    ``current_id → min(values())`` summary entry — the operator reads
    the per-prior breakdown to see which prior is gating the abort.
    ``paired_breakdown`` adds the underlying ``(mean_d, se_d, n_paired)``
    per prior so the live stream shows the diff posterior the candidate
    is sitting on.
    """

    p_best: dict[str, float]
    current_id: str
    n_samples: int
    paired_breakdown: dict[str, dict[str, float]]


@dataclass(frozen=True)
class PoBBConfig:
    """Bundled PoBB tuning knobs — passed through l1_score → score_population → PoBBCheck."""

    n_min: int = 6
    epsilon: float = POBB_DEFAULT_EPSILON
    lock_in: float = 0.95
    lock_in_n_min: int = 8


class PoBBCheck:
    """Bayesian Posterior-of-Being-Best stop rule (Russo 2016) — paired-sample.

    Priors are stored sample-keyed (``priors_by_sample[cid][sample_id] →
    fitness``) so that comparison against any candidate is always on the
    same sample set. The hard-sample sorter intentionally violates iid
    by reordering samples per candidate (hardest first for cheap
    elimination of losers); paired comparison restores PoBB's statistical
    validity by re-deriving the leader's posterior on whatever sample set
    the candidate-under-test actually saw.

    When the candidate has measured samples the leader has not, the
    operator-injected ``backfill_fn`` scores the leader on those missing
    samples (cache-first via the per-sample archive; fresh LLM call on
    miss). The result is appended to the leader's sample-keyed history
    before ``check()`` runs, so every PoBB comparison is over identical
    sample IDs. See ``docs/concepts/paired-sample-pobb.md``.
    """

    name = "elimination"

    def __init__(
        self,
        config: PoBBConfig,
        *,
        n_samples: int,
        round_num: int = 0,
        backfill_fn: BackfillFn | None = None,
    ) -> None:
        self.n_min = config.n_min
        self.epsilon = config.epsilon
        self.lock_in = config.lock_in
        self.lock_in_n_min = config.lock_in_n_min
        self.n_samples = n_samples
        self.round_num = int(round_num)
        # Sample-keyed prior fitness — cid → sample_id (str) → fitness.
        self.priors_by_sample: dict[str, dict[str, float]] = {}
        # Prior SearchPoints, used to backfill missing (prior, sample) pairs.
        self.prior_sps: dict[str, JobSearchPoint] = {}
        self.prior_ids: list[str] = []
        self._current_id: str = ""
        self._on_snapshot: Callable[[PoBBSnapshot], None] | None = None
        self._backfill_fn = backfill_fn
        # The candidate's sample budget — the set of sample IDs the
        # candidate is scheduled to run on if PoBB does not stop it
        # early. Set per candidate via ``set_sample_universe``; the
        # online picker consumes the dataset as a set rather
        # than an ordered list, so order is no longer part of the
        # contract. ``check()``'s dominance gate reads ``len()`` for the
        # budget and ``in`` for seed coverage.
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
        """Add a completed candidate's per-sample fitness map to the priors pool.

        ``sp`` is retained so missing (prior, sample) pairs can be backfilled
        on demand when a future candidate touches samples this prior never saw.
        """
        scores_by_sample: dict[str, float] = {}
        for r in results:
            sid = r.get("sample_id")
            if sid is None:
                continue
            scores_by_sample[str(sid)] = float(r.get("fitness", 0.0))
        self.priors_by_sample[candidate_id] = scores_by_sample
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
                if sid_new is None:
                    continue
                existing[str(sid_new)] = float(r.get("fitness", 0.0))
            if key in existing:
                fresh.append(cid)
        return fresh

    def snapshot_priors(self, sample_ids: Sequence[int | str]) -> dict[str, dict[str, float]]:
        """Return the per-prior fitness map over ``sample_ids``; for decision archival.

        Only sample IDs the prior actually covers are emitted (the caller
        is asking "what did we know at decision time?"); missing entries
        are omitted rather than zero-filled.
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

        candidate_samples = [str(r.get("sample_id", "")) for r in results]
        scores = [float(r.get("fitness", 0.0)) for r in results]

        # Deterministic dominance check — no statistics, pure arithmetic.
        # If even hitting every remaining sample can't match the SEED
        # prior's already-known total hits across the candidate's intended
        # sample budget, abort immediately. Fires before the Bayesian
        # posterior because it's a stronger statement: "mathematically
        # cannot catch up" trumps "probably won't catch up."
        dominance_signal = self._dominance_check(scores, candidate_idx, n_total_candidates)
        if dominance_signal is not None:
            return dominance_signal

        # Paired vectors — a prior is included only when it covers every
        # sample the candidate has measured. ``backfill_for_sample`` runs
        # per-sample ahead of ``check()`` to close gaps as they appear; an
        # incomplete prior here means the backfill hook was wired off (no
        # backfill_fn) or its score failed — exclude rather than 0-fill.
        paired_priors: dict[str, list[float]] = {}
        for cid in self.prior_ids:
            prior_map = self.priors_by_sample[cid]
            if all(sid in prior_map for sid in candidate_samples):
                paired_priors[cid] = [prior_map[sid] for sid in candidate_samples]
        if not paired_priors:
            return None

        cid = self._current_id or "__current__"
        p_better = paired_better_probabilities(
            scores,
            paired_priors,
            rng=pobb_rng(self.round_num, cid, list(paired_priors.keys()), n),
        )
        # Candidate's P(best) is bounded above by min over priors of
        # P(cand > prior_i) — beating ALL priors cannot be more probable
        # than beating the single hardest one.
        p_best_current = min(p_better.values())
        hardest_prior_id = min(p_better, key=lambda k: p_better[k])

        # Per-prior paired-diff posterior for the audit + stream telemetry.
        # The operator triangulates "did the abort threshold fire because
        # the candidate genuinely lost, or because a single prior moved?"
        # by reading the per-prior (mean_d, se_d, n_paired) breakdown.
        paired_breakdown: dict[str, dict[str, float]] = {}
        for pid, prior_scores in paired_priors.items():
            mean_d, se_d, n_paired = paired_diff_posterior(scores, prior_scores)
            paired_breakdown[pid] = {
                "mean_d": float(mean_d),
                "se_d": float(se_d),
                "n_paired": float(n_paired),
                "p_better": float(p_better[pid]),
            }

        # PoBBSnapshot.p_best carries per-prior P(cand > prior) plus a
        # ``cid → p_best_current`` summary entry; the stream's delta math
        # tracks each prior independently across queries.
        snapshot_dict: dict[str, float] = {**p_better, cid: float(p_best_current)}
        snap = PoBBSnapshot(
            p_best=snapshot_dict,
            current_id=cid,
            n_samples=n,
            paired_breakdown=paired_breakdown,
        )
        if self._on_snapshot is not None:
            self._on_snapshot(snap)

        # Leader lock-in (preempts loser elimination): when the candidate
        # beats every prior with probability ≥ lock_in, stop measuring
        # this candidate. Paired ``p_best_current ≥ 0.5`` already implies
        # candidate is the leader against every prior, so the
        # ``leader_id == cid`` guard collapses into the threshold check.
        # Disabled when lock_in ≥ 1.0.
        if self.lock_in < 1.0 and n >= self.lock_in_n_min and p_best_current >= self.lock_in:
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

        if not pobb_should_stop(p_best_current, self.epsilon):
            return None

        # Separability floor — don't eliminate while the paired-difference
        # CI against the hardest prior still crosses zero. At the picker's
        # preferred samples (high expected information gain at the
        # candidate's running θ̂_c posterior), binomial noise alone can
        # push one-sided posterior tails past ε before the two-sided CI
        # separates. Paired analogue of ``wilson_overlap``; preserves the
        # AIME-binomial-noise protection from 2026-05-18 (memory:
        # ``project_pobb_separability_floor``).
        if paired_diff_ci_overlaps_zero(scores, paired_priors[hardest_prior_id], alpha=0.05):
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

    def _dominance_check(
        self,
        scores: list[float],
        candidate_idx: int,
        n_total_candidates: int,
    ) -> EscalationSignal | None:
        """Abort when the candidate provably cannot match the seed prior.

        Seed = origin (R1) or prior round's winner (R2+) — the first id
        in :attr:`prior_ids`, registered before any in-round candidate.

        Pure arithmetic over hits: ``cand_max_final_hits = cand_hits +
        remaining_in_budget``; if this is still less than the seed's
        known total hits across the same budget, no further scoring can
        flip the comparison. Fires regardless of the latest sample's
        hit/miss outcome because the dominance is structural, not
        evidential. Hits are derived from ``score > 0.5`` (matches the
        binary ``hit`` convention shared with post-round comparisons).

        Returns ``None`` (no abort) when:
        - no seed has been registered yet (round 1, candidate 1 before origin),
        - the seed isn't fully covered on the candidate's sample budget
          (backfill skipped or partial — can't compute the cap exactly),
        - or the candidate can still tie/exceed the seed.
        """
        if not self.prior_ids:
            return None
        seed_id = self.prior_ids[0]
        seed_full = self.priors_by_sample.get(seed_id, {})
        # Require an explicit sample universe — without it we can't
        # know the candidate's intended budget, and inferring from the
        # seed's coverage would compare on samples the candidate isn't
        # scored against. The score-population loop sets this per
        # candidate; unit tests that don't would silently skip the gate.
        sample_universe = self._sample_universe
        if not sample_universe:
            return None
        if not all(sid in seed_full for sid in sample_universe):
            return None
        seed_total_hits = sum(1 for sid in sample_universe if seed_full[sid] > 0.5)
        cand_hits = sum(1 for s in scores if s > 0.5)
        budget = len(sample_universe)
        remaining = max(0, budget - len(scores))
        cand_max_hits = cand_hits + remaining
        if cand_max_hits >= seed_total_hits:
            return None
        return _eliminate(
            self.name,
            {
                "queries_scored": len(scores),
                "total_samples": self.n_samples,
                "n_priors": len(self.prior_ids),
                "p_best": 0.0,
                "epsilon": float(self.epsilon),
                "p_best_snapshot": {},
                "leader_id": seed_id,
                "dominance": {
                    "cand_hits": cand_hits,
                    "cand_max_hits": cand_max_hits,
                    "seed_total_hits": seed_total_hits,
                    "budget": budget,
                    "remaining": remaining,
                },
            },
            candidate_idx,
            n_total_candidates,
        )


def build_degradation_checks(config: CampaignConfig) -> list[StopRule]:
    """Per-sample checks (degradation). PoBBCheck is built by the runner."""
    opt = config.optimization
    checks: list[StopRule] = []
    if opt.degradation_threshold > 0:
        checks.append(DegradationCheck(threshold=opt.degradation_threshold))
    return checks


__all__ = [
    "DegradationCheck",
    "PoBBCheck",
    "PoBBConfig",
    "PoBBSnapshot",
    "build_degradation_checks",
]
