"""Narrative-state renderers — persistent state from prior LLM calls
(L3 plan, L3→L2 note, current prompt, L1 critique) plus the operator's frozen task framing.
"""

from __future__ import annotations

import json
import logging

from promptpotter.application.optimization.dispatch.bundle import (
    InjectionBundle,
    InjectionKind,
    signal,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    effective_optimizer_prompts,
)
from promptpotter.domain.escalation_signals import ExplorationBudget
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results_health import evidence_starved_node

logger = logging.getLogger(__name__)


@signal(
    "plan",
    kind=InjectionKind.TRACE,
    # A RAIL, and it AGREES with the bound at production (`L3PlanOutput.plan`, same number).
    # A rail above that could never fire; one below would re-cut a plan already declared legal.
    char_cap=800,
    citable=True,
)
def _r_plan(b: InjectionBundle) -> str:
    """L3's strategic plan text — read by every prompt; persistent until next L3 fire."""
    plan = b.opt_sp.plan
    return f"PLAN:\n{plan}" if plan else ""


@signal(
    "l3_to_l2_note",
    kind=InjectionKind.DIRECTIVE,
    char_cap=400,
    citable=False,
)
def _r_l3_to_l2_note(b: InjectionBundle) -> str:
    """Sticky L3→L2 directive — mounted only in L2's template, absent from L1."""
    note = b.opt_sp.memory.wounds.l3_note
    return f"L3 NOTE TO L2:\n{note}" if note else ""


_OPTIMIZER_PROMPT_HEADER = (
    "CURRENT INNER OPTIMIZER PROMPTS — the text an override REPLACES, field by field.\n"
    "Text in doubled curly braces is an injection slot the inner loop fills; a replacement "
    "that drops one severs that channel and is rejected."
)


@signal(
    "rendered_prompt",
    kind=InjectionKind.TRACE,
    # The cap is a runaway backstop, NOT a budget knob — this is the exact prompt
    # under edit, so it must never arrive truncated (a cut-off prompt makes the
    # generator mis-edit or hallucinate the missing tail, and every mutation here is
    # a WHOLE-field replacement). Sized above the recursion's own bundle, which is an
    # order of magnitude past a single evolved target prompt: the sixteen editable
    # optimizer prompt fields measure ~10k at the origin. Only true runaway trips.
    char_cap=16000,
    # The prompt under edit is the SUBJECT of a mutation, never its evidence.
    citable=False,
)
def _r_rendered_prompt(b: InjectionBundle) -> str:
    """The artifact under edit — a target prompt, inner optimizer prompts, or both.

    Two halves, each empty where it is not the mutation surface, so there is no branch
    and no second panel. A normal campaign evolves the ``OptSearchPoint`` and renders
    its body; an L4 cycle's outer point is inert (its fields reach no node) while the
    real levers ride ``pipeline_params`` — which is what left this MANDATORY panel
    rendering nothing at all on the recursion, and the generator rewriting fields it
    had never been shown.
    """
    sections: list[str] = []
    if body := b.opt_sp.render():
        sections.append(f"CURRENT PROMPT:\n---\n{body}\n---")
    inner = effective_optimizer_prompts(b.pipeline_schema, b.cycle_slice.pipeline_params)
    if inner:
        sections.append(_OPTIMIZER_PROMPT_HEADER)
        # One section per node·field: the cap's truncation drops whole tail sections,
        # so a runaway costs whole fields rather than slicing one mid-contract.
        sections.extend(
            f"[{node}.{field}]\n{text or '(empty — nothing to carry forward)'}"
            for node, fields in inner.items()
            for field, text in fields.items()
        )
    return "\n\n".join(sections)


@signal(
    "l1_overrides",
    kind=InjectionKind.TRACE,
    char_cap=None,
    citable=False,
)
def _r_l1_overrides(b: InjectionBundle) -> str:
    """Current L1 runtime knobs (creativity, n_variants, …) as JSON."""
    overrides = b.opt_sp.memory.l1_overrides
    return f"CURRENT L1 CONFIG: {json.dumps(overrides)}" if overrides else ""


@signal(
    "task_context",
    kind=InjectionKind.TRACE,
    char_cap=None,  # verbatim BY CONTRACT — see below
    # Citable because the operator's framing is real evidence a variant can be grounded in
    # ("the framing records that anti-hedging backfires here"). It is NOT citable because
    # anything instructs L1 to cite it: the CHAIN-BIND rule that once did was deleted with
    # the channel it named — task_context is frozen and never carries an axis directive.
    citable=True,
)
def _r_task_context(b: InjectionBundle) -> str:
    """The operator's framing, rendered VERBATIM — this panel never truncates.

    A renderer cannot know which half of an authored sentence matters, so it never guesses:
    the budget is enforced where the text is written (`TaskDecomposition.check_budget`, at
    mint), where a human can actually fix it. Truncation stays legitimate for the DERIVED
    panels, which rank their rows and say what they dropped.
    """
    tc = b.opt_sp.memory.task_context
    if not tc:
        return ""
    skip = {"raw_description", "upstream_context", "downstream_context"}
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k not in skip]
    if not pairs:
        return ""
    return "TASK CONTEXT:\n" + "\n".join(f"  {k}: {v}" for k, v in pairs)


@signal(
    "critique",
    kind=InjectionKind.TRACE,
    # Sized for failure_highlights <=3x320c + priority_fix 320c + axes — the
    # distiller's whole output quota; an 800 cap silently re-truncated it.
    char_cap=2000,
    citable=True,
)
def _r_critique(b: InjectionBundle) -> str:
    """Compact view of the most recent L1_CRITIQUE output dict."""
    return format_l1_critique_for_prompt(b.digest.critique, b.pipeline_schema)


_REBASE_CAPABILITY_TEXT = (
    "FORK PROPOSAL (rare escape hatch). If the current subtree is genuinely "
    "exhausted — multiple stall rounds in this lineage with no lift, and refining "
    "this trajectory cannot recover — you may emit "
    'fork_proposal = {"reason": "<1-2 sentences>"}. '
    "You judge WHETHER to rewind, not where to: the engine selects the ancestor "
    "round by UCB over the lineage statistics (each ancestor's mean ability against "
    "how little it has been explored), then mints a sibling cycle there and "
    "auto-continues optimization. Capped at 10 rebases per session. Default: omit — "
    "a fork costs a whole cycle."
)

# Rendered only where the unlock would change something — see `_r_rebase_capability`.
_SCHEMA_RENAME_UNLOCK_TEXT = (
    " On that same fork_proposal you may set unlock_schema_field_rename = true, which "
    "lets the fork's L1 RENAME a field on the optimizer's own output schema (today it "
    "may only rewrite each field's description). The name is the strongest lever — the "
    "model has priors about what belongs under a key, so the name steers before a single "
    "token of the value is written — and it is the only one that can break the parser. "
    "Request it ONLY when the panels show the search stalling on what a field is FOR "
    "rather than on what it says: a field whose name misdescribes the content it should "
    "hold. Otherwise omit it — describe the field, do not rename it."
)


@signal(
    "rebase_capability",
    kind=InjectionKind.DIRECTIVE,
    char_cap=None,
    citable=False,
)
def _r_rebase_capability(b: InjectionBundle) -> str:
    """Render the fork_proposal escape-hatch instruction, gated by
    ``OptimizationConfig.rebase_capability``. When the capability is off
    this returns the empty string so the L2/L3 prompt body is bit-for-bit
    identical to a no-rebase ablation run.

    The rename-unlock clause rides this same directive rather than a signal of its
    own: it is one more sentence about the same emitted object, and a second
    injection would render an empty line into every prompt that lacks the lever.
    It appears only when the unlock is both *reachable* — some node DECLARES the
    rename param, which only an optimizer-of-the-optimizer dataset does — and *not
    already open*. Reachability is read off the declaration, never off a node name:
    teaching a lever the campaign cannot pull is the same defect as a citable panel
    that never renders, and hardcoding ``"l1_generate"`` here would re-file the
    knowledge of the target that ``node_param_keys`` exists to hold.

    Gated on ``exploration_budget`` for the same reason, one level up: the block's own text
    conditions the lever on stalled rounds with no lift, and that is already measured — the
    SAME threshold ``citable_fields`` uses to license ``stall_exploration``. At ``tight`` the
    round has no grounds to fork, so the block taught against its own evidence."""
    if not b.rebase_capability or b.cycle_slice.exploration_budget == ExplorationBudget.TIGHT:
        return ""
    schema = b.pipeline_schema
    unlockable = (
        not b.schema_field_rename
        and schema is not None
        and any(SCHEMA_RENAME_PARAM in n.param_keys for n in schema.nodes)
    )
    return _REBASE_CAPABILITY_TEXT + (_SCHEMA_RENAME_UNLOCK_TEXT if unlockable else "")


_TERMINATE_CAPABILITY_TEXT = (
    "TERMINATE PROPOSAL — stop the whole cycle by emitting terminate_proposal = "
    '{"reason": "<1-2 sentences naming what the operator must fix>"}; the cycle halts, the '
    "operator fixes it and resumes. Rare: use it ONLY for a fault no framing nudge or replan "
    "can recover — an unrecoverable upstream/backend fault — never for a hard task or a "
    "stalled-but-healthy search (rewind or keep refining for those). Default: omit."
)

# The starvation coaching, rendered only in the round it describes — see `_r_terminate_capability`.
_TERMINATE_STARVED_TEXT = (
    " THIS ROUND, EVALUATE IT FIRST, BEFORE ANY REFINEMENT: '{node}' failed across ~all of this "
    "round's samples (a backend quota or rate-limit exhausted), so the measurement itself is "
    "unreliable and this round's failure clusters — critique.failure_highlights and the "
    "axis-memory cluster, often a downstream matcher — are CASCADE NOISE from that dead node, "
    "not real targets. Do NOT chase them with a refinement: no framing nudge recovers a starved "
    "backend, and another round just burns spend on noise. TERMINATE NOW, naming that node."
)


@signal(
    "terminate_capability",
    kind=InjectionKind.DIRECTIVE,
    char_cap=None,
    citable=False,
)
def _r_terminate_capability(b: InjectionBundle) -> str:
    """Render the terminate_proposal instruction, gated by
    ``OptimizationConfig.terminate_capability``. Off ⇒ empty string so the L2/L3
    prompt body is bit-for-bit identical to a no-terminate ablation run (R-48).

    The starvation coaching is gated a second time, on the round actually having a starved
    node — it used to point at "the EVIDENCE STARVED panel above", which renders nothing on a
    healthy round. The lever itself stays unconditional: starvation is its canonical first
    user, not its only one."""
    if not b.terminate_capability:
        return ""
    starved = evidence_starved_node(b.digest.node_failure_rates)
    return _TERMINATE_CAPABILITY_TEXT + (
        _TERMINATE_STARVED_TEXT.format(node=starved) if starved else ""
    )
