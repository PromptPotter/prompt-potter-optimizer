"""Narrative-state renderers — persistent state from prior LLM calls
(L3 plan, L2 task_context + L3→L2 note, current prompt, L1 critique).
"""

from __future__ import annotations

import json
import logging

from promptpotter.application.optimization.dispatch.hub.bundle import (
    TASK_CONTEXT_VALUE_CAP,
    InjectionBundle,
    InjectionKind,
    signal,
)
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.infrastructure.llm.models import emit_round_warning

logger = logging.getLogger(__name__)


@signal(
    "plan",
    kind=InjectionKind.TRACE,
    description="L3's strategic plan text. Persistent until next L3 fire.",
    # A RAIL, not a budget the writer honours. Stating the char budget in l3_plan's
    # answer_format did not work and could not: a model cannot count the characters it is
    # emitting, so "<=2000 chars" was an instruction it had no way to comply with — live
    # plans arrived at ~3.2k and the rail silently dropped the strategy's back five
    # sections, on most rounds. The budget is now expressed where the writer CAN honour it
    # (at most 6 one-sentence bullets); this only catches a genuine runaway.
    char_cap=2000,
    citable=True,
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
    citable=False,
)
def _r_l3_to_l2_note(b: InjectionBundle) -> str:
    """Sticky L3→L2 directive — mounted only in L2's template, absent from L1."""
    note = b.opt_sp.memory.wounds.l3_note
    return f"L3 NOTE TO L2:\n{note}" if note else ""


@signal(
    "rendered_prompt",
    kind=InjectionKind.TRACE,
    description="Current best searchpoint's compiled prompt body.",
    # The cap is a runaway backstop, NOT a budget knob — this is the exact prompt
    # L1 is editing, so it must never arrive truncated (a cut-off prompt makes L1
    # mis-edit or hallucinate the missing tail). Sized above a fully-evolved
    # complex prompt (the 8-field scheme + situational rules for a hard task like
    # entity-linking, which overran the old 2500 at 2766); only true runaway trips.
    char_cap=8000,
    # The prompt under edit is the SUBJECT of a mutation, never its evidence.
    citable=False,
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
    citable=False,
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
    citable=True,  # the CHAIN-BIND rule requires citing it
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
    truncated: list[str] = []
    for k, v in pairs:
        if len(v) > TASK_CONTEXT_VALUE_CAP:
            truncated.append(f"{k}({len(v)}→{TASK_CONTEXT_VALUE_CAP})")
            v = v[: TASK_CONTEXT_VALUE_CAP - 1].rsplit(" ", 1)[0] + "…"
        lines.append(f"  {k}: {v}")
    if truncated:
        # L2-authored overrun — heal (truncate) but leave a DISK trace too, not just a log
        # line: silently dropping L2's framing violates "material facts land on disk".
        logger.warning(
            "task_context field(s) over the %d-char cap — truncating: %s",
            TASK_CONTEXT_VALUE_CAP,
            ", ".join(truncated),
        )
        emit_round_warning(
            kind="injection_budget_overrun",
            severity="warning",
            message=(
                f"L2-authored task_context field(s) exceeded the {TASK_CONTEXT_VALUE_CAP}-char "
                f"cap and were truncated: {', '.join(truncated)} — some framing didn't reach the LLM."
            ),
            detail={
                "injection": "task_context",
                "cap": TASK_CONTEXT_VALUE_CAP,
                "fields": truncated,
            },
        )
    return "TASK CONTEXT:\n" + "\n".join(lines)


@signal(
    "critique",
    kind=InjectionKind.TRACE,
    description="Compact view of the most recent L1_CRITIQUE LLM output dict.",
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
    description=(
        "Conditional fork_proposal escape-hatch instruction (renders into L2 + "
        "L3 prompts), plus the schema_field_rename unlock clause where that lever "
        "exists and is still locked. Empty when ``OptimizationConfig.rebase_capability`` "
        "is off — keeps prompt body bit-for-bit identical to a no-rebase "
        "ablation so the input distribution doesn't drift on prompt text."
    ),
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
    knowledge of the target that ``node_param_keys`` exists to hold."""
    if not b.rebase_capability:
        return ""
    schema = b.pipeline_schema
    unlockable = (
        not b.schema_field_rename
        and schema is not None
        and any(SCHEMA_RENAME_PARAM in n.param_keys for n in schema.nodes)
    )
    return _REBASE_CAPABILITY_TEXT + (_SCHEMA_RENAME_UNLOCK_TEXT if unlockable else "")


_TERMINATE_CAPABILITY_TEXT = (
    "TERMINATE PROPOSAL — stop the whole cycle. Evaluate this FIRST, before any refinement: if "
    "the EVIDENCE STARVED panel above names a node that failed across ~all of this round's "
    "samples (an enricher whose backend quota or rate-limit is exhausted), the measurement "
    "itself is unreliable and the round's failure clusters — including "
    "critique.failure_highlights and the axis-memory cluster, often a downstream matcher — are "
    "CASCADE NOISE from that dead node, not real targets. Do NOT chase them with a refinement "
    "first: no framing nudge recovers a starved backend, and another round just burns spend on "
    'noise. TERMINATE NOW — emit terminate_proposal = {"reason": "<1-2 sentences naming the '
    'dead node and what the operator must fix>"}; the cycle halts, the operator fixes the '
    "backend and resumes. For every OTHER fault terminate is rare — default omit; use it ONLY "
    "for an unrecoverable upstream/backend fault, never for a hard task or a stalled-but-healthy "
    "search (rewind or keep refining for those)."
)


@signal(
    "terminate_capability",
    kind=InjectionKind.DIRECTIVE,
    description=(
        "Conditional terminate_proposal instruction (renders into L2 + L3 prompts). "
        "Empty when ``OptimizationConfig.terminate_capability`` is off — keeps the "
        "prompt body bit-for-bit identical to an ablation run without it."
    ),
    char_cap=None,
    citable=False,
)
def _r_terminate_capability(b: InjectionBundle) -> str:
    """Render the terminate_proposal instruction, gated by
    ``OptimizationConfig.terminate_capability``. Off ⇒ empty string so the L2/L3
    prompt body is bit-for-bit identical to a no-terminate ablation run (R-48)."""
    if not b.terminate_capability:
        return ""
    return _TERMINATE_CAPABILITY_TEXT
