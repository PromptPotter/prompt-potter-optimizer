"""Narrative-state + supplemental-rule renderers — persistent state from prior LLM calls
(L3 plan, L2 task_context + L3→L2 note, current prompt, L1 critique) plus auto-triggered +
L2-authored rules/examples appended to L1's instruction.
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
    InjectionKind,
    signal,
)
from promptpotter.domain.search_point import PARAM_SCOPE_KEYS

logger = logging.getLogger(__name__)

# LaTeX corruption — match `oxed{` not preceded by `\b` (skips legit `\boxed{`) OR `athematical`
# at a word boundary not preceded by `m` (skips `mathematical`).
_LATEX_CORRUPTION_RE = re.compile(r"(?<!\\b)oxed\{|(?<![a-zA-Z])athematical")


@signal(
    "plan",
    kind=InjectionKind.TRACE,
    description="L3's strategic plan text. Persistent until next L3 fire.",
    char_cap=800,
)
def _r_plan(b: InjectionBundle) -> str:
    """L3's strategic plan text — read by every prompt; persistent until next L3 fire."""
    plan = b.opt_sp.plan
    return f"PLAN:\n{plan}" if plan else ""


@signal(
    "l3_to_l2_note",
    kind=InjectionKind.DIRECTIVE,
    description="Sticky L3→L2 pointer. Mounted only in L2's template; absent from L1.",
    char_cap=400,
)
def _r_l3_to_l2_note(b: InjectionBundle) -> str:
    """Sticky L3→L2 directive — mounted only in L2's template, absent from L1."""
    note = b.opt_sp.memory.wounds.l3_note
    return f"L3 NOTE TO L2:\n{note}" if note else ""


@signal(
    "rendered_prompt",
    kind=InjectionKind.TRACE,
    description="Current best searchpoint's compiled prompt body.",
    char_cap=2500,
)
def _r_rendered_prompt(b: InjectionBundle) -> str:
    """Current best searchpoint's compiled prompt body."""
    body = b.opt_sp.render()
    return f"CURRENT PROMPT:\n---\n{body}\n---" if body else ""


@signal(
    "l1_overrides",
    kind=InjectionKind.TRACE,
    description="Current L1 runtime knobs (creativity, n_variants, etc.) as JSON.",
    char_cap=None,
)
def _r_l1_overrides(b: InjectionBundle) -> str:
    """Current L1 runtime knobs (creativity, n_variants, …) as JSON."""
    overrides = b.opt_sp.memory.l1_overrides
    return f"CURRENT L1 CONFIG: {json.dumps(overrides)}" if overrides else ""


@signal(
    "task_context",
    kind=InjectionKind.TRACE,
    description="Persistent task framing dict refined by L2; broadcast to all four prompts.",
    char_cap=None,  # _r_task_context caps each field at TASK_CONTEXT_VALUE_CAP
)
def _r_task_context(b: InjectionBundle) -> str:
    tc = b.opt_sp.memory.task_context
    if not tc:
        return ""
    skip = {"raw_description", "upstream_context", "downstream_context"}
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k not in skip]
    if not pairs:
        return ""
    lines: list[str] = []
    for k, v in pairs:
        if len(v) > TASK_CONTEXT_VALUE_CAP:
            # L2-authored overrun — truncate (heal) + warn, don't bloat every prompt.
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
    """Schema-legitimate axes (prompt fields + node names + param keys) — used to filter L2's
    hallucinated `suggested_axes` (e.g. `prompt_size`) before they seed the next round.
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


def format_l1_critique_for_prompt(critique: dict[str, Any], pipeline_schema: Any = None) -> str:
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


@signal(
    "critique",
    kind=InjectionKind.TRACE,
    description="Compact view of the most recent L1_CRITIQUE LLM output dict.",
    char_cap=800,
)
def _r_critique(b: InjectionBundle) -> str:
    """Compact view of the most recent L1_CRITIQUE output dict."""
    return format_l1_critique_for_prompt(b.digest.critique or {}, b.pipeline_schema)


_REBASE_CAPABILITY_TEXT = (
    "FORK PROPOSAL (rare escape hatch). If the current subtree is genuinely "
    "exhausted — multiple stall rounds in this lineage with no lift, AND the "
    "panels point to a specific deferred ancestor round worth re-expanding — you "
    'may emit fork_proposal = {"round_offset": -N, "reason": "<1-2 sentences>"}. '
    "round_offset MUST be a negative integer (the rewind distance from the "
    "current round). The runner mints a sibling cycle at current+round_offset and "
    "auto-continues optimization there. Capped at 10 rebases per session. "
    "Default: omit. Prefer continuing the current strategy; fork only when "
    "refining this trajectory cannot recover."
)


@signal(
    "rebase_capability",
    kind=InjectionKind.DIRECTIVE,
    description=(
        "Conditional fork_proposal escape-hatch instruction (renders into L2 + "
        "L3 prompts). Empty when ``OptimizationConfig.rebase_capability`` is "
        "off — keeps prompt body bit-for-bit identical to a no-rebase "
        "ablation so the input distribution doesn't drift on prompt text."
    ),
    char_cap=None,
)
def _r_rebase_capability(b: InjectionBundle) -> str:
    """Render the fork_proposal escape-hatch instruction, gated by
    ``OptimizationConfig.rebase_capability``. When the capability is off
    this returns the empty string so the L2/L3 prompt body is bit-for-bit
    identical to a no-rebase ablation run."""
    if not b.rebase_capability:
        return ""
    return _REBASE_CAPABILITY_TEXT


def _detect_auto_triggers(b: InjectionBundle) -> list[str]:
    """Walk auto-trigger conditions in fixed order — also the render order in L1's prompt."""
    triggers: list[str] = []
    if b.axes is not None and b.axes.peaked_axes():
        triggers.append("peaked_axis")
    if b.opt_sp.memory.wounds.runtime_failures:
        triggers.append("runtime_failure")
    sa = (b.digest.critique or {}).get("suggested_axes") or []
    if any(a in PARAM_SCOPE_KEYS for a in sa):
        triggers.append("continuous_envelope")
    key_challenges = b.opt_sp.memory.task_context.key_challenges or ""
    if "targeting L1 axis" in key_challenges:
        triggers.append("chain_bind")
    if b.opt_sp.memory.wounds.l2_guard_breaches:
        triggers.append("l2_stall_diversity")
    if _LATEX_CORRUPTION_RE.search(b.opt_sp.render()):
        triggers.append("latex_repair")
    if any(vf.reason == "forbidden_axis" for vf in b.opt_sp.memory.wounds.validation_failures):
        triggers.append("forbidden_axis_attempted")
    return triggers


@signal(
    "l1_supplemental_rules",
    kind=InjectionKind.DIRECTIVE,
    description=(
        "Situational rules appended to L1's instruction — auto-triggered from "
        "bundle state (PEAKED axes, runtime failures, chain-bind, continuous-axis, "
        "L2 stall, LaTeX corruption) plus L2-authored entries on opt_sp."
    ),
    char_cap=1000,
)
def _r_l1_supplemental_rules(b: InjectionBundle) -> str:
    """Auto-triggered rules (from `AUTO_RULES`) + L2-authored ones (cited). Empty → L1 omits the block."""
    rendered: list[tuple[str, str]] = []
    for trigger_id in _detect_auto_triggers(b):
        body = AUTO_RULES.get(trigger_id)
        if body:
            rendered.append((trigger_id, body))
    for rule in b.opt_sp.memory.l1_supplemental_rules:
        body = f"{rule.body} [citation: {rule.citation}]"
        rendered.append((rule.rule_id, body))
    if not rendered:
        return ""
    lines = ["SITUATIONAL RULES (apply only when the cited evidence is present):"]
    for trigger_id, body in rendered:
        lines.append(f"  • [{trigger_id}] {body}")
    return "\n".join(lines)


@signal(
    "l1_situational_examples",
    kind=InjectionKind.DIRECTIVE,
    description=(
        "Worked examples pinned to currently-active triggers — built-ins shipped "
        "in auto_rules.py plus L2-authored entries on opt_sp. Examples whose "
        "trigger is not active this round are silently filtered."
    ),
    char_cap=1000,
)
def _r_l1_situational_examples(b: InjectionBundle) -> str:
    """Worked examples for currently-active triggers. L2-authored examples without a matching
    active trigger filter out (would orphan from the rule). L2 entry overrides matching built-in.
    """
    auto_triggers = _detect_auto_triggers(b)
    l2_rule_ids = {r.rule_id for r in b.opt_sp.memory.l1_supplemental_rules}
    active = set(auto_triggers) | l2_rule_ids
    l2_example_triggers = {ex.trigger_id for ex in b.opt_sp.memory.l1_situational_examples}

    blocks: list[str] = []
    for trigger_id in auto_triggers:
        if trigger_id in l2_example_triggers:
            continue  # L2's entry overrides the built-in
        builtin = BUILTIN_EXAMPLES.get(trigger_id)
        if builtin:
            blocks.append(_format_example(trigger_id, builtin))
    for l2_ex in b.opt_sp.memory.l1_situational_examples:
        if l2_ex.trigger_id not in active:
            continue
        blocks.append(_format_example(l2_ex.trigger_id, l2_ex.model_dump()))
    if not blocks:
        return ""
    return "WORKED EXAMPLES:\n" + "\n".join(blocks)


def _format_example(trigger_id: str, ex: dict[str, Any]) -> str:
    """Worked-example block: trigger header + ✗/✓/→ lines. Parent excerpt dropped (duplicates the
    rule body rendered just above); ✗/✓/→ symbols replace verbose labels for small-model legibility.
    """
    lines = [f"  [{trigger_id}]"]
    if rej := (ex.get("rejected") or "").strip():
        lines.append(f"    ✗ {rej}")
    if acc := (ex.get("accepted") or "").strip():
        lines.append(f"    ✓ {acc}")
    if why := (ex.get("why") or "").strip():
        lines.append(f"    → {why}")
    return "\n".join(lines)
