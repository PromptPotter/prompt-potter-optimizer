"""Layer narrative-state + supplemental-rule injection renderers.

Carries the persistent narrative state authored by prior LLM calls —
L3's plan, L2's task-context framing and L3→L2 note, the current best
prompt, the L1 critique — plus the auto-triggered + L2-authored
situational rules and worked examples appended to L1's instruction.
All share the uniform ``(InjectionBundle) -> str`` renderer signature.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from promptpotter.application.optimization.dispatch.hub.auto_rules import (
    AUTO_RULES,
    BUILTIN_EXAMPLES,
)
from promptpotter.application.optimization.dispatch.hub.bundle import (
    TASK_CONTEXT_VALUE_CAP,
    InjectionBundle,
)
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS

logger = logging.getLogger(__name__)

# LaTeX corruption markers — match 'oxed{' NOT preceded by '\b' (legitimate
# `\boxed{` is preceded by '\b' so the lookbehind skips it) OR 'athematical'
# at a word boundary NOT preceded by 'm' (so 'mathematical' is skipped).
_LATEX_CORRUPTION_RE = re.compile(r"(?<!\\b)oxed\{|(?<![a-zA-Z])athematical")


def _r_plan(b: InjectionBundle) -> str:
    return f"PLAN:\n{b.opt_sp.plan}" if b.opt_sp.plan else ""


def _r_l3_to_l2_note(b: InjectionBundle) -> str:
    """Sticky L3→L2 pointer. Mounted only in L2's template; absent from
    ``L1_POSSIBLE`` so L1 never sees it."""
    note = b.opt_sp.wounds.l3_note
    return f"L3 NOTE TO L2:\n{note}" if note else ""


def _r_rendered_prompt(b: InjectionBundle) -> str:
    rendered = b.opt_sp.render()
    return f"CURRENT PROMPT:\n---\n{rendered}\n---" if rendered else ""


def _r_task_context(b: InjectionBundle) -> str:
    tc = b.opt_sp.task_context
    if not tc:
        return ""
    skip = {"raw_description", "upstream_context", "downstream_context"}
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k not in skip]
    if not pairs:
        return ""
    lines: list[str] = []
    for k, v in pairs:
        if len(v) > TASK_CONTEXT_VALUE_CAP:
            # L2 authors task_context; an over-cap value is an LLM mistake —
            # truncate (heal) + warn rather than let it bloat every prompt.
            logger.warning(
                "task_context.%s is %d chars (cap %d) — L2 overran its output budget; truncating",
                k,
                len(v),
                TASK_CONTEXT_VALUE_CAP,
            )
            v = v[:TASK_CONTEXT_VALUE_CAP] + "…"
        lines.append(f"  {k}: {v}")
    return "TASK CONTEXT:\n" + "\n".join(lines)


def _valid_axis_set(schema: Any) -> set[str]:
    """Axes the schema legitimately exposes: prompt fields + node names + param keys.

    L2's free-form ``suggested_axes`` may hallucinate names that aren't in
    the pipeline (e.g. ``prompt_size``); rendering those into the next
    round's prompt seeds the cycle with a non-existent mutation target.
    Match the union the operator can actually act on.
    """
    from promptpotter.config.settings import PROMPT_STRING_FIELDS

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


def format_l1_critique_for_prompt(critique: dict, pipeline_schema: Any = None) -> str:
    """L1 critique dict → compact text for L1_GENERATE + L2_CONTEXT consumption.

    Three load-bearing fields only: ``priority_fix`` (axis+change+quoted
    failure), ``suggested_axes`` (schema-filtered), ``failure_highlights``
    (per-sample evidence). Prose summary / positive_critique /
    negative_critique were dropped from the schema — see L1CritiqueOutput.

    When ``pipeline_schema`` is provided, ``suggested_axes`` is filtered
    against the schema's known axis names so a hallucinated axis (e.g.
    ``prompt_size``) doesn't get re-rendered into the next round's L2
    brief.
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


def _r_critique(b: InjectionBundle) -> str:
    """Compact view of the most recent L1_CRITIQUE output dict."""
    return format_l1_critique_for_prompt(b.digest.critique or {}, b.pipeline_schema)


def _r_l1_overrides(b: InjectionBundle) -> str:
    if not b.opt_sp.l1_overrides:
        return ""
    return f"CURRENT L1 CONFIG: {json.dumps(b.opt_sp.l1_overrides)}"


def _detect_auto_triggers(b: InjectionBundle) -> list[str]:
    """Walk the deterministic auto-trigger conditions in fixed order.

    The order is the order the rules render in L1's prompt. Each clause
    is a cheap read off the bundle — no allocation beyond the small
    intermediate strings.
    """
    triggers: list[str] = []
    if b.axes is not None and b.axes.peaked_axes():
        triggers.append("peaked_axis")
    if b.opt_sp.wounds.runtime_failures:
        triggers.append("runtime_failure")
    sa = (b.digest.critique or {}).get("suggested_axes") or []
    if any(a in PARAM_SCOPE_KEYS for a in sa):
        triggers.append("continuous_envelope")
    key_challenges = b.opt_sp.task_context.key_challenges or ""
    if "targeting L1 axis" in key_challenges:
        triggers.append("chain_bind")
    if b.opt_sp.wounds.l2_guard_breaches:
        triggers.append("l2_stall_diversity")
    if _LATEX_CORRUPTION_RE.search(b.opt_sp.render()):
        triggers.append("latex_repair")
    if any(vf.reason == "forbidden_axis" for vf in b.opt_sp.wounds.validation_failures):
        triggers.append("forbidden_axis_attempted")
    return triggers


def _r_l1_supplemental_rules(b: InjectionBundle) -> str:
    """Auto-triggered situational rules + L2-authored rules.

    Renders each active auto-trigger's canonical rule body (from
    :data:`AUTO_RULES`) followed by every L2-authored
    :class:`L1SupplementalRule` on ``opt_sp.l1_supplemental_rules`` with
    its citation in brackets. Returns empty when neither source has
    anything to say — variable absent ⇒ L1's instruction omits the
    block entirely.
    """
    rendered: list[tuple[str, str]] = []
    for trigger_id in _detect_auto_triggers(b):
        body = AUTO_RULES.get(trigger_id)
        if body:
            rendered.append((trigger_id, body))
    for rule in b.opt_sp.l1_supplemental_rules:
        body = f"{rule.body} [citation: {rule.citation}]"
        rendered.append((rule.rule_id, body))
    if not rendered:
        return ""
    lines = ["SITUATIONAL RULES (apply only when the cited evidence is present):"]
    for trigger_id, body in rendered:
        lines.append(f"  • [{trigger_id}] {body}")
    return "\n".join(lines)


def _r_l1_situational_examples(b: InjectionBundle) -> str:
    """Built-in + L2-authored worked examples for currently-active triggers.

    L2-authored examples whose ``trigger_id`` matches neither an
    auto-trigger that fired this round nor a currently-authored
    supplemental rule are silently filtered — the example would land in
    L1's prompt without the rule it illustrates, which is worse than
    omitting it.

    When the same ``trigger_id`` exists in both built-ins and L2-authored
    entries, L2's entry wins (overrides the built-in).
    """
    auto_triggers = _detect_auto_triggers(b)
    l2_rule_ids = {r.rule_id for r in b.opt_sp.l1_supplemental_rules}
    active = set(auto_triggers) | l2_rule_ids
    l2_example_triggers = {ex.trigger_id for ex in b.opt_sp.l1_situational_examples}

    blocks: list[str] = []
    for trigger_id in auto_triggers:
        if trigger_id in l2_example_triggers:
            continue  # L2's entry overrides the built-in
        builtin = BUILTIN_EXAMPLES.get(trigger_id)
        if builtin:
            blocks.append(_format_example(trigger_id, builtin))
    for l2_ex in b.opt_sp.l1_situational_examples:
        if l2_ex.trigger_id not in active:
            continue
        blocks.append(_format_example(l2_ex.trigger_id, l2_ex.model_dump()))
    if not blocks:
        return ""
    return "WORKED EXAMPLES:\n" + "\n".join(blocks)


def _format_example(trigger_id: str, ex: dict) -> str:
    """Compact worked-example block — trigger header + ✗/✓/→ lines.

    Parent excerpt is dropped: it almost always duplicates the rule body
    rendered immediately above in SITUATIONAL RULES. The ✗/✓/→ symbols
    replace verbose REJECTED/ACCEPTED/Why labels for small-model legibility.
    """
    lines = [f"  [{trigger_id}]"]
    if rej := (ex.get("rejected") or "").strip():
        lines.append(f"    ✗ {rej}")
    if acc := (ex.get("accepted") or "").strip():
        lines.append(f"    ✓ {acc}")
    if why := (ex.get("why") or "").strip():
        lines.append(f"    → {why}")
    return "\n".join(lines)
