from __future__ import annotations

import json
from collections.abc import Collection, Sequence

from promptpotter.application.bench.node_context import Calls
from promptpotter.application.optimizers.potter.dispatch.bundle import (
    CRITIQUE_MAX_CHARS,
    L3_NOTE_MAX_CHARS,
    L3_PLAN_MAX_CHARS,
    ElectionRow,
    InjectionKind,
    Item,
    election_table_rows,
    offers_param,
    signal,
)
from promptpotter.application.optimizers.potter.dispatch.facade import PotterView
from promptpotter.application.optimizers.potter.dispatch.layout import (
    L1_LAYOUT_REGIONS,
    NODE_LAYOUTS,
    VOLATILE_REGION,
)
from promptpotter.application.optimizers.potter.dispatch.prompts import node_layout
from promptpotter.application.optimizers.potter.dispatch.schemas import CLUSTERS_MAX
from promptpotter.domain.arms import Measured, arm_name
from promptpotter.domain.connector import unit_plural
from promptpotter.domain.opt_search_point import Locus
from promptpotter.domain.optimizer_state import CritiqueReadout
from promptpotter.domain.paired_reading import ArmKey, level_text
from promptpotter.domain.pipeline_schema import SCHEMA_RENAME_PARAM
from promptpotter.domain.results import RoundOutcome
from promptpotter.domain.sample import sample_tag
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.value_tree import visibility_of

_PLAN_HEADER = "PLAN:\n"


@signal(
    "plan",
    kind=InjectionKind.TRACE,
    # Without the header's width the cap re-cuts a legal full-length plan UNMARKED.
    char_cap=L3_PLAN_MAX_CHARS + len(_PLAN_HEADER),
    citable=True,
)
def _r_plan(b: PotterView) -> list[Item]:
    plan = b.l3.plan
    return [Item(f"{_PLAN_HEADER}{plan}" if plan else "")]


_NOTE_HEADER = "L3 NOTE TO L2:\n"


@signal(
    "l3_to_l2_note",
    kind=InjectionKind.DIRECTIVE,
    char_cap=L3_NOTE_MAX_CHARS + len(_NOTE_HEADER),
    citable=False,
)
def _r_l3_to_l2_note(b: PotterView) -> list[Item]:
    note = b.l3.note
    return [Item(f"{_NOTE_HEADER}{note}" if note else "")]


_TARGET_PROMPT_HEADER = (
    "CURRENT PROMPTS — each node's fields, concatenated verbatim in this order; the operator's "
    "upstream/downstream framing is spliced around problem_description."
)


# Public: the behaviour scores read a round's held fields back off the prompt it rendered.
HELD_PROMPT_FIELD_MARK = " — held, not yours to replace"


@signal(
    "rendered_prompt",
    kind=InjectionKind.TRACE,
    # Never cut: an edit anchors on its text, and `L1GenerateKnobs.length_budget` bounds its growth.
    char_cap=None,
    citable=False,
)
def _r_rendered_prompt(b: PotterView) -> list[Item]:
    sections: list[str] = []
    # Field by field: as one blob a replacement sweeps in neighbours, SPLICED it absorbs the framing.
    # A HELD field still renders: the fields replaced must fit around it.
    prompts = b.parent.prompts
    fields = [
        (Locus(node.name, f).name, text, f not in node.open_prompt_fields)
        for node in b.prompt_nodes
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
    return [Item("\n\n".join(sections))]


@signal(
    "l1_overrides",
    kind=InjectionKind.TRACE,
    char_cap=None,
    citable=False,
)
def _r_l1_overrides(b: PotterView) -> list[Item]:
    overrides = b.l2.steered("l1_generate")
    return [Item(f"CURRENT L1 CONFIG: {json.dumps(overrides)}" if overrides else "")]


@signal(
    "l1_layout",
    kind=InjectionKind.TRACE,
    char_cap=None,
    citable=False,
)
def _r_l1_layout(b: PotterView) -> list[Item]:
    """Listed region → panels, the shape an edit takes, so each line is an edit ready to answer."""
    layout = node_layout("l1_generate", b.l2, b.ctx.read(Calls).levers("l1_generate"))
    lines = [
        f'  "{region}": {json.dumps([p for p in layout.region(region) if p not in b.silent_l1_panels])}'
        for region in L1_LAYOUT_REGIONS
    ]
    offered = NODE_LAYOUTS["l1_generate"].possible - b.silent_l1_panels
    if unplaced := sorted(offered - set(layout.all_placeholders())):
        lines.append(
            f"  in no slot; to place one, add it to a slot's list — {VOLATILE_REGION!r} unless the "
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
def _r_task_context(b: PotterView) -> list[Item]:
    tc = b.framing
    if not tc:
        return []
    # The decomposed fields, then the description they were decomposed from (`task.md`'s body).
    pairs = [(k, v) for k, v in tc.to_dict().items() if v and k != "raw_description"]
    body = tc.raw_description.strip()
    if not pairs and not body:
        return []
    lines = ["TASK CONTEXT:", *(f"  {k}: {v}" for k, v in pairs)]
    return [Item("\n".join(lines) + (f"\n\n{body}" if body else ""))]


# What bounds the critique panel: a cluster's size is its count, and a target needs only two of its rows.
CLUSTER_IDS_SHOWN = 12


def critique_axes(b: PotterView) -> tuple[str, ...]:
    """The one axis vocabulary: the critique names from it, and its panel keeps only what it names."""
    schema = b.pipeline_schema
    out: list[str] = []
    for node in schema.prompt_nodes():
        out.extend(Locus(node.name, f).name for f in node.open_prompt_fields)
        if b.offers_shots:
            out.append(Locus(node.name, SHOTS_KEY).name)
    for node in schema.running_nodes:
        out.extend(
            f"{node.name}.{pk}" if node.name else pk
            for pk in sorted(node.param_keys)
            if offers_param(schema, node, pk, schema_field_rename=b.schema_field_rename)
        )
    return tuple(dict.fromkeys(out))


@signal(
    "critique_axes",
    kind=InjectionKind.DIRECTIVE,
    char_cap=None,
    citable=False,
)
def _r_critique_axes(b: PotterView) -> list[Item]:
    return [Item(", ".join(critique_axes(b)))]


def format_l1_critique_for_prompt(
    critique: CritiqueReadout | None, axes: Collection[str] | None = None
) -> str:
    if not critique:
        return ""
    parts: list[str] = []
    pf = critique.get("priority_fix") or ""
    if pf:
        parts.append(f"Fix: {pf}")
    sa = list(critique.get("suggested_axes") or [])
    # The steer's own axis leads its menu, so `Fix:` and `Axes:` never name different things.
    head, colon, _ = pf.partition(":")
    lead = head.strip()
    if colon and all(part.isidentifier() for part in lead.split(".")):
        sa = [lead, *(a for a in sa if a != lead)]
    if axes is not None:
        sa = [a for a in sa if a in axes]
    if sa:
        parts.append(f"Axes: {', '.join(sa)}")
    if fh := critique.get("failure_highlights"):
        parts.append(
            "Clusters (target ONE by its rows; the edit must hold for every row it spans):"
        )
        for c in fh[:CLUSTERS_MAX]:
            ids = c["sample_ids"]
            # The panels' own mark, so a row this panel names is a row the generator may target.
            shown = " ".join(f"[{sample_tag(i)}]" for i in ids[:CLUSTER_IDS_SHOWN])
            more = f" +{len(ids) - CLUSTER_IDS_SHOWN}" if len(ids) > CLUSTER_IDS_SHOWN else ""
            parts.append(f"  {c['mechanism']} ({len(ids)} rows: {shown}{more}) — {c['claim']}")
    if not parts:
        return ""
    # `critique` is in the citation enum, and a variant grounding on it cites this heading.
    return "\n".join(["CRITIQUE (last round's failures, distilled):", *parts])


@signal(
    "critique",
    kind=InjectionKind.TRACE,
    char_cap=CRITIQUE_MAX_CHARS,
    citable=True,
)
def _r_critique(b: PotterView) -> list[Item]:
    return [Item(format_l1_critique_for_prompt(b.critique, critique_axes(b)))]


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
def _r_skill_tiers(b: PotterView) -> list[Item]:
    if visibility_of(b.prompt_delivery) != "on_demand":
        return []
    return [Item(_SKILL_TIERS_TEXT)]


_REBASE_CAPABILITY_TEXT = (
    'FORK PROPOSAL — fork_proposal = {"reason": "<1-2 sentences>"} only when this lineage is '
    "exhausted (stalled, refining cannot recover); you judge WHETHER, the engine picks the "
    "individual it stands on and keeps this layer's memory. A fork changing nothing that only "
    "rewinds to where the line stands, or to the origin, is refused; its rounds count against "
    "the same budget. Default omit."
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
def _r_rebase_capability(b: PotterView) -> list[Item]:
    if not b.rebase_offered:
        return []
    unlockable = not b.schema_field_rename and any(
        SCHEMA_RENAME_PARAM in n.param_keys for n in b.pipeline_schema.nodes
    )
    directive = _REBASE_CAPABILITY_TEXT + (_SCHEMA_RENAME_UNLOCK_TEXT if unlockable else "")
    if not b.reelect_offer:
        return [Item(directive)]
    # The one carrier of the labels `fork_proposal.stand_on` names, so it renders only beside the field.
    rounds = b.measured_rounds
    lines = [_election_line(row, rounds) for row in election_table_rows(rounds)]
    return [Item(directive + _STAND_ON_TEXT), Item("\n".join([_ELECTIONS_HEADER, *lines]))]


_STAND_ON_TEXT = (
    " It may set stand_on = an arm label from ELECTIONS: the fork then continues from that arm, "
    "its round re-elected to it, instead of where the engine would stand it — only where that "
    "arm's cells contradict the pick that stands."
)

_ELECTIONS_HEADER = (
    "ELECTIONS — each round's pick and how it won (locked in on part of its panel, or on the full "
    "panel), else the leader the line moved on to uncrowned, its reads as the parent in the "
    "rounds after, and the arms it beat; fitness on cells read."
)


def _read(rr: RoundOutcome, key: ArmKey) -> str:
    fold = rr.folds.get(key)
    if fold is None or fold.fitness is None or not fold.total:
        return "unscored"
    return f"{level_text(fold.fitness, fold.fitness_unit)} on {fold.total}"


def _how_won(rr: RoundOutcome, key: ArmKey) -> str:
    arm = rr.arms[key]
    if isinstance(arm, Measured) and arm.locked_in is not None:
        fold = rr.folds.get(key)
        walked = 0 if fold is None else fold.walked
        return f"locked in at {walked} of {arm.expected_samples} cells, {_read(rr, key)}"
    return f"on the full panel, {_read(rr, key)}"


def _election_line(row: ElectionRow, later: Sequence[RoundOutcome]) -> str:
    rr = row.round
    label = {key: arm_name(key, arm, rr.round).label for key, arm in rr.arms.items()}
    if row.picked is not None:
        parts = [f"r{rr.round}: elected {label[row.picked]}, {_how_won(rr, row.picked)}"]
    elif row.moved_to is not None:
        parts = [
            f"r{rr.round}: none elected; moved on to {label[row.moved_to]} uncrowned, "
            f"{_how_won(rr, row.moved_to)}"
        ]
    else:
        ref = None if rr.election is None else rr.election.parent
        held = f" (the parent read {_read(rr, ref.arm)})" if ref and ref.arm in rr.arms else ""
        parts = [f"r{rr.round}: none elected{held}"]
    if (stood := row.stood_on) is not None:
        reads = [
            f"r{s.round} {_read(s, stood)}"
            for s in later
            if s.round > rr.round
            and s.election is not None
            and s.election.parent is not None
            and s.election.parent.arm == stood
        ]
        if reads:
            parts.append("as parent: " + ", ".join(reads))
    if row.unelected:
        parts.append("unelected: " + ", ".join(f"{label[k]} {_read(rr, k)}" for k in row.unelected))
    return "  " + " · ".join(parts)


# Offered only where the round measured a backend fault (`PotterView.terminate_offer`): a layer
# shown it on a healthy round reads rows a prompt edit did not move as a broken backend, and aborts.
_TERMINATE_CAPABILITY_TEXT = (
    'TERMINATE PROPOSAL — terminate_proposal = {{"reason": "<what the operator must fix>"}} halts '
    "the cycle. THIS ROUND IT APPLIES: '{node}' failed on ~all {unit} (quota or rate limit), so "
    "every failure cluster is cascade noise from it — terminate now, naming that node."
)


@signal(
    "terminate_capability",
    kind=InjectionKind.DIRECTIVE,
    char_cap=None,
    citable=False,
)
def _r_terminate_capability(b: PotterView) -> list[Item]:
    if (starved := b.terminate_offer) is None:
        return []
    return [
        Item(_TERMINATE_CAPABILITY_TEXT.format(node=starved, unit=unit_plural(b.measured_unit)))
    ]
