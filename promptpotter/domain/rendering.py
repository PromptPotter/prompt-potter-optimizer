"""Pure rendering + classification helpers shared by application + presentation.

These four functions (plus their tightly-coupled private helpers + the
``ResultClassification`` dataclass) take plain dicts / scalars and return text or
a verdict — no I/O, no orchestration state. They live in ``domain/`` so the
display formatters in ``presentation/views/`` can render rounds without importing
``application/`` (the layer rule, both directions). Their original application
homes re-export them so existing optimizer-side callers don't churn.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.results import CritiqueReadout
from promptpotter.shared import BOXED_RE, NUMBER_RE, extract_gsm8k_number, extract_last_bold
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result

# --------------------------------------------------------------------------- #
# display_fitness + round_winner_key — the one composite-or-accuracy resolution #
# --------------------------------------------------------------------------- #


def display_fitness(composite_fitness: float | None, accuracy: float) -> float:
    """The canonical fitness value shown to / ranked-by the operator: the active-formula
    `composite_fitness` when present (a real `0.0` — e.g. a validation-failed candidate — is
    an honest score and is kept), degrading to plain `accuracy` only on genuine absence
    (`None`, i.e. no active formula). One resolution of the composite-or-accuracy rule that
    every display + ranking site routes through, so an `or` can never mask the honest 0.
    """
    return composite_fitness if composite_fitness is not None else accuracy


def round_winner_key(composite_fitness: float | None, accuracy: float) -> tuple[float, float]:
    """Composite-first, accuracy-tiebreak. The point-estimate ordering used by the
    mask lens (`application/mask`) to re-project candidates under an alternative
    criterion — NOT the round-winner election (that's `elect_round_winner`'s
    paired-delta LCB; the view reads the elected winner off the round result).
    """
    return (display_fitness(composite_fitness, accuracy), accuracy)


# --------------------------------------------------------------------------- #
# format_l1_critique_for_prompt (was dispatch/hub/injections/layer_state)      #
# --------------------------------------------------------------------------- #


def _valid_axis_set(schema: Any) -> set[str]:
    """Schema-legitimate axes (prompt fields + node names + param keys) — used to filter L2's
    hallucinated `suggested_axes` (e.g. `prompt_size`) before they seed the next round.
    """
    out: set[str] = set(PROMPT_STRING_FIELDS) | {"few_shot_examples", "plan"}
    if schema is None:
        return out
    for node in getattr(schema, "nodes", ()):
        name = getattr(node, "name", "")
        if name:
            out.add(name)
        for pk in getattr(node, "param_keys", ()) or ():
            out.add(pk)
            if name:
                out.add(f"{name}.{pk}")
    return out


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, pipeline_schema: Any = None
) -> str:
    """L1 critique → compact text for L1_GENERATE + L2_CONTEXT. Three load-bearing fields:
    `priority_fix`, schema-filtered `suggested_axes`, `failure_highlights`.
    """
    if not critique:
        return ""
    parts: list[str] = []
    if pf := critique.get("priority_fix"):
        parts.append(f"Fix: {pf}")
    sa = critique.get("suggested_axes") or []
    if sa:
        if pipeline_schema is not None:
            valid = _valid_axis_set(pipeline_schema)
            sa = [a for a in sa if a in valid]
        if sa:
            parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append("Failures:")
        for h in fh[:3]:
            parts.append(f"  {h}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# classify_result (was pobb/elimination/classification)                       #
# --------------------------------------------------------------------------- #


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


def _structural_advisory_keys(result: Mapping[str, Any]) -> set[str]:
    """``"step:code"`` keys whose **source-stamped** ``kind`` is structural.

    The backend (TermNorm ``WarningKind``) owns the structural/transient verdict;
    PoBB reads it directly so elimination stays in lockstep with the degradation
    verdict — one truth, no re-derivation from the code. A warning with no ``kind``
    is NOT treated structural (no guessing): unstamped → under-counts, never
    over-eliminates."""
    pd = result.get("pipeline_data") or {}
    keys: set[str] = set()
    for w in (pd.get("diagnostics") or {}).get("warnings") or []:
        if isinstance(w, dict) and w.get("kind") == "structural":
            keys.add(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
    return keys


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
    structural_advs = _structural_advisory_keys(result)
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
        elif adv in structural_advs:
            # Source-stamped structural (``WarningKind.STRUCTURAL`` from the backend):
            # a deterministic-for-config candidate failure — route to fatal so
            # DegradationCheck fast-eliminates the candidate instead of retrying the
            # same broken config on every remaining sample. Lockstep with the
            # degradation verdict, which grades the same warning structural-critical
            # off the same stamped field (one truth, not two disagreeing classifiers).
            fatals.add(adv)

    if is_error_result(result) and error_category(result) == ErrorCategory.CLIENT:
        fatals.add("backend:client_error")

    return ResultClassification(
        advisory_codes=frozenset(advisories),
        infra_codes=frozenset(infra),
        fatal_codes=frozenset(fatals),
    )


# --------------------------------------------------------------------------- #
# extract_display_answer — the display side of the scorer's label extraction.   #
# Shares the answer-isolation primitives with the scorer (promptpotter.shared)  #
# so the parsed answer shown matches the one scored; only the display-specific  #
# string formatting lives here.                                                 #
# --------------------------------------------------------------------------- #


def _extract_gsm8k_display(text: str) -> str:
    n = extract_gsm8k_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_boxed_display(text: str) -> str:
    if not text:
        return ""
    boxed = BOXED_RE.findall(text)
    if boxed:
        last_boxed: str = boxed[-1]
        return last_boxed.strip()
    nums = NUMBER_RE.findall(text)
    if nums:
        last_num: str = nums[-1]
        return last_num
    return text.strip()


DISPLAY_EXTRACTORS: dict[str, Any] = {
    "exact_match": extract_last_bold,
    "gsm8k_match": _extract_gsm8k_display,
    "aime_match": _extract_boxed_display,
}


def extract_display_answer(predicted: str, formula: str | None) -> str:
    """Return the parsed answer for *predicted* under *formula*; falls back to stripped text."""
    text = (predicted or "").strip()
    if not formula:
        return text
    for name, extractor in DISPLAY_EXTRACTORS.items():
        if name in formula:
            return str(extractor(predicted or "")).strip()
    return text


__all__ = [
    "DISPLAY_EXTRACTORS",
    "ResultClassification",
    "classify_result",
    "extract_display_answer",
    "format_l1_critique_for_prompt",
    "round_winner_key",
]
