from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import NO_RESULT, PROMPT_STRING_FIELDS
from promptpotter.domain.results import CritiqueReadout
from promptpotter.shared import extract_boxed_number, extract_gsm8k_number, extract_last_bold

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result


def display_fitness(composite_fitness: float | None, accuracy: float) -> float:
    """THE composite-or-accuracy rule, one implementation: an honest ``0.0`` is a real score, so
    only genuine absence degrades to ``accuracy``. Every display and ranking site routes here."""
    return composite_fitness if composite_fitness is not None else accuracy


def display_rank_key(composite_fitness: float | None, accuracy: float) -> tuple[float, float]:
    """``display_fitness``'s argmax form — what every DISPLAY site orders by, the mask lens with
    them. NOT the election (``elect_round_winner``'s Rasch θ-lift), which no aggregate reproduces."""
    return (display_fitness(composite_fitness, accuracy), accuracy)


# --------------------------------------------------------------------------- #
# format_l1_critique_for_prompt                                                #
# --------------------------------------------------------------------------- #


def _valid_axis_set(schema: PipelineSchema) -> set[str]:
    """Schema-legitimate axes (prompt fields + node names + param keys) — used to filter L2's
    hallucinated `suggested_axes` (e.g. `prompt_size`) before they seed the next round.
    """
    out: set[str] = set(PROMPT_STRING_FIELDS) | {"few_shot_examples", "plan"}
    for node in schema.nodes:
        if node.name:
            out.add(node.name)
        for pk in node.param_keys:
            out.add(pk)
            if node.name:
                out.add(f"{node.name}.{pk}")
    return out


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, pipeline_schema: PipelineSchema | None = None
) -> str:
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
# classify_result (was pobb/classification)                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResultClassification:
    """Three buckets: ``advisory`` observes; ``infra`` deprecates the sample without blaming the
    candidate; ``fatal`` is candidate-quality and eliminates on one sighting."""

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
        """Pick a fatal code for one-sighting fast-elimination. Reads ``fatal_codes`` ONLY — infra-driven deprecation must
        never trigger the fast path."""
        return next(iter(sorted(self.fatal_codes)), None)


_REFUSAL_PATTERN = re.compile(
    r"^\s*(?:i'?m\s+sorry|i\s+apologi[sz]e|i\s+cannot|i\s+can'?t|i'?m\s+(?:not\s+able|unable))\b",
    re.IGNORECASE,
)
"""Head-anchored regex for LLM refusal prefixes. Anchored to ``^`` so
mid-text apologies inside genuine reasoning don't false-positive — a
real refusal opens with the apology, not buries it."""


def _is_refusal(result: Mapping[str, Any]) -> bool:
    """A refusal completes with ``finish_reason=stop`` and no warning, so every advisory channel
    sees a plain MISS — L2 needs it as its own failure mode to propose a mitigation."""
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
        advisories.add(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
    if not advisories and is_error_result(result):
        advisories.add(f"{pd.get('terminal_node', 'unknown')}:error")
    if _is_refusal(result):
        advisories.add(f"{_terminal_node(result)}:model_refusal")
    return advisories


def _structural_advisory_keys(result: Mapping[str, Any]) -> set[str]:
    """Keys whose SOURCE-STAMPED ``kind`` is structural. The backend owns that verdict and PoBB reads it directly, so
    elimination stays in lockstep. A warning with no ``kind`` is NOT structural: under-count, never over-eliminate."""
    pd = result.get("pipeline_data") or {}
    keys: set[str] = set()
    for w in (pd.get("diagnostics") or {}).get("warnings") or []:
        if w.get("kind") == "structural":
            keys.add(f"{w.get('step', 'unknown')}:{w.get('code', 'unknown')}")
    return keys


def _terminal_node(result: Mapping[str, Any]) -> str:
    """The deepest node this result reached, read off its OWN ``pipeline_data`` rather than a literal name, so truncation
    classification keys on this result's terminal node and fires for a multi-node terminal LLM too."""
    pd = result.get("pipeline_data") or {}
    return pd.get("terminal_node") or "llm_only"


def _terminal_llm_shape(result: Mapping[str, Any]) -> tuple[str | None, int]:
    """(finish_reason, reasoning_tokens) from the terminal LLM node's step_tokens;
    (None, 0) if missing."""
    pd = result.get("pipeline_data") or {}
    st = (pd.get("step_tokens") or {}).get(_terminal_node(result)) or {}
    fr = st.get("finish_reason")
    reasoning = int(st.get("reasoning") or 0)
    return (fr, reasoning)


def classify_result(result: Mapping[str, Any]) -> ResultClassification:
    """Advisories + response shape → advisory / infra / fatal codes. Truncation is INFRA (provider-ceiling, recurs per
    sample); a backend 4xx is FATAL, so one sighting kills the candidate instead of poisoning every remaining one."""
    advisories = _collect_advisories(result)
    structural_advs = _structural_advisory_keys(result)
    infra: set[str] = set()
    fatals: set[str] = set()

    node = _terminal_node(result)
    # ``content_empty`` describes ONE ATTEMPT, not the result: the backend raises it and
    # retries (``llm_retry`` beside it, both stamped transient), and that retry can answer.
    # A result carrying a real prediction is not an empty response whatever the advisory
    # says — three archived rows recovered this way and two scored 1.0, yet all three were
    # stamped ``empty_response``, whose FATAL routing fast-eliminates the candidate off one
    # sighting. Read the result, not the attempt. ``NO_RESULT`` is the scorer's sentinel for
    # "terminal node emitted nothing parseable" (``results_health`` owns the round-level
    # version of this same question).
    predicted = str(result.get("predicted") or "").strip()
    answered = bool(predicted) and predicted != NO_RESULT
    if f"{node}:content_empty" in advisories and not answered:
        finish_reason, reasoning_tokens = _terminal_llm_shape(result)
        # ``reasoning_tokens > 0`` is proof the model WORKED — it neither refused (a refusal
        # carries content, or ``finish_reason=content_filter``) nor idled. Emitting nothing
        # visible after thinking is a property of the ROUTE, deterministic for every prompt
        # we could send it, so it routes to infra whatever ended the call: hitting the cap
        # (``length``) and stopping on its own (``stop``) are the same fault seen at two
        # budgets. Observed on ``z-ai/glm-4.7-flash`` — empty content, ``stop``, 5352
        # reasoning chars, then a schema-repair re-prompt — and charging that to the
        # candidate fast-eliminates a prompt that was never read.
        if reasoning_tokens > 0:
            infra.add(
                f"{node}:reasoning_budget_exhausted"
                if finish_reason == "length"
                else f"{node}:reasoning_only_response"
            )
        elif finish_reason == "length":
            infra.add(f"{node}:output_truncated")
        else:
            fatals.add(f"{node}:empty_response")

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
    # Route through the shared AIME extractor so the shown answer IS the value the
    # scorer (`_aime_match`) matched — a non-numeric `\boxed{…}` falls back to the
    # last number (as the scorer does), never the raw boxed junk. Mirrors
    # `_extract_gsm8k_display`'s int-if-integral formatting; stripped text when none.
    n = extract_boxed_number(text or "")
    if n is None:
        return (text or "").strip()
    return str(int(n)) if n.is_integer() else str(n)


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
    "classify_result",
    "display_fitness",
    "display_rank_key",
    "extract_display_answer",
    "format_l1_critique_for_prompt",
]
