"""Auto-trigger rule registry + built-in worked examples.

Six canonical trigger IDs the dispatch hub emits into L1's prompt when
their evidence is present in the bundle. Each rule body is a short
sentence (1-3 lines) appended to L1's ``instruction`` slot via the
``l1_supplemental_rules`` injection.

The trigger IDs are also the keys L2 may pin a worked example to via
:class:`L1SituationalExample`. When L2 authors a supplemental rule with
a new ``rule_id``, examples may pin to that ID too — the renderer
unions auto-trigger IDs with L2-authored rule IDs and only emits
examples whose ``trigger_id`` is currently active.

Adding a new auto-trigger: add an entry to :data:`AUTO_RULES` and add
the trigger-detection clause to ``_detect_auto_triggers`` in
``injections.py``. Optionally add a built-in example to
:data:`BUILTIN_EXAMPLES`.
"""

from __future__ import annotations

# Mapping of trigger_id → rule body. Bodies should be 1-3 short sentences;
# the renderer prefixes each with the trigger_id in brackets.
AUTO_RULES: dict[str, str] = {
    "peaked_axis": (
        "PEAKED axes (the parent's current value IS the measured peak): "
        "do NOT mutate without quoting a sibling_yield row with mean_delta>0 "
        "on the same axis OR escalation_panel.exploration_budget=wide."
    ),
    "runtime_failure": (
        "KNOWN-FAILING configs (see runtime_failures above): do not re-propose. "
        "To revisit that region, change a different axis simultaneously so the new "
        "variant is not a repeat."
    ),
    "continuous_envelope": (
        "Continuous-axis envelope: for numeric knobs (max_tokens, temperature, "
        "top_p, reasoning_effort), stay within ±50% of the parent value unless "
        "sibling_yield, runtime_failures, or exploration_budget=wide justifies "
        "a larger jump."
    ),
    "chain_bind": (
        "Chain-binding directive: task_context.key_challenges names an L1 axis "
        "via `targeting L1 axis '<name>'` and quotes a failure cluster. AT LEAST "
        "ONE variant MUST target that named axis and cite the quoted cluster "
        "verbatim in evidence_grounding.citation (field=task_context)."
    ),
    "latex_repair": (
        "LaTeX-repair: parent prompt contains corrupted tokens (e.g. 'oxed' for "
        "'\\boxed', 'athematical' for 'mathematical'). Repair these in your "
        "mutation; do not propagate the corruption."
    ),
    "l2_stall_diversity": (
        "L2 stall recovery: the framing chain has stalled (l2_guard_breaches "
        "above). At least one variant MUST propose a STRUCTURALLY distinct "
        "mutation type — swap a prompt-field rewrite for an answer_format "
        "restructure, or swap a verify/restate framing for a different solution "
        "method."
    ),
    "forbidden_axis_attempted": (
        "OPERATOR LOCK: a prior round proposed a forbidden axis "
        "(model/provider) — validator rejected it. Do NOT re-propose any of "
        "{model, provider} this round. They are operator-fixed at the dataset "
        "overlay; mutating them is impossible by policy. Route the budget to "
        "prompt-field or other param axes instead."
    ),
}


# Built-in worked examples — one per trigger that benefits from a concrete
# REJECTED/ACCEPTED pair. Shipped here so a fresh cycle sees the example the
# moment its trigger fires, without L2 having to author one. L2 may add more
# via :class:`L1SituationalExample` and may also override these by pinning a
# new example to the same trigger_id (L2's entry wins on duplicate trigger_id).
BUILTIN_EXAMPLES: dict[str, dict[str, str]] = {
    "peaked_axis": {
        "parent_excerpt": (
            "axis_rankings: llm_only.temperature (effect=0.245, PEAKED — do not "
            "mutate without sibling_yield>0 or exploration_budget=wide rebut)"
        ),
        "rejected": (
            "target_axis=temperature, pipeline_params_override={llm_only:{temperature:0.5}}, "
            "evidence={field:axis_memory, citation:'temperature effect=0.245'} — "
            "cites the effect number but ignores the PEAKED tag on the same row"
        ),
        "accepted": (
            "target_axis=persona, prompt_fields_override={persona:'...'}, "
            "evidence={field:critique, citation:'priority_fix: sharpen verification step'} — "
            "different axis, no peaked conflict"
        ),
        "why": "Citing the effect number on a PEAKED axis without a rebut is rejected by evidence_grounding_present.",
    },
    "latex_repair": {
        "parent_excerpt": "parent instruction contains: 'End with oxed{N}.'",
        "rejected": (
            "prompt_fields_override.instruction='End with oxed{N}. Verify your answer.' — "
            "preserves the corruption; the mutation propagates a broken token"
        ),
        "accepted": (
            "prompt_fields_override.instruction='End with \\boxed{N}. Verify your answer.' — "
            "repairs the corruption before extending the field"
        ),
        "why": "Corrupted LaTeX bytes get rendered literally; repair before reuse.",
    },
    "chain_bind": {
        "parent_excerpt": (
            "task_context.key_challenges: 'Coordinate geometry: 3/5 misses on parabola "
            "rotation, 3x9 grid. targeting L1 axis 'persona' next round to add "
            "coordinate-setup discipline.'"
        ),
        "rejected": (
            "Three variants targeting answer_format, thinking_style, instruction. "
            "No variant targets persona; the chain-bind directive is unaddressed."
        ),
        "accepted": (
            "One variant target_axis=persona, evidence={field:task_context, "
            "citation:'Coordinate geometry: 3/5 misses on parabola rotation, 3x9 grid'}. "
            "Other variants explore different axes."
        ),
        "why": "Skipping the task_context directive when one is present counts as wasted budget — the slot was meant to test what L2 just framed.",
    },
}


__all__ = ["AUTO_RULES", "BUILTIN_EXAMPLES"]
