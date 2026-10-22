"""``Cycle``-free by contract; the ``Cycle``-snapshot path lives in ``facade.py``."""

from __future__ import annotations

import enum
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.potter.records import L2Memory, L3Memory, Ladder
from promptpotter.domain.connector import MeasuredUnit
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import CritiqueReadout
from promptpotter.domain.pipeline_schema import PipelineNode, PipelineSchema
from promptpotter.domain.pool import EARNED_SOURCE, Access, PoolEntry
from promptpotter.domain.results import CutKind, RoundResult
from promptpotter.domain.round_diagnostics import RoundDiagnostics
from promptpotter.domain.ruler import AbilityReading, DeltaRuler
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, GradedCell
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.value_tree import Delivery
from promptpotter.domain.wounds import RuntimeFailure
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.application.optimizers.potter.pobb.checks import EliminationGate
    from promptpotter.application.runner.inner.tasks import InnerCells

# Every optimizer's source digest hashes this module: its constants decide what a prompt receives.
shapes_optimizer_prompt(__name__)

# A field with no entry is emitted unbounded.
OPTIMIZER_PROMPT_FIELD_MAX_CHARS: dict[str, int] = {
    "instruction": 3_200,
}

SCHEMA_DESCRIPTION_MAX_CHARS = 400

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
    "Where in the READING node's own prompt each evidence panel is rendered. A slot is a "
    "position in that prompt, never the target-prompt field the evidence is about: the "
    "reader gets every placed panel whichever slot holds it. Keyed by SLOT — the four "
    "properties are the only slots there are — each listing the panels to place there: a "
    "listed panel MOVES to that slot, an omitted one stays where it is, and a panel is only "
    "ever in one place."
)


AXES_ENUM_PREVIEW = 4
PRECISION_ARM_ROWS = 3
NEAR_MISS_RENDER_CAP = 2
SAMPLE_RENDER_CAP = 2
TRANSCRIPT_RENDER_CAP = 3
TRANSCRIPT_QUERY_CAP = 1200
TRANSCRIPT_REASONING_CAP = 2200
TRANSCRIPT_PREDICTED_CAP = 60
INNER_NARRATIVE_CAP = 1150
MISS_QUERY_CAP = 100
MISS_PREDICTED_CAP = 60
MISS_GT_CAP = 40
MISS_NOTE_CAP = 120
MISS_STEP_CAP = 160
# The panel is mandatory on the critique, so this is what bounds its prompt on a dataset of any size.
MISS_ROWS_CAP = 40
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
DEMO_POOL_RENDER_CAP = 12
DEMO_QUERY_STEM = 80
# Per field under `open`, earned blocks included; the rest rotate in by round. `closed` shows its enum whole.
BLOCK_POOL_RENDER_CAP = 8
BLOCK_ID_INSTRUCTION = (
    "Per prompt node, its pooled fields: {node: {field: the id of a block the PROMPT BLOCK POOL "
    "panel lists for that field}}."
)


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
class CycleSlice:
    # No accuracy here: cycle tracking's `current`/`best` are subset-relative and lag the round rendered.
    round_num: int
    # Closed rounds since the last advance, which no fire resets — never the ladder's pacing count.
    l1_stall_depth: int
    ladder: Ladder
    exploration_budget: str
    pipeline_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    composite_formula: str | None = None
    # `frozen` | `adaptive`, resolved once: a renderer deriving it disagrees with the sampler.
    subset_mode: str | None = None
    sp_budget_round: int | None = None
    budget_rounds: int | None = None
    spend_budget_usd: float | None = None
    # What the cap has counted — a FLOOR while unpriced tokens are outstanding, never "spent".
    spend_used_usd: float | None = None


@dataclass(frozen=True)
class ArmDigest:
    """Deliberately not the arm's state: a panel that can reach a rival's whole prompt will quote it."""

    label: str
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    scored_samples: int
    expected_samples: int
    # `None`: measured, the panel run out or locked in.
    cut: CutKind | None
    gate: EliminationGate | None


@dataclass(frozen=True)
class RoundDigest:
    """One round's readouts; the FAILURE renderers read the layers' memories and ``bundle.runtime_failures``, which accumulate across rounds."""

    diagnostics: RoundDiagnostics | None
    critique: CritiqueReadout | None
    # `L1Readout.l1_yield`: `None` where nothing was proposed.
    l1_yield: float | None = None
    # `None` where the round's generation parsed, or made no call.
    l1_parse_failure: str | None = None
    node_failure_rates: dict[str, float] = field(default_factory=dict)
    latest_sample_keys: frozenset[str] = field(default_factory=frozenset)
    prev_sample_keys: frozenset[str] = field(default_factory=frozenset)
    composite_fitness: float | None = None
    evaluators: dict[str, float] = field(default_factory=dict)
    ability: AbilityReading | None = None
    arms: tuple[ArmDigest, ...] = ()


@dataclass(frozen=True)
class InjectionBundle:
    opt_sp: OptSearchPoint
    l2: L2Memory
    l3: L3Memory
    framing: TaskDecomposition
    pipeline_schema: PipelineSchema | None
    cycle_slice: CycleSlice
    digest: RoundDigest
    axes: AxisIndex | None
    block_access: Access
    rebase_capability: bool
    terminate_capability: bool
    schema_field_rename: bool
    # 0 silences the shot menu as an empty `demo_pool` does, withdrawing the `shots` slot.
    shot_k_max: int
    # Each sample's slot in the cycle's bank: the `[#id]` a panel marks a row by.
    slot_of: Mapping[str, int]
    origin_per_sample: CellSheet = field(default_factory=lambda: CellSheet(""))
    # Hits included: a pipeline collapsed onto one label shows only against the labels it is NOT emitting.
    trajectory_results: Sequence[GradedCell] = ()
    # `None` while the cycle's locked ruler is cold.
    ruler: DeltaRuler | None = None
    # The round under render LAST.
    measured_rounds: list[RoundResult] = field(default_factory=list)
    # The block pool's entries by field, in its draw order; none under `off`.
    blocks: Mapping[str, tuple[PoolEntry, ...]] = field(default_factory=dict)
    # Sibling forks' and this cycle's, each distinct config once (`Cycle.runtime_failures`).
    runtime_failures: tuple[RuntimeFailure, ...] = ()
    # L1 panels rendering nothing for this bundle: L2's layout menu and wire enum leave them out.
    silent_l1_panels: frozenset[str] = frozenset()
    measured_unit: MeasuredUnit = "sample"
    prompt_delivery: Delivery = "request"
    # What a variant's shots may be drawn from; `demo_split` resolves the shots an individual already holds.
    demo_pool: tuple[Sample, ...] = ()
    demo_split: tuple[Sample, ...] = ()
    # `None` off the L4 recursion; each outer node's manifest is `InnerCells.owners`'.
    inner_cells: InnerCells | None = None

    @property
    def prompt_nodes(self) -> list[PipelineNode]:
        return [] if self.pipeline_schema is None else self.pipeline_schema.prompt_nodes()

    @property
    def offers_shots(self) -> bool:
        return bool(self.demo_pool and self.prompt_nodes) and self.shot_k_max > 0

    def block_window(self) -> dict[str, tuple[PoolEntry, ...]]:
        """By field a prompt node opens, what the panel shows: under ``closed`` the active subset whole, its wire's enum; under ``open`` earned blocks, then a window rotating by round."""
        opened = {name for node in self.prompt_nodes for name in node.open_prompt_fields}
        if self.block_access == "closed":
            return {name: held for name, held in self.blocks.items() if name in opened}
        out: dict[str, tuple[PoolEntry, ...]] = {}
        for name, blocks in self.blocks.items():
            earned = [b for b in blocks if b.group[1] == EARNED_SOURCE]
            rest = [b for b in blocks if b.group[1] != EARNED_SOURCE]
            k = min(max(BLOCK_POOL_RENDER_CAP - len(earned), 0), len(rest))
            start = (self.cycle_slice.round_num - 1) * k % len(rest) if rest else 0
            if name in opened and (window := (*earned, *(rest[start:] + rest[:start])[:k])):
                out[name] = window
        return out


@dataclass(frozen=True)
class Item:
    """``trusted=False`` marks dataset-derived text; the fence around it is the composition's to emit."""

    text: str
    trusted: bool = True


Renderer = Callable[[InjectionBundle], list[Item]]

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


__all__ = [
    "ANSWER_LABEL_STEM",
    "ANSWER_TALLY_ROWS",
    "AXES_ENUM_PREVIEW",
    "BLOCK_ID_INSTRUCTION",
    "BLOCK_POOL_RENDER_CAP",
    "DEMO_POOL_RENDER_CAP",
    "DEMO_QUERY_STEM",
    "INNER_NARRATIVE_CAP",
    "L3_PLAN_MAX_CHARS",
    "LOST_CELL_MIN",
    "MEMORY_FIELD_CAP",
    "MEMORY_ROUND_CAP",
    "MEMORY_VALUE_CAP",
    "MISS_GT_CAP",
    "MISS_NOTE_CAP",
    "MISS_PREDICTED_CAP",
    "MISS_QUERY_CAP",
    "MISS_ROWS_CAP",
    "MISS_STEP_CAP",
    "NEAR_MISS_RENDER_CAP",
    "NODE_FAILURE_RENDER_CAP",
    "RUNTIME_FAILURE_RECENCY_WINDOW",
    "SAMPLE_RENDER_CAP",
    "TRANSCRIPT_PREDICTED_CAP",
    "TRANSCRIPT_QUERY_CAP",
    "TRANSCRIPT_REASONING_CAP",
    "TRANSCRIPT_RENDER_CAP",
    "VALIDATION_RENDER_CAP",
    "CycleSlice",
    "InjectionBundle",
    "InjectionKind",
    "Renderer",
    "RoundDigest",
    "injection_registry",
    "signal",
]
