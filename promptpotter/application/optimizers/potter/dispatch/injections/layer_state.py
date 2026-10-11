from __future__ import annotations

import json
import logging

from promptpotter.application.optimizers.potter.dispatch.bundle import (
    L3_PLAN_MAX_CHARS,
    InjectionBundle,
    InjectionKind,
    Item,
    signal,
)
from promptpotter.application.optimizers.potter.dispatch.layout import (
    L1_LAYOUT_SLOTS,
    NODE_LAYOUTS,
    resolve_node_layout,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    effective_optimizer_prompts,
)
from promptpotter.application.optimizers.potter.escalation.state import ExplorationBudget
from promptpotter.application.views.render.optimizer_prompt_text import (
    critique_axes,
    format_l1_critique_for_prompt,
)
from promptpotter.domain.connector import unit_plural
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.run_records import MAX_AUTO_REBASES
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS
from promptpotter.domain.value_tree import visibility_of

logger = logging.getLogger(__name__)


_PLAN_HEADER = "PLAN:\n"


@signal(
    "plan",
    kind=InjectionKind.TRACE,
    # Without the header's width the cap re-cuts a legal full-length plan UNMARKED.
    char_cap=L3_PLAN_MAX_CHARS + len(_PLAN_HEADER),
    citable=True,
)
def _r_plan(b: InjectionBundle) -> list[Item]:
    plan = b.memory.plan
    return [Item(f"{_PLAN_HEADER}{plan}" if plan else "")]


@signal(
    "l3_to_l2_note",
    kind=InjectionKind.DIRECTIVE,
    char_cap=400,
    citable=False,
)
def _r_l3_to_l2_note(b: InjectionBundle) -> list[Item]:
    note = b.memory.wounds.l3_note
    return [Item(f"L3 NOTE TO L2:\n{note}" if note else "")]


_TARGET_PROMPT_HEADER = (
    "CURRENT PROMPT — the text an override REPLACES, field by field. These sections "
    "concatenate VERBATIM in this order to form the prompt, with the operator's upstream and "
    "downstream framing spliced around problem_description and the output schema's field "
    "descriptions sent beside it; a field you do not name is carried "
    "forward unchanged, so restating one field's text — or that framing — inside another ships "
    "it twice."
)


# Public: the behaviour scores read a round's held fields back off the prompt it rendered.
HELD_PROMPT_FIELD_MARK = " — held by the operator, not yours to replace"


_OPTIMIZER_PROMPT_HEADER = (
    "CURRENT INNER OPTIMIZER PROMPTS — the text an override REPLACES, field by field.\n"
    "Text in doubled curly braces is an injection slot the inner loop fills; a replacement "
    "that drops one severs that channel and is rejected."
)


@signal(
    "rendered_prompt",
    kind=InjectionKind.TRACE,
    # A runaway backstop, NOT a budget knob: a WHOLE-field replacement must never see it truncated.
    char_cap=16000,
    citable=False,
)
def _r_rendered_prompt(b: InjectionBundle) -> list[Item]:
    sections: list[str] = []
    # Field by field: as one blob a replacement sweeps in neighbours, SPLICED it absorbs the framing.
    if fields := b.opt_sp.render_fields():
        # A HELD field still renders: the fields replaced must fit around it.
        schema = b.pipeline_schema
        held = (
            set(PROMPT_STRING_FIELDS) - set(schema.open_prompt_fields())
            if schema is not None and schema.prompt_node_names()
            else set()
        )
        sections.append(_TARGET_PROMPT_HEADER)
        sections.extend(
            f"[{field}{HELD_PROMPT_FIELD_MARK if field in held else ''}]\n{text}"
            for field, text in fields
        )
    inner = effective_optimizer_prompts(
        b.pipeline_schema, b.cycle_slice.pipeline_params, b.inner_optimizer
    )
    if inner:
        sections.append(_OPTIMIZER_PROMPT_HEADER)
        # One section per node·field, so the cap drops whole fields, never one mid-contract.
        sections.extend(
            f"[{node}.{field}]\n{text or '(empty — nothing to carry forward)'}"
            for node, fields in inner.items()
            for field, text in fields.items()
        )
    return [Item("\n\n".join(sections))]


@signal(
    "l1_overrides",
    kind=InjectionKind.TRACE,
    char_cap=None,
    citable=False,
)
def _r_l1_overrides(b: InjectionBundle) -> list[Item]:
    overrides = b.memory.steered("l1_generate")
    return [Item(f"CURRENT L1 CONFIG: {json.dumps(overrides)}" if overrides else "")]


@signal(
    "l1_layout",
    kind=InjectionKind.TRACE,
    char_cap=None,
    citable=False,
)
def _r_l1_layout(b: InjectionBundle) -> list[Item]:
    """Listed panel → slot, as an edit is keyed: shown slot → panels, the answer comes back slot-keyed and is refused."""
    steered = b.memory.steered_layout("l1_generate")
    layout = resolve_node_layout("l1_generate") if steered is None else steered
    lines = [
        f'  "{panel}": "{slot}"'
        for slot in L1_LAYOUT_SLOTS
        for panel in layout.slot(slot)
        if panel not in b.silent_l1_panels
    ]
    offered = NODE_LAYOUTS["l1_generate"].possible - b.silent_l1_panels
    if unplaced := sorted(offered - set(layout.all_placeholders())):
        lines.append(f"  in no slot, available to place: {', '.join(unplaced)}")
    return [
        Item(
            "CURRENT L1 LAYOUT — each panel l1_generate reads today, and the slot it fills:\n"
            + "\n".join(lines)
        )
    ]


@signal(
    "task_context",
    kind=InjectionKind.TRACE,
    # Never truncated: a renderer cannot know which half of an authored sentence matters.
    char_cap=None,
    citable=True,
)
def _r_task_context(b: InjectionBundle) -> list[Item]:
    tc = b.framing
    if not tc:
        return []
    skip = {"raw_description"}
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k not in skip]
    if not pairs:
        return []
    return [Item("TASK CONTEXT:\n" + "\n".join(f"  {k}: {v}" for k, v in pairs))]


@signal(
    "critique",
    kind=InjectionKind.TRACE,
    # The distiller's whole output quota: failure_highlights <=3x320c + priority_fix 320c + axes.
    char_cap=2000,
    citable=True,
)
def _r_critique(b: InjectionBundle) -> list[Item]:
    schema = b.pipeline_schema
    axes = None if schema is None else critique_axes(schema, offers_shots=b.offers_shots)
    return [Item(format_l1_critique_for_prompt(b.digest.critique, axes))]


_SKILL_TIERS_TEXT = (
    "SKILL TARGET — the candidate is the body of an agent skill (a SKILL.md). Work on the first "
    "tier the data shows failing:\n"
    "  1 VALID — under 500 lines, and no instruction to leak secrets, disable checks or run "
    "unvetted downloads.\n"
    "  2 NO REPEATS — merge or delete guidance it restates, never append another version.\n"
    "  3 LIFT — delete a line whose removal would not lower the score, rather than reword it."
)


@signal(
    "skill_tiers",
    kind=InjectionKind.DIRECTIVE,
    char_cap=None,
    citable=False,
)
def _r_skill_tiers(b: InjectionBundle) -> list[Item]:
    if visibility_of(b.prompt_delivery) != "on_demand":
        return []
    return [Item(_SKILL_TIERS_TEXT)]


_REBASE_CAPABILITY_TEXT = (
    "FORK PROPOSAL (rare escape hatch). If the current subtree is genuinely "
    "exhausted — multiple stall rounds in this lineage with no lift, and refining "
    "this trajectory cannot recover — you may emit "
    'fork_proposal = {"reason": "<1-2 sentences>"}. '
    "You judge WHETHER to rewind, not where to: the engine selects the ancestor "
    "round by UCB over the lineage statistics (each ancestor's mean ability against "
    "how little it has been explored), then mints a sibling cycle there and "
    f"auto-continues optimization. Capped at {MAX_AUTO_REBASES} rebases per session. "
    "Default: omit — a fork costs a whole cycle."
)

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
def _r_rebase_capability(b: InjectionBundle) -> list[Item]:
    if not b.rebase_capability or b.cycle_slice.exploration_budget == ExplorationBudget.TIGHT:
        return []
    schema = b.pipeline_schema
    unlockable = (
        not b.schema_field_rename
        and schema is not None
        and any(SCHEMA_RENAME_PARAM in n.param_keys for n in schema.nodes)
    )
    return [Item(_REBASE_CAPABILITY_TEXT + (_SCHEMA_RENAME_UNLOCK_TEXT if unlockable else ""))]


_TERMINATE_CAPABILITY_TEXT = (
    "TERMINATE PROPOSAL — stop the whole cycle by emitting terminate_proposal = "
    '{"reason": "<1-2 sentences naming what the operator must fix>"}; the cycle halts, the '
    "operator fixes it and resumes. Rare: use it ONLY for a fault no framing nudge or replan "
    "can recover — an unrecoverable upstream/backend fault — never for a hard task or a "
    "stalled-but-healthy search (rewind or keep refining for those). Default: omit."
)

_TERMINATE_STARVED_TEXT = (
    " THIS ROUND, EVALUATE IT FIRST, BEFORE ANY REFINEMENT: '{node}' failed across ~all of this "
    "round's {unit} (a backend quota or rate-limit exhausted), so the measurement itself is "
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
def _r_terminate_capability(b: InjectionBundle) -> list[Item]:
    if not b.terminate_capability:
        return []
    starved = evidence_starved_node(b.digest.node_failure_rates)
    return [
        Item(
            _TERMINATE_CAPABILITY_TEXT
            + (
                _TERMINATE_STARVED_TEXT.format(node=starved, unit=unit_plural(b.measured_unit))
                if starved
                else ""
            )
        )
    ]
