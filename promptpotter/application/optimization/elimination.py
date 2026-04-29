"""Candidate elimination — classification, result helpers, and mid-round checks.

Three layers in one file:

1. **Classification** (``classify_result`` / ``ResultClassification``) — derives
   *fatal codes* from raw response shape (advisory + ``finish_reason`` +
   ``reasoning_tokens``). A fatal code marks a measurement as
   deterministic-for-the-config: one sighting proves the candidate will fail
   the same way on every remaining query, so spending more backend calls is
   waste. Rule table — extend here, not in callers:

       advisory                            finish_reason   reasoning   →  fatal
       ----------------------------------  --------------  ----------  ----------
       llm_only:content_empty              length          > 0         reasoning_budget_exhausted
       llm_only:content_empty              length          0 / unset   output_truncated
       llm_only:content_empty              stop / other    any         empty_response
       *:content_filtered                  any             any         (passthrough as fatal)

   Legacy alias: archives written before TermNorm renamed the advisory carry
   ``llm_only:empty_content_reasoning_fallback``. Treated as
   ``llm_only:reasoning_budget_exhausted`` so resume on old cycles still
   deprecates correctly.

2. **Result helpers** (``extract_warning_types``, ``is_deprecated``,
   ``candidate_keys_from_schema``, ``get_candidates``, ``update_query_tracker``)
   — pure read-only helpers over a result dict / pipeline schema. Consumed by
   elimination, L1 execute, L1 critique, scoring, and the display layer.

3. **Mid-round checks** (``DegradationCheck``, ``EliminationCheck``) —
   first-to-fire wins; produce ``EscalationSignal`` to terminate a candidate.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.application.scoring.metrics import count_degraded_queries
from promptpotter.domain.analysis import EscalationSignal, EscalationTarget
from promptpotter.shared.errors import is_error_result
from promptpotter.shared.statistics import should_stop_early

if TYPE_CHECKING:
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import QueryResult

__all__ = [
    "DegradationCheck",
    "EliminationCheck",
    "ResultClassification",
    "build_degradation_checks",
    "candidate_keys_from_schema",
    "classify_result",
    "extract_warning_types",
    "get_candidates",
    "is_deprecated",
    "update_query_tracker",
]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_LEGACY_ADVISORY = "llm_only:empty_content_reasoning_fallback"


@dataclass(frozen=True)
class ResultClassification:
    """Codes seen on a single result.

    ``advisory_codes`` is everything the backend emitted (and any synthesized
    error code). ``fatal_codes`` is what PromptPotter's policy classifies as
    deterministic-for-config; non-empty fatal_codes ⇒ row is deprecated.
    """

    advisory_codes: frozenset[str]
    fatal_codes: frozenset[str]

    @property
    def is_fatal(self) -> bool:
        return bool(self.fatal_codes)

    @property
    def all_codes(self) -> list[str]:
        """Union for callers that just want to display every code seen."""
        return sorted(self.advisory_codes | self.fatal_codes)

    @property
    def dominant_fatal(self) -> str | None:
        """First fatal code (sorted) — used as ``RoundClassification.dominant_warning``."""
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
    """Extract (finish_reason, reasoning_tokens) from ``step_tokens.llm_only``.

    Returns ``(None, 0)`` when the backend didn't surface the field — typical
    for legacy archives or non-LLM-bearing pipelines.
    """
    pd = result.get("pipeline_data") or {}
    st = (pd.get("step_tokens") or {}).get("llm_only") or {}
    fr = st.get("finish_reason")
    reasoning = int(st.get("reasoning") or 0)
    return (fr, reasoning)


def classify_result(result: Mapping[str, Any]) -> ResultClassification:
    """Walk advisories + raw response shape; return advisory + fatal codes."""
    advisories = _collect_advisories(result)
    fatals: set[str] = set()

    if _LEGACY_ADVISORY in advisories:
        fatals.add("llm_only:reasoning_budget_exhausted")

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


def candidate_keys_from_schema(schema: PipelineSchema | None) -> list[str]:
    """Derive pipeline_data candidate keys from schema's ranker/candidate_source nodes."""
    if not schema:
        return []
    keys: list[str] = []
    for node in schema.nodes:
        if node.node_type in ("ranker", "candidate_source"):
            keys.extend(node.output_keys)
    return keys


def get_candidates(r: Mapping[str, Any], candidate_keys: list[str] | None = None) -> list:
    """Extract candidates from a result dict, checking keys in order."""
    pd = r.get("pipeline_data") or {}
    for key in candidate_keys or []:
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


def update_query_tracker(
    tracker: dict[str, dict],
    results: list[QueryResult],
) -> None:
    """Merge results into the per-query warning inventory (mutates tracker)."""
    for r in results:
        query = r.get("query", "")
        if not query:
            continue
        entry = tracker.setdefault(
            query,
            {
                "rounds_seen": 0,
                "hits": 0,
                "misses": 0,
                "warnings": {},
                "last_terminated_at": "",
            },
        )
        entry["rounds_seen"] += 1
        if r.get("hit"):
            entry["hits"] += 1
        else:
            entry["misses"] += 1
        pd = r.get("pipeline_data") or {}
        terminated = pd.get("terminated_at", "")
        if terminated:
            entry["last_terminated_at"] = terminated
        for wtype in extract_warning_types(r):
            entry["warnings"][wtype] = entry["warnings"].get(wtype, 0) + 1


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


class DegradationCheck:
    """Fatal-classification fast-path + rate-based degradation.

    Fatal codes come from :func:`classify_result` — derived from raw response
    shape (finish_reason + reasoning_tokens), not from string-matching a
    backend warning. One sighting ends the candidate; not a tunable.
    """

    name = "degradation"

    def __init__(self, threshold: float = 0.4, min_queries: int = 3) -> None:
        self.threshold = threshold
        self.min_queries = min_queries

    def check(
        self, results: list[dict], candidate_idx: int, n_total_candidates: int
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
        if n < self.min_queries:
            return None
        degraded = count_degraded_queries(results)
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


class EliminationCheck:
    """Paired one-sided Wilcoxon signed-rank vs completed priors (Holm-Bonferroni)."""

    name = "elimination"

    def __init__(self, *, n_min: int = 4, alpha: float = 0.2, n_queries: int) -> None:
        self.n_min = n_min
        self.alpha = alpha
        self.n_queries = n_queries
        self.priors: list[list[float]] = []
        self.prior_ids: list[str] = []

    def register_completed(self, scores: list[float], candidate_id: str = "") -> None:
        self.priors.append(scores)
        self.prior_ids.append(candidate_id)

    def check(
        self, results: list[dict], candidate_idx: int, n_total_candidates: int
    ) -> EscalationSignal | None:
        if not self.priors:
            return None
        n = len(results)
        if n < self.n_min:
            return None
        scores = [r.get("score", 0.0) for r in results]
        stop, ctx = should_stop_early(scores, self.priors, self.alpha)
        if not stop:
            return None
        return _eliminate(
            self.name,
            {
                "queries_scored": n,
                "total_queries": self.n_queries,
                "n_priors": len(self.priors),
                **ctx,
            },
            candidate_idx,
            n_total_candidates,
        )


def build_degradation_checks(config: CampaignConfig) -> list[Any]:
    """Per-query checks (degradation). EliminationCheck is built by the runner."""
    opt = config.optimization
    checks: list[Any] = []
    if opt.degradation_threshold > 0:
        checks.append(DegradationCheck(threshold=opt.degradation_threshold))
    return checks
