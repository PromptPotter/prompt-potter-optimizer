"""``Cycle``-free by contract: what a renderer reads is the node's own, through ``facade.py::PotterView``."""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.domain.arms import arm_name, is_leader_eligible
from promptpotter.domain.l4.cells import InnerCells
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.paired_reading import ArmKey, RoundArm
from promptpotter.domain.pipeline_schema import NodeSearchNarrowing, PipelineNode, PipelineSchema
from promptpotter.domain.results import RoundOutcome

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.dispatch.facade import PotterView


# A field with no entry is emitted unbounded.
OPTIMIZER_PROMPT_FIELD_MAX_CHARS: dict[str, int] = {
    "instruction": 3_200,
}

SCHEMA_DESCRIPTION_MAX_CHARS = 400


def prose_ceiling(node: PipelineNode, param: str, *, inner_cells: InnerCells | None) -> int | None:
    # An optimizer-prompt field's ceiling binds only the inner optimizer's prose, under L4.
    if param in node.description_keys:
        return SCHEMA_DESCRIPTION_MAX_CHARS
    return None if inner_cells is None else OPTIMIZER_PROMPT_FIELD_MAX_CHARS.get(param)


L3_PLAN_MAX_CHARS = 800

SCHEMA_DESCRIPTIONS_INSTRUCTION = (
    "Each `output_schema_descriptions.<path>` key rewrites the JSON-Schema "
    "`description` of that field on this node's OWN output schema. This prose sits "
    "adjacent to the slot it governs, inside the field-filling loop, so it steers the "
    "model harder per token than the instruction does. Paths are FIXED — you describe "
    "a field, you never rename or add one. Rewrite one your prompt edit contradicts, "
    "or that underspecifies what the field should hold."
)

SCHEMA_RENAME_INSTRUCTION = (
    "Rename a field on the inner optimizer's own output schema. The model holds "
    "strong priors about what belongs under a given key, so the name steers "
    "before a single token of the value is written. Keys are the existing field "
    "names; values are the new wire names. Rename only when the current name "
    "misdescribes what the field should hold — a rename the model then fails to "
    "honour makes the round unparseable and scores it maximally dirty."
)

LAYOUT_SCHEMA_INSTRUCTION = (
    "Where in the reader node's own prompt each evidence panel is rendered. A slot is a "
    "position in that prompt, never the target-prompt field the evidence is about: the "
    "reader gets every placed panel whichever slot holds it. Keyed by SLOT — the "
    "properties are the only slots there are — each listing the panels to place there: a "
    "listed panel MOVES to that slot, an omitted one stays where it is, and a panel is only "
    "ever in one place."
)


AXES_ENUM_PREVIEW = 4
NEAR_MISS_RENDER_CAP = 2
SAMPLE_RENDER_CAP = 2
TRANSCRIPT_RENDER_CAP = 3
TRANSCRIPT_QUERY_CAP = 1200
TRANSCRIPT_REASONING_CAP = 2200
TRANSCRIPT_PREDICTED_CAP = 60
# Compressed bytes per char a trace's second half adds to its first; below it the trace loops.
LOOP_NOVELTY = 0.05
DEGENERATE_TRACE_CAP = 600
INNER_NARRATIVE_CAP = 1150
MISS_QUERY_CAP = 100
MISS_PREDICTED_CAP = 60
MISS_GT_CAP = 40
MISS_NOTE_CAP = 120
MISS_STEP_CAP = 160
# The panel is mandatory on the critique, so this is what bounds its prompt on a dataset of any size.
MISS_ROWS_CAP = 20
# Rows on a δ the ruler hands several cells carry no order among themselves, so a few stand for all.
MISS_TIED_CAP = 5
MEMORY_ROUND_CAP = 4
MEMORY_FIELD_CAP = 2
MEMORY_VALUE_CAP = 90
# One loss is a noisy cell's chance flip; from here it is evidence about EDITING.
LOST_CELL_MIN = 2
NODE_FAILURE_RENDER_CAP = 3
RUNTIME_FAILURE_RECENCY_WINDOW = 6
VALIDATION_RENDER_CAP = 8
ANSWER_LABEL_STEM = 40
ANSWER_TALLY_ROWS = 5
DEMO_QUERY_STEM = 80
RECOMBINATION_VALUE_STEM = 160
# An id is shown, and answered, by its head, resolved over every individual the cycle measured.
RECOMBINATION_ID_CHARS = 8
BLOCK_ID_INSTRUCTION = (
    "Per prompt node, its pooled fields: {node: {field: the id of a block the PROMPT BLOCK POOL "
    "panel lists for that field}}."
)
# The scorer parses these, so an edit to one scores near 0 however good its reasoning.
L1_HELD_PROMPT_FIELDS: tuple[str, ...] = ("answer_format",)
# The ELECTIONS panel's reach: its latest rounds, and per round the challengers its pick beat.
ELECTION_ROUNDS_SHOWN = 8
UNELECTED_SHOWN = 2


class ElectionRow(NamedTuple):
    """One round's election as a layer that may re-elect reads it: the pick, and the best of the
    panel arms left unelected, in the round's own rank."""

    round: RoundOutcome
    picked: ArmKey | None
    unelected: tuple[ArmKey, ...]


def election_table_rows(rounds: Sequence[RoundOutcome]) -> list[ElectionRow]:
    rows: list[ElectionRow] = []
    for rr in rounds:
        if rr.round == 0 or rr.election is None:
            continue
        picked = rr.election.selected
        ranked = (row.reading.arm.key for row in rr.ranked())
        unelected = tuple(
            key
            for key in ranked
            if key != picked and key in rr.panel and is_leader_eligible(rr.panel[key])
        )
        rows.append(ElectionRow(rr, picked, unelected[:UNELECTED_SHOWN]))
    return rows[-ELECTION_ROUNDS_SHOWN:]


def reelection_roster(rounds: Sequence[RoundOutcome], *, parent: str) -> dict[str, RoundArm]:
    """By label, every arm the ELECTIONS panel lists, the one the line stands on now left out."""
    roster: dict[ArmKey, tuple[str, RoundArm]] = {}
    for row in election_table_rows(rounds):
        for key in ((row.picked,) if row.picked is not None else ()) + row.unelected:
            if key != parent and key not in roster:
                label = arm_name(key, row.round.arms[key], row.round.round).label
                roster[key] = (label, RoundArm(round=row.round.round, arm=key))
    return dict(roster.values())


def l1_target_schema(schema: PipelineSchema) -> PipelineSchema:
    """The target as every potter node reads it: `L1_HELD_PROMPT_FIELDS` held on each prompt node."""
    held = NodeSearchNarrowing(prompt_fields_held=list(L1_HELD_PROMPT_FIELDS))
    return schema.narrow({node.name: held for node in schema.prompt_nodes()})


class InjectionKind(enum.StrEnum):
    MEASUREMENT = "measurement"
    DERIVED = "derived"
    TRACE = "trace"
    DIRECTIVE = "directive"

    @property
    def divisible(self) -> bool:
        """Evidence thins to a smaller sample of the same story; half a TRACE or DIRECTIVE is a different, wrong thing."""
        return self in (InjectionKind.MEASUREMENT, InjectionKind.DERIVED)


@dataclass(frozen=True)
class _Injection:
    """``citable`` is False for value-space menus and the prompt under edit: citing those grounds a mutation in itself."""

    name: str
    kind: InjectionKind
    render: Renderer
    char_cap: int | None
    citable: bool


@dataclass(frozen=True)
class PoolParent:
    """One recombination pool entry: a measured individual, its θ on the round's one scale (``None``
    where that scale reads none of it), and whether it is in the population a main is drawn from."""

    individual: OptSearchPoint
    theta: float | None
    main: bool

    @property
    def id(self) -> str:
        return self.individual.id[:RECOMBINATION_ID_CHARS]


class RecombinationOffer(NamedTuple):
    """What a host's recombination slot is closed on: the children it may name, the id heads a main
    and a donor may take, and the locus names a donor may give."""

    children: int
    mains: tuple[str, ...]
    ids: tuple[str, ...]
    loci: tuple[str, ...]


@dataclass(frozen=True)
class Item:
    """``trusted=False`` marks dataset-derived text; the fence around it is the composition's to emit."""

    text: str
    trusted: bool = True
    # The rows this item quotes whole.
    quotes: frozenset[str] = frozenset()
    # `(row, text)`: what this item says instead once a placed item, in any panel, quotes that row.
    if_quoted: tuple[str, str] | None = None


Renderer = Callable[["PotterView"], list[Item]]

_REGISTRY: dict[str, _Injection] = {}


def signal(
    name: str,
    *,
    kind: InjectionKind,
    char_cap: int | None,
    citable: bool,
) -> Callable[[Renderer], Renderer]:
    def deco(fn: Renderer) -> Renderer:
        if name in _REGISTRY:
            raise ValueError(f"duplicate injection signal {name!r}")
        _REGISTRY[name] = _Injection(name, kind, fn, char_cap, citable)
        return fn

    return deco


def injection_registry() -> dict[str, _Injection]:
    """Complete only after every renderer module is imported, which ``registry.injection_table()`` does."""
    return dict(_REGISTRY)
