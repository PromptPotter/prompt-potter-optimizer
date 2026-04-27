"""Per-result diagnostics — backend advisories vs PromptPotter-policy fatals.

The backend (TermNorm or any other connector) emits *neutral advisory* warnings
in ``pipeline_data.diagnostics.warnings`` and exposes raw response shape
(normalized ``finish_reason``, ``reasoning`` token count, ``max_tokens_requested``)
in ``pipeline_data.step_tokens.{node}``. This module derives **fatal codes**
from those signals.

A fatal code marks a measurement that is deterministic-for-the-config: one
sighting proves the candidate will fail the same way on every remaining query,
so spending more backend calls to confirm is waste. Consumers (DegradationCheck
fast-path, cache eviction, primary-statistics exclusion, DEPR display tag)
treat any fatal code as a deprecated row.

Rule table (extend here, not in callers):

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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from promptpotter.shared.errors import is_error_result

__all__ = ["ResultClassification", "classify_result"]


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
