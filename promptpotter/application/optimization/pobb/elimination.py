"""Candidate elimination — classify_result, result helpers, mid-round checks.

classify_result(result) → three-bucket classification (advisory / infra / fatal):
  llm_only:content_empty + length + reasoning>0 → infra:reasoning_budget_exhausted
  llm_only:content_empty + length + reasoning=0 → infra:output_truncated
  llm_only:content_empty + stop                 → fatal:empty_response
  *:content_filtered                            → fatal (passthrough)

``infra_codes`` mark the sample deprecated for accounting + display, but
DegradationCheck's one-sighting fast-path (``dominant_fatal``) reads only
from ``fatal_codes`` — truncation alone does not eliminate a candidate at
n=1. Rate-based degradation still counts truncations toward elimination,
so chronically-truncating candidates are removed after ``min_samples`` and
``threshold`` are crossed.

PoBBCheck = Bayesian Posterior-of-Being-Best (Russo 2016): joint Normal-CLT
posterior, MC argmax, stop when current's P(best) < ε.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.pobb.seeding import pobb_rng
from promptpotter.application.scoring.metrics import count_degraded_samples
from promptpotter.domain.escalation_signals import EscalationSignal, EscalationTarget
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result
from promptpotter.shared.statistics import pobb_should_stop, posterior_best_probabilities

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint

    BackfillFn = Callable[[JobSearchPoint, list[Sample]], Awaitable[list[QueryMeasurement]]]

__all__ = [
    "DegradationCheck",
    "PoBBCheck",
    "PoBBConfig",
    "PoBBSnapshot",
    "ResultClassification",
    "build_degradation_checks",
    "classify_result",
    "extract_warning_types",
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultClassification:
    """Three-bucket result classification.

    ``advisory_codes`` — everything observed (warnings; informational).
    ``infra_codes`` — sample-killing but **not candidate-fault** (provider
        truncation, reasoning-budget exhaustion). Deprecates the sample for
        accounting/display; does **not** participate in one-sighting fast
        elimination, since the same sample tends to truncate regardless of
        which candidate prompt is in play.
    ``fatal_codes`` — deterministic-for-config candidate-quality failures
        (empty stop, content filtered, etc.). One sighting suffices to
        eliminate via ``dominant_fatal``.
    """

    advisory_codes: frozenset[str]
    infra_codes: frozenset[str]
    fatal_codes: frozenset[str]

    @property
    def is_fatal(self) -> bool:
        """True iff the sample should be treated as deprecated (fatal OR infra)."""
        return bool(self.fatal_codes or self.infra_codes)

    @property
    def all_codes(self) -> list[str]:
        return sorted(self.advisory_codes | self.infra_codes | self.fatal_codes)

    @property
    def dominant_fatal(self) -> str | None:
        """Pick a fatal code for one-sighting fast-elimination.

        Reads from ``fatal_codes`` only — infra-driven deprecation (e.g.
        ``llm_only:output_truncated``) must NOT trigger fast-path elimination.
        """
        return next(iter(sorted(self.fatal_codes)), None)


def _collect_advisories(result: Mapping[str, Any]) -> set[str]:
    pd = result.get("pipeline_data") or {}
    advisories: set[str] = set()
    for w in (pd.get("diagnostics") or {}).get("warnings") or []:
        if isinstance(w, dict):
            advisories.add(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
        elif isinstance(w, str):
            advisories.add(w)
    if not advisories and is_error_result(result):
        advisories.add(f"{pd.get('terminated_at', 'unknown')}:error")
    return advisories


def _llm_only_shape(result: Mapping[str, Any]) -> tuple[str | None, int]:
    """(finish_reason, reasoning_tokens) from step_tokens.llm_only; (None, 0) if missing."""
    pd = result.get("pipeline_data") or {}
    st = (pd.get("step_tokens") or {}).get("llm_only") or {}
    fr = st.get("finish_reason")
    reasoning = int(st.get("reasoning") or 0)
    return (fr, reasoning)


def classify_result(result: Mapping[str, Any]) -> ResultClassification:
    """Walk advisories + raw response shape; return advisory/infra/fatal codes.

    Truncation-shaped failures (``finish_reason=length``) route to
    ``infra_codes`` — the sample still counts as deprecated for display
    and rate-based elimination, but does not fast-path eliminate a
    candidate at n=1. Truncation is provider-ceiling-driven and tends to
    recur on the same sample independent of the prompt.

    Backend HTTP 4xx errors (``[CLIENT]``-tagged) are deterministic
    candidate-config failures — the caller sent a wire payload the
    upstream provider rejected (wrong type, unknown enum, missing
    required field). These route to ``fatal_codes`` so DegradationCheck's
    one-sighting fast-path eliminates the candidate immediately instead
    of retrying every remaining sample with the same poisonous config.
    """
    advisories = _collect_advisories(result)
    infra: set[str] = set()
    fatals: set[str] = set()

    if "llm_only:content_empty" in advisories:
        finish_reason, reasoning_tokens = _llm_only_shape(result)
        if finish_reason == "length" and reasoning_tokens > 0:
            infra.add("llm_only:reasoning_budget_exhausted")
        elif finish_reason == "length":
            infra.add("llm_only:output_truncated")
        else:
            fatals.add("llm_only:empty_response")

    for adv in advisories:
        if adv.endswith(":content_filtered"):
            fatals.add(adv)

    if is_error_result(result) and error_category(result.get("error")) == ErrorCategory.CLIENT:
        fatals.add("backend:client_error")

    return ResultClassification(
        advisory_codes=frozenset(advisories),
        infra_codes=frozenset(infra),
        fatal_codes=frozenset(fatals),
    )


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def ranked_item_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    """Derive pipeline_data keys carrying ranked items from schema's ranker/candidate_source nodes."""
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.node_type in ("ranker", "candidate_source"):
            keys.extend(node.output_keys)
    return keys


def get_ranked_items(r: Mapping[str, Any], ranked_item_keys: list[str] | None = None) -> list:
    """Extract ranked items from a result dict, checking keys in order."""
    pd = r.get("pipeline_data") or {}
    for key in ranked_item_keys or []:
        val = pd.get(key)
        if val:
            return val
    return []


def extract_warning_types(result: Mapping[str, Any]) -> list[str]:
    """Extract every advisory + fatal code seen on this result.

    Display and tracker callers want the full code list; classification is
    handled separately by :func:`classify_result`.
    """
    return classify_result(result).all_codes


def is_deprecated(result: Mapping[str, Any]) -> bool:
    """True iff the classifier flagged the sample as fatal or infra-truncated.

    Both buckets deprecate the sample for accounting purposes; only
    ``fatal_codes`` (not ``infra_codes``) participate in one-sighting
    fast-path elimination via ``ResultClassification.dominant_fatal``.
    """
    return classify_result(result).is_fatal


# ---------------------------------------------------------------------------
# Mid-round elimination checks
# ---------------------------------------------------------------------------


def _eliminate(
    name: str, check_result: dict, candidate_idx: int, n_total_candidates: int
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
    name: str, check_result: dict, candidate_idx: int, n_total_candidates: int
) -> EscalationSignal:
    """Lock in the current candidate as round leader; skip remaining candidates."""
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
    """Per-sample PoBB snapshot for telemetry."""

    p_best: dict[str, float]
    current_id: str
    n_samples: int


@dataclass(frozen=True)
class PoBBConfig:
    """Bundled PoBB tuning knobs — passed through l1_score → score_population → PoBBCheck."""

    n_min: int = 6
    epsilon: float = 0.05
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

    async def backfill_priors(
        self,
        target_sample_ids: list[int | str],
        samples_by_id: dict[str, Sample],
    ) -> dict[str, list[str]]:
        """Ensure every prior has fitness on each target sample; score on miss.

        Idempotent: priors that already cover ``target_sample_ids`` are
        skipped. When ``backfill_fn`` is None (e.g. unit tests), this is
        a no-op — paired ``check()`` will see incomplete priors and skip
        them, which surfaces the gap rather than silently substituting 0.

        Returns ``{prior_id: [newly_measured_sample_ids]}`` so the caller
        can surface backfill activity through the telemetry channel.
        Priors with zero new measurements are omitted from the return.
        """
        if not self._backfill_fn or not target_sample_ids:
            return {}
        targets = [str(sid) for sid in target_sample_ids]
        fresh: dict[str, list[str]] = {}
        for cid in self.prior_ids:
            existing = self.priors_by_sample[cid]
            missing_sids = [sid for sid in targets if sid not in existing]
            if not missing_sids:
                continue
            missing_samples = [samples_by_id[sid] for sid in missing_sids if sid in samples_by_id]
            if not missing_samples:
                continue
            new_results = await self._backfill_fn(self.prior_sps[cid], missing_samples)
            added: list[str] = []
            for r in new_results:
                sid_new = r.get("sample_id")
                if sid_new is None:
                    continue
                key = str(sid_new)
                existing[key] = float(r.get("fitness", 0.0))
                added.append(key)
            if added:
                fresh[cid] = added
        return fresh

    def snapshot_priors(self, sample_ids: list[int | str]) -> dict[str, dict[str, float]]:
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

        # Paired vectors — a prior is included only when it covers every
        # sample the candidate has measured. ``backfill_priors`` is supposed
        # to have closed any gaps before this fires; an incomplete prior
        # here means backfill was skipped (no backfill_fn) or failed, in
        # which case we exclude rather than silently 0-fill.
        paired_priors: dict[str, list[float]] = {}
        for cid in self.prior_ids:
            prior_map = self.priors_by_sample[cid]
            if all(sid in prior_map for sid in candidate_samples):
                paired_priors[cid] = [prior_map[sid] for sid in candidate_samples]
        if not paired_priors:
            return None

        cid = self._current_id or "__current__"
        histories: dict[str, list[float]] = {**paired_priors, cid: scores}
        snapshot = posterior_best_probabilities(
            histories,
            rng=pobb_rng(self.round_num, cid, list(paired_priors.keys()), n),
        )
        snap = PoBBSnapshot(p_best=snapshot, current_id=cid, n_samples=n)
        if self._on_snapshot is not None:
            self._on_snapshot(snap)

        p_best_current = snapshot.get(cid, 1.0)
        leader_id = max(snapshot.items(), key=lambda kv: kv[1])[0]

        # Leader lock-in (preempts loser elimination): when current is the
        # leader and its posterior P(best) ≥ lock_in threshold past the
        # lock-in floor, terminate the round early. Disabled when lock_in≥1.
        if (
            self.lock_in < 1.0
            and n >= self.lock_in_n_min
            and leader_id == cid
            and p_best_current >= self.lock_in
        ):
            return _leader_locked(
                self.name,
                {
                    "queries_scored": n,
                    "total_samples": self.n_samples,
                    "n_priors": len(paired_priors),
                    "p_best": float(p_best_current),
                    "lock_in": float(self.lock_in),
                    "lock_in_n_min": int(self.lock_in_n_min),
                    "p_best_snapshot": snapshot,
                    "leader_id": leader_id,
                },
                candidate_idx,
                n_total_candidates,
            )

        if not pobb_should_stop(p_best_current, self.epsilon):
            return None

        return _eliminate(
            self.name,
            {
                "queries_scored": n,
                "total_samples": self.n_samples,
                "n_priors": len(paired_priors),
                "p_best": float(p_best_current),
                "epsilon": float(self.epsilon),
                "p_best_snapshot": snapshot,
                "leader_id": leader_id,
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
