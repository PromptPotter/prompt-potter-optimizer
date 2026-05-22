"""Result classification — three-bucket (advisory / infra / fatal) verdict.

classify_result(result) → three-bucket classification:
  llm_only:content_empty + length + reasoning>0 → infra:reasoning_budget_exhausted
  llm_only:content_empty + length + reasoning=0 → infra:output_truncated
  llm_only:content_empty + stop                 → fatal:empty_response
  *:content_filtered                            → fatal (passthrough)

``infra_codes`` mark the sample deprecated for accounting + display, but
DegradationCheck's one-sighting fast-path (``dominant_fatal``) reads only
from ``fatal_codes`` — truncation alone does not eliminate a candidate at
n=1. Rate-based degradation still counts truncations toward elimination.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


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


_REFUSAL_PATTERN = re.compile(
    r"^\s*(?:i'?m\s+sorry|i\s+apologi[sz]e|i\s+cannot|i\s+can'?t|i'?m\s+(?:not\s+able|unable))\b",
    re.IGNORECASE,
)
"""Head-anchored regex for LLM refusal prefixes. Anchored to ``^`` so
mid-text apologies inside genuine reasoning don't false-positive — a
real refusal opens with the apology, not buries it."""


def _is_refusal(result: Mapping[str, Any]) -> bool:
    """Detect head-anchored refusal patterns in the predicted answer.

    The model occasionally returns a refusal as its full output (e.g.
    ``"I'm sorry, but I cannot solve this problem."``) instead of an
    actual answer. These slip past every existing advisory channel
    because ``finish_reason=stop`` and there's no diagnostics warning —
    the model "completed" normally, it just completed with a refusal.

    L2 needs to see these as a distinct failure mode to propose
    mitigations (different model, rephrased instruction). Plain ``MISS``
    classification loses that signal.
    """
    predicted = str(result.get("predicted") or "")
    if not predicted:
        return False
    # Cap the matched prefix at the first sentence/120 chars — refusals
    # are short; a 500-char prediction that opens with apology framing
    # is likely a real reasoning chain that started with hedging.
    head = predicted[:120]
    return bool(_REFUSAL_PATTERN.match(head))


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
    if _is_refusal(result):
        terminated_at = pd.get("terminated_at") or "llm_only"
        advisories.add(f"{terminated_at}:model_refusal")
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
        elif adv.endswith(":model_refusal"):
            # Refusal routes to infra (not fatal): the same query at a
            # different temperature / rephrased instruction can recover,
            # so don't fast-path eliminate the candidate at n=1. But
            # surfacing it in infra_codes routes it to RUNTIME FAILURES
            # so L2 sees the pattern and can propose mitigations
            # (different model, less safety-triggering instruction).
            infra.add(adv)

    if is_error_result(result) and error_category(result.get("error")) == ErrorCategory.CLIENT:
        fatals.add("backend:client_error")

    return ResultClassification(
        advisory_codes=frozenset(advisories),
        infra_codes=frozenset(infra),
        fatal_codes=frozenset(fatals),
    )


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


__all__ = [
    "ResultClassification",
    "classify_result",
    "extract_warning_types",
    "get_ranked_items",
    "is_deprecated",
    "ranked_item_keys_from_schema",
]
