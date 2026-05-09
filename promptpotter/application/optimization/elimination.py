"""Candidate elimination — classify_result, result helpers, mid-round checks.

classify_result(result) → fatal codes from advisory + finish_reason + reasoning:
  llm_only:content_empty + length + reasoning>0 → reasoning_budget_exhausted
  llm_only:content_empty + length + reasoning=0 → output_truncated
  llm_only:content_empty + stop                 → empty_response
  *:content_filtered                            → passthrough as fatal

PoBBCheck = Bayesian Posterior-of-Being-Best (Russo 2016): joint Normal-CLT
posterior, MC argmax, stop when current's P(best) < ε.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.metrics import count_degraded_samples
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.domain.validators import StopRule
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import pobb_should_stop, posterior_best_probabilities

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryMeasurement

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
    """advisory_codes = everything observed; fatal_codes = deterministic-for-config."""

    advisory_codes: frozenset[str]
    fatal_codes: frozenset[str]

    @property
    def is_fatal(self) -> bool:
        return bool(self.fatal_codes)

    @property
    def all_codes(self) -> list[str]:
        return sorted(self.advisory_codes | self.fatal_codes)

    @property
    def dominant_fatal(self) -> str | None:
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
    """Walk advisories + raw response shape; return advisory + fatal codes."""
    advisories = _collect_advisories(result)
    fatals: set[str] = set()

    if "llm_only:content_empty" in advisories:
        finish_reason, reasoning_tokens = _llm_only_shape(result)
        if finish_reason == "length" and reasoning_tokens > 0:
            fatals.add("llm_only:reasoning_budget_exhausted")
        elif finish_reason == "length":
            fatals.add("llm_only:output_truncated")
        else:
            fatals.add("llm_only:empty_response")

    for adv in advisories:
        if adv.endswith(":content_filtered"):
            fatals.add(adv)

    return ResultClassification(
        advisory_codes=frozenset(advisories),
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
    """True iff the classifier marked any fatal code — a deprecated data point."""
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

    n_min: int = 4
    epsilon: float = 0.05
    lock_in: float = 0.95
    lock_in_n_min: int = 8


class PoBBCheck:
    """Bayesian Posterior-of-Being-Best stop rule (Russo 2016)."""

    name = "elimination"

    def __init__(self, config: PoBBConfig, *, n_samples: int) -> None:
        self.n_min = config.n_min
        self.epsilon = config.epsilon
        self.lock_in = config.lock_in
        self.lock_in_n_min = config.lock_in_n_min
        self.n_samples = n_samples
        self.priors: dict[str, list[float]] = {}
        self.prior_ids: list[str] = []
        self._current_id: str = ""
        self._on_snapshot: Callable[[PoBBSnapshot], None] | None = None
        self._last_snapshot: PoBBSnapshot | None = None

    def set_current(
        self,
        candidate_id: str,
        on_snapshot: Callable[[PoBBSnapshot], None] | None = None,
    ) -> None:
        """Bind the candidate-under-evaluation; reset per-candidate snapshot state."""
        self._current_id = candidate_id
        self._on_snapshot = on_snapshot
        self._last_snapshot = None

    def register_completed(self, scores: list[float], candidate_id: str = "") -> None:
        """Add a completed candidate's score history to the priors pool."""
        self.priors[candidate_id] = list(scores)
        self.prior_ids.append(candidate_id)

    def latest_snapshot(self) -> PoBBSnapshot | None:
        return self._last_snapshot

    def check(
        self, results: list[QueryMeasurement], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
        if not self.priors:
            return None
        n = len(results)
        if n < self.n_min:
            return None

        scores = [r.get("fitness", 0.0) for r in results]
        cid = self._current_id or "__current__"
        histories: dict[str, list[float]] = {**self.priors, cid: scores}
        snapshot = posterior_best_probabilities(histories)
        snap = PoBBSnapshot(p_best=snapshot, current_id=cid, n_samples=n)
        self._last_snapshot = snap
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
                    "n_priors": len(self.priors),
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
                "n_priors": len(self.priors),
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
