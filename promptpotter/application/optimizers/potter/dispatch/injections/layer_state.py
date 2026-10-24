from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

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
    VOLATILE_SLOT,
    resolve_node_layout,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import (
    effective_optimizer_prompts,
)
from promptpotter.application.optimizers.potter.escalation.state import ExplorationBudget
from promptpotter.domain.connector import unit_plural
from promptpotter.domain.opt_search_point import Locus
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM
from promptpotter.domain.results_health import evidence_starved_node
from promptpotter.domain.run_records import MAX_AUTO_REBASES
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.value_tree import visibility_of

if TYPE_CHECKING:
    from promptpotter.domain.optimizer_state import CritiqueReadout
    from promptpotter.domain.pipeline_schema import PipelineSchema

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
    plan = b.l3.plan
    return [Item(f"{_PLAN_HEADER}{plan}" if plan else "")]


@signal(
    "l3_to_l2_note",
    kind=InjectionKind.DIRECTIVE,
    char_cap=400,
    citable=False,
)
def _r_l3_to_l2_note(b: InjectionBundle) -> list[Item]:
    note = b.l3.note
    return [Item(f"L3 NOTE TO L2:\n{note}" if note else "")]


_TARGET_PROMPT_HEADER = (
    "CURRENT PROMPTS — each node's fields, concatenated verbatim in this order; the operator's "
    "upstream/downstream framing is spliced around problem_description."
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
    # A HELD field still renders: the fields replaced must fit around it.
    schema = b.pipeline_schema
    prompts = b.parent.prompts
    fields = [
        (Locus(node.name, f).name, text, f not in node.open_prompt_fields)
        for node in (schema.prompt_nodes() if schema is not None else ())
        if (prompt := prompts.get(node.name)) is not None
        for f in node.prompt_fields
        if (text := prompt.fields.get(f, ""))
    ]
    if fields:
        sections.append(_TARGET_PROMPT_HEADER)
        sections.extend(
            f"[{locus}{HELD_PROMPT_FIELD_MARK if held else ''}]\n{text}"
            for locus, text, held in fields
        )
    inner = effective_optimizer_prompts(
        b.pipeline_schema, b.cycle_slice.pipeline_params, b.inner_cells
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
    overrides = b.l2.steered("l1_generate")
    return [Item(f"CURRENT L1 CONFIG: {json.dumps(overrides)}" if overrides else "")]


@signal(
    "l1_layout",
    kind=InjectionKind.TRACE,
    char_cap=None,
    citable=False,
)
def _r_l1_layout(b: InjectionBundle) -> list[Item]:
    """Listed slot → panels, the shape an edit takes, so each line is an edit ready to answer."""
    steered = b.l2.steered_layout("l1_generate")
    layout = resolve_node_layout("l1_generate") if steered is None else steered
    lines = [
        f'  "{slot}": {json.dumps([p for p in layout.slot(slot) if p not in b.silent_l1_panels])}'
        for slot in L1_LAYOUT_SLOTS
    ]
    offered = NODE_LAYOUTS["l1_generate"].possible - b.silent_l1_panels
    if unplaced := sorted(offered - set(layout.all_placeholders())):
        lines.append(
            f"  in no slot; to place one, add it to a slot's list — {VOLATILE_SLOT!r} unless the "
            f"failures need it read earlier: {json.dumps(unplaced)}"
        )
    return [
        Item(
            "CURRENT L1 LAYOUT — each slot of l1_generate's OWN prompt, and the panels it reads "
            "there today:\n" + "\n".join(lines)
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


# What bounds the critique panel: a cluster's size is its count, and a target needs only two of its rows.
CLUSTER_IDS_SHOWN = 12


def critique_axes(schema: PipelineSchema, *, offers_shots: bool) -> frozenset[str]:
    out: set[str] = set()
    for node in schema.prompt_nodes():
        out.update(Locus(node.name, f).name for f in node.open_prompt_fields)
        if offers_shots:
            out.add(Locus(node.name, SHOTS_KEY).name)
    for node in schema.nodes:
        if node.name:
            out.add(node.name)
        for pk in node.param_keys:
            out.add(pk)
            if node.name:
                out.add(f"{node.name}.{pk}")
    return frozenset(out)


def _priority_fix_axis(priority_fix: str) -> str:
    head, sep, _ = priority_fix.partition(":")
    axis = head.strip()
    return axis if sep and axis.isidentifier() else ""


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, axes: frozenset[str] | None = None
) -> str:
    if not critique:
        return ""
    parts: list[str] = []
    pf = critique.get("priority_fix") or ""
    if pf:
        parts.append(f"Fix: {pf}")
    sa = list(critique.get("suggested_axes") or [])
    # The steer's own axis leads its menu, so `Fix:` and `Axes:` never name different things.
    if lead := _priority_fix_axis(pf):
        sa = [lead, *(a for a in sa if a != lead)]
    if axes is not None:
        sa = [a for a in sa if a in axes]
    if sa:
        parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append(
            "Clusters (target ONE by its rows; the edit must hold for every row it spans):"
        )
        for c in fh[:3]:
            ids = c["sample_ids"]
            # The panels' own mark, so a row this panel names is a row the generator may target.
            shown = " ".join(f"[#{i}]" for i in ids[:CLUSTER_IDS_SHOWN])
            more = f" +{len(ids) - CLUSTER_IDS_SHOWN}" if len(ids) > CLUSTER_IDS_SHOWN else ""
            parts.append(f"  {c['mechanism']} ({len(ids)} rows: {shown}{more}) — {c['claim']}")
    if not parts:
        return ""
    # `critique` is in the citation enum, and a variant grounding on it cites this heading.
    return "\n".join(["CRITIQUE (last round's failures, distilled):", *parts])


@signal(
    "critique",
    kind=InjectionKind.TRACE,
    # Above the distiller's whole quota: 3 clusters (label 60c, `CLUSTER_IDS_SHOWN` ids, claim 240c) + priority_fix 320c + axes.
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
    'FORK PROPOSAL — fork_proposal = {"reason": "<1-2 sentences>"} only when this lineage is '
    "exhausted (stalled, refining cannot recover); you judge WHETHER, the engine picks the "
    f"ancestor round. At most {MAX_AUTO_REBASES} per session; default omit — it costs a cycle."
)

_SCHEMA_RENAME_UNLOCK_TEXT = (
    " It may set unlock_schema_field_rename = true, letting the fork's L1 RENAME an output-schema "
    "field — only when the search stalls on what a field is FOR, not what it says."
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
    'TERMINATE PROPOSAL — terminate_proposal = {"reason": "<what the operator must fix>"} halts '
    "the cycle; only for a backend fault no prompt move recovers, never a hard task or a "
    "healthy stall. Default omit."
)

_TERMINATE_STARVED_TEXT = (
    " THIS ROUND IT APPLIES: '{node}' failed on ~all {unit} (quota or rate limit), so every "
    "failure cluster is cascade noise from it — terminate now, naming that node."
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
