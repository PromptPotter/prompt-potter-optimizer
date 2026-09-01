from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import NO_RESULT, PROMPT_STRING_FIELDS
from promptpotter.domain.results import CritiqueReadout
from promptpotter.shared import (
    extract_boxed_number,
    extract_gsm8k_number,
    extract_last_bold,
    text_list_items,
)

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.shared.errors import ErrorCategory, error_category, is_error_result


def display_fitness(composite_fitness: float | None, accuracy: float) -> float:
    """THE composite-or-accuracy rule, one implementation: an honest ``0.0`` is a real score, so
    only genuine absence degrades to ``accuracy``. Every display and ranking site routes here."""
    return composite_fitness if composite_fitness is not None else accuracy


# The ordering key every display site sorts on. Named once because it was restated as a bare
# tuple at two call sites, and adding a term to the key silently broke both — the shape is
# `display_rank_key`'s to declare, not each caller's to remember.
DisplayRankKey = tuple[bool, bool, float, float, float]


def display_rank_key(
    composite_fitness: float | None,
    accuracy: float,
    theta: float | None = None,
    *,
    is_winner: bool = False,
    is_partial: bool = False,
) -> DisplayRankKey:
    """``display_fitness``'s argmax form — what every DISPLAY site orders by.

    On a warm round rank 1 IS the crown, by construction: the round is won on Rasch θ-lift over
    the parent (``elect_round_winner``), so a table ordered on the composite could seat the winner
    anywhere and offer no column that explained it. Both leading terms DEFAULT OFF, so a cold
    round — no candidate carrying a θ, nothing crowned yet — orders on the composite alone.

    ⚠️ A mask lens must keep passing two arguments (``mask/verdicts.py``). It exists to show a
    DIFFERENT ordering under a masked formula, and pinning the active-formula winner to rank 1
    there would leave it unable to disagree."""
    return (
        is_winner,
        # A rate the operator CUT SHORT never outranks one measured on the whole panel: the round
        # order is stratified, so the cells a stopped walk kept are a biased slice rather than a
        # smaller sample of the same thing.
        not is_partial,
        theta if theta is not None else -math.inf,
        display_fitness(composite_fitness, accuracy),
        accuracy,
    )


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


def _priority_fix_axis(priority_fix: str) -> str:
    """The axis a ``<axis>: <change>`` steer names, so the menu beside it cannot omit it."""
    head, sep, _ = priority_fix.partition(":")
    axis = head.strip()
    return axis if sep and axis.isidentifier() else ""


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, pipeline_schema: PipelineSchema | None = None
) -> str:
    if not critique:
        return ""
    parts: list[str] = []
    pf = critique.get("priority_fix") or ""
    if pf:
        parts.append(f"Fix: {pf}")
    sa = list(critique.get("suggested_axes") or [])
    # The steer's own axis leads its menu: a `Fix:` naming an axis `Axes:` omitted told the
    # generator two different things about one round, and it was the steer that got followed.
    if lead := _priority_fix_axis(pf):
        sa = [lead, *(a for a in sa if a != lead)]
    if pipeline_schema is not None:
        valid = _valid_axis_set(pipeline_schema)
        sa = [a for a in sa if a in valid]
    if sa:
        parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append("Failures:")
        for h in fh[:3]:
            parts.append(f"  {h}")
    if not parts:
        return ""
    # Titled like every panel beside it. `critique` is offered in the citation enum, but the block
    # rendered untitled, so variants grounding on it named whichever heading rendered above.
    return "\n".join(["CRITIQUE (last round's failures, distilled):", *parts])


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
        advisories.add(f"{terminal_node(result)}:error")
    if _is_refusal(result):
        advisories.add(f"{terminal_node(result)}:model_refusal")
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


def terminal_node(result: Mapping[str, Any]) -> str:
    """The deepest node this result reached, read off its OWN ``pipeline_data`` rather than a literal name, so truncation
    classification keys on this result's terminal node and fires for a multi-node terminal LLM too."""
    pd = result.get("pipeline_data") or {}
    return pd.get("terminal_node") or "llm_only"


def _terminal_llm_shape(result: Mapping[str, Any]) -> tuple[str | None, int]:
    """(finish_reason, reasoning_tokens) from the terminal LLM node's step_tokens;
    (None, 0) if missing."""
    pd = result.get("pipeline_data") or {}
    st = (pd.get("step_tokens") or {}).get(terminal_node(result)) or {}
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

    node = terminal_node(result)
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


def _one_line(text: str) -> str:
    # Collapses every whitespace run, newlines included, and strips — one call so no extractor
    # has to remember it.
    return " ".join(text.split())


def _extract_gsm8k_display(text: str) -> str:
    n = extract_gsm8k_number(text)
    if n is None:
        return text.strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_boxed_display(text: str) -> str:
    # Route through the shared AIME extractor so the shown answer IS the value the
    # scorer (`_aime_match`) matched — a non-numeric `\boxed{…}` falls back to the
    # last number (as the scorer does), never the raw boxed junk. Mirrors
    # `_extract_gsm8k_display`'s int-if-integral formatting; stripped text when none.
    n = extract_boxed_number(text)
    if n is None:
        return text.strip()
    return str(int(n)) if n.is_integer() else str(n)


def _extract_list_display(text: str) -> str:
    # A list matcher's answer is the whole ORDERED SET, so route through the same
    # `text_list_items` walk `_list_rr` scores on: bullets and `1.` numbering stripped
    # the same way, joined so the slate the scorer read stays one readable line.
    items = text_list_items(text)
    return " | ".join(items) if items else text


DISPLAY_EXTRACTORS: dict[str, Any] = {
    "exact_match": extract_last_bold,
    "gsm8k_match": _extract_gsm8k_display,
    "aime_match": _extract_boxed_display,
    "list_rr": _extract_list_display,
}


def extract_display_answer(predicted: str, formula: str | None) -> str:
    """The parsed answer for *predicted* under *formula*, on ONE line; stripped text when no
    extractor claims the formula.

    Single-line is the CONTRACT, not the caller's to re-impose: every consumer renders into a
    one-line-per-sample readout, so a multi-line answer — a ranked slate, reasoning no extractor
    isolates — splits the row and the ANSI-stripped `logs/latest.log` mirror with it."""
    text = predicted
    if formula:
        for name, extractor in DISPLAY_EXTRACTORS.items():
            if name in formula:
                return _one_line(str(extractor(text)))
    return _one_line(text)


__all__ = [
    "DisplayRankKey",
    "classify_result",
    "display_fitness",
    "display_rank_key",
    "extract_display_answer",
    "format_l1_critique_for_prompt",
    "terminal_node",
]
