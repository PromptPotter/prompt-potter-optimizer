"""Mid-round elimination — DegradationCheck (fatal/rate) + PoBBCheck (Russo 2016 stop rule)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.pobb.elimination.classification import (
    classify_result,
    extract_warning_types,
    is_deprecated,
)
from promptpotter.application.optimization.pobb.seeding import pobb_rng
from promptpotter.config.settings import POBB_DEFAULT_EPSILON
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.validators import StopRule
from promptpotter.shared.statistics import (
    paired_better_probabilities,
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
    """Per-sample PoBB snapshot. ``p_best[current_id] = min(per-prior values)``."""

    p_best: dict[str, float]
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
    # Mechanism toggles (OptimizationConfig.mechanisms.elimination.*). Defaults
    # preserve today's behavior: ε + dominance on, lock-in off.
    epsilon_elimination: bool = True
    deterministic_dominance: bool = True
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
        round_num: int = 0,
        backfill_fn: BackfillFn | None = None,
    ) -> None:
        self.n_min = config.n_min
        self.epsilon = config.epsilon
        self.lock_in = config.lock_in
        self.lock_in_n_min = config.lock_in_n_min
        self.epsilon_elimination = config.epsilon_elimination
        self.deterministic_dominance = config.deterministic_dominance
        self.leader_lock_in = config.leader_lock_in
        self.n_samples = n_samples
        self.round_num = int(round_num)
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

        # Deterministic dominance: abort if cand_max_final_hits < seed_total_hits.
        if self.deterministic_dominance:
            dominance_signal = self._dominance_check(scores, candidate_idx, n_total_candidates)
            if dominance_signal is not None:
                return dominance_signal

        # Exclude priors with sample-set gaps rather than 0-fill.
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
        # P(best) bounded above by min over priors of P(cand > prior_i).
        p_best_current = min(p_better.values())
        hardest_prior_id = min(p_better, key=lambda k: p_better[k])

        paired_breakdown: dict[str, dict[str, float]] = {}
        for pid, prior_scores in paired_priors.items():
            mean_d, se_d, n_paired = paired_diff_posterior(scores, prior_scores)
            paired_breakdown[pid] = {
                "mean_d": float(mean_d),
                "se_d": float(se_d),
                "n_paired": float(n_paired),
                "p_better": float(p_better[pid]),
            }

        snapshot_dict: dict[str, float] = {**p_better, cid: float(p_best_current)}
        snap = PoBBSnapshot(
            p_best=snapshot_dict,
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
                    "p_best_snapshot": snapshot_dict,
                    "leader_id": hardest_prior_id,
                    "paired_breakdown": paired_breakdown,
                },
                candidate_idx,
                n_total_candidates,
            )

        if not self.epsilon_elimination or not pobb_should_stop(p_best_current, self.epsilon):
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
        """Abort when ``cand_max_final_hits < seed_total_hits`` on the candidate's budget.

        Seed = first ``prior_ids`` entry (origin R1, prior winner R2+).
        Hits derived from ``fitness > 0.5``. Requires an explicit
        ``_sample_universe`` AND full seed coverage on it.
        """
        if not self.prior_ids:
            return None
        seed_id = self.prior_ids[0]
        seed_full = self.priors_by_sample.get(seed_id, {})
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
    round_num: int,
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
        round_num=round_num,
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
