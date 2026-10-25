from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from promptpotter.application import optimizers
from promptpotter.application.bench.children import (
    DROPPED_MANDATORY_PLACEHOLDER,
    admission_failures,
    carried_writes,
    placeholder_failures,
)
from promptpotter.application.bench.llm_call import InjectionBreakdown, LLMCallContext, llm_call
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.intelligence.earned_blocks import (
    answer_space_signature,
    earned_blocks_for,
)
from promptpotter.application.mask.backprop import select_rewind_round
from promptpotter.application.mask.load import load_lineage_spine
from promptpotter.application.optimizer_manifest import running_prompt
from promptpotter.application.optimizers.nodes import CycleRead, ReadArm
from promptpotter.application.runner.inner.spawn_context import inner_cells
from promptpotter.application.scoring.arm_states import conclude, measuring
from promptpotter.application.scoring.evaluators import resolve_cell_formula
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.opt_search_point import (
    OPERATOR_SOURCE,
    Locus,
    OptSearchPoint,
    Variation,
    loci_of,
    node_source,
)
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.domain.pool import BlockPool, DemoPool, Pool, PoolEntry
from promptpotter.domain.results import (
    ArmRole,
    Proposal,
    Proposed,
    merge_known_outcomes,
    round_document_digest,
    walked_slot,
)
from promptpotter.domain.run_records import LLMCallRecord, RoundProposedRecord
from promptpotter.domain.scoring import CellSheet
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.telemetry import at_round
from promptpotter.shared.errors import SendRefusedError
from promptpotter.shared.measurement_context import MeasuredPass, MeasurementRole

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.difficulty import DifficultyView
    from promptpotter.application.intelligence.indexes.axis import AxisIndex
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        Controller,
        NodeMember,
        Proposer,
        RaceSnapshot,
        RoundContext,
    )
    from promptpotter.application.runner.inner.tasks import InnerCells
    from promptpotter.domain.connector import MeasuredUnit
    from promptpotter.domain.opt_search_point import EvidenceGrounding
    from promptpotter.domain.optimizer_state import RoundOutput, StatePart, StateSlice
    from promptpotter.domain.phase_views import PhaseView
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.pool import Access
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.ruler import LevelSeries
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.value_tree import Delivery
    from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure

logger = logging.getLogger(__name__)


def bank_proposals(round_: RoundContext, proposals: Sequence[Proposal], *, submitted: bool) -> None:
    """Banked beside what they were composed from, so a resume asks whether that round still says the same."""
    ledger = round_.cycle.session.state.ledger
    if ledger is None:
        return
    rounds = round_.cycle.rounds
    ledger.append(
        RoundProposedRecord(
            round=round_.round_num,
            consumed=round_document_digest(rounds[-1]) if rounds else "",
            proposals=list(proposals),
            submitted=submitted,
        )
    )


async def measure_as_parent(
    round_: RoundContext,
    individual: OptSearchPoint,
    sp: JobSearchPoint,
    cells: list[Sample],
    *,
    role: ArmRole,
    force_fresh: bool = False,
) -> ReadArm:
    """A reference read is an arm of the round whoever asked; a re-read continues that arm's walk."""
    cycle = round_.cycle
    session = cycle.session
    key = ArmKey(individual.id)
    asked = round_.asked(key, cells)
    walking = round_.walks.get(key)
    # A panel arm keeps its panel state: a read of it adds only its cells' records.
    announce = walking is None or walking.arm.state.role == MeasurementRole.PARENT
    arm = measuring(
        Proposed(role=role, slot=walked_slot(cycle.rounds, key), individual=individual),
        sp,
        session.pipeline_schema,
        expected=len(asked),
        sample_order=[s.id for s in cells],
    )
    if announce:
        round_.callbacks.on_arm(round_.round_num, key, arm)
    walked = await score_search_point(
        sp,
        cells,
        session,
        label=role,
        sample_index=cycle.sample_index,
        measured=MeasuredPass(individual.id, role),
        force_fresh=force_fresh,
        held=round_.held(key),
    )
    # A partial pass is kept: `read_pair` pairs each arm on the cells both scored.
    if walked.stopped is not None:
        logger.warning(
            "Parent %s stopped after %d/%d cells (%s); its floor covers only those.",
            arm.label,
            len(walked.sheet),
            len(cells),
            walked.stopped,
        )
    read = ReadArm(key, conclude(arm, walked), walked.sheet)
    if announce:
        round_.callbacks.on_arm(round_.round_num, key, read.state)
    round_.walked(read, asked)
    return read


class Lineage(CycleRead):
    @property
    def parent(self) -> OptSearchPoint:
        return self._cycle.parent

    @property
    def origin(self) -> OptSearchPoint:
        return self._cycle.origin_round.origin.individual

    @property
    def population(self) -> Sequence[OptSearchPoint]:
        return self._cycle.population

    @property
    def rounds(self) -> Sequence[RoundResult]:
        """Sealed: a round's optimizer state opens only through ``recall`` / ``slice``, which check what the node declares."""
        return [rr.model_copy(update={"optimizer_state": None}) for rr in self._cycle.rounds]

    def rewind_round(self) -> int | None:
        """Where a rewind of this cycle lands (UCB over the campaign's lineage); ``None`` where no ancestor stands."""
        return select_rewind_round(
            load_lineage_spine(self._cycle.difficulty),
            cycle_id=self._cycle.session.cycle_id,
            current_round=self._round.round_num,
        )


class Frontier(CycleRead):
    @property
    def parent_point(self) -> JobSearchPoint:
        return self._cycle.parent_point

    @property
    def frontier_rows(self) -> list[GradedCell]:
        return self._cycle.frontier

    @property
    def parent_own_rows(self) -> list[GradedCell]:
        """Each sample's latest cell of the parent ITSELF, never one another individual answered."""
        key = ArmKey(self._cycle.parent.id)
        rows: list[GradedCell] = []
        for rr in self._cycle.rounds:
            rows = merge_known_outcomes(rows, rr.rows(key))
        return rows

    @property
    def parent_accuracy(self) -> float | None:
        """On the panel it was LAST scored on; ``None`` is unmeasured."""
        fold = self._cycle.parent_fold
        return None if fold is None else fold.accuracy


class Ruler(CycleRead):
    @property
    def difficulty(self) -> DifficultyView:
        return self._cycle.difficulty

    @property
    def level_series(self) -> LevelSeries:
        """Every θ a member reads across rounds is the last close's, off the ledger, never its own fit."""
        session = self._cycle.session
        series = session.store.campaigns.standing_rounds(session.hop, moment=None).level_series
        if series is None:
            raise LookupError(
                f"round {self._round.round_num} reads a level series no close has banked"
            )
        return series

    @property
    def sample_index(self) -> SampleIndex | None:
        """As of the last CLOSED round; ``None`` before a run opens one."""
        return self._cycle.sample_index

    @property
    def axes(self) -> AxisIndex | None:
        return self._cycle.axes()

    @property
    def slot_of(self) -> Mapping[str, int]:
        return self._cycle.session.bank.slot_of


class Bounds(CycleRead):
    @property
    def elimination_n_min(self) -> int:
        return self._cycle.config.optimization.elimination_n_min

    @property
    def budget_rounds(self) -> int | None:
        return self._cycle.config.optimization.budget.rounds

    def spend_cap_usd(self) -> float | None:
        return self._cycle.session.state.control.spend_cap_usd()

    def spend_used_usd(self) -> float:
        return self._cycle.session.state.control.spend_used_usd()


class Target(CycleRead):
    """The pipeline the run measures, as the bench resolved it."""

    @property
    def framing(self) -> Any:
        return self._cycle.framing

    @property
    def demo_split(self) -> tuple[Sample, ...]:
        """The demo partition whole, which resolves the shots an individual already holds; a reader draws through ``drawn``."""
        return self._cycle.session.state.scoring.require_partition().demo

    @property
    def measured_unit(self) -> MeasuredUnit:
        return self._cycle.session.backend_client.measured_unit

    @property
    def prompt_delivery(self) -> Delivery:
        session = self._cycle.session
        return session.backend_client.prompt_delivery(session.pipeline_params)

    @property
    def cell_formula(self) -> str | None:
        session = self._cycle.session
        formula, _ = resolve_cell_formula(
            session.state.scoring.require_scorer().per_cell, session.pipeline_schema
        )
        return formula

    @property
    def matcher(self) -> str | None:
        return self._cycle.session.state.scoring.require_scorer().matcher

    @property
    def inner_cells(self) -> InnerCells | None:
        """The cells an L4 outer's arms are measured on; ``None`` off the recursion."""
        return inner_cells()

    def runtime_failures(self) -> list[RuntimeFailure]:
        return self._cycle.runtime_failures()

    def earned_blocks(self) -> Mapping[str, tuple[str, ...]]:
        """By prompt field, the blocks runs sharing the origin's answer space measured as a lift."""
        session = self._cycle.session
        signature = answer_space_signature(
            (cell.facts.ground_truth for cell in self._cycle.origin_round.results),
            dataset=session.measured_dataset,
        )
        return earned_blocks_for(session.store, signature)


class NodeContext[K: StrictModel]:
    """Nothing here hands out the cycle, its session, a store, the manifest or the config: only what this node declares, and its own."""

    def __init__(self, round_: RoundContext, member: NodeMember) -> None:
        self._round = round_
        self._member = member
        self.node = member.name

    def read[R: CycleRead](self, kind: type[R]) -> R:
        if kind not in self._member.sees:
            raise KeyError(f"{self.node!r} reads the cycle's {kind.__name__} and does not see it")
        return kind(self._round)

    @property
    def round_num(self) -> int:
        return self._round.round_num

    @property
    def is_final_round(self) -> bool:
        return self._round.is_final_round

    def banked_proposals(self) -> list[Proposal] | None:
        return self._round.cycle.banked_proposals(self.round_num)

    @property
    def _selected(self) -> SelectedOptimizer:
        return self._round.cycle.optimizer

    def _declares(self, kind: type[StatePart], *, writes: bool) -> None:
        member = self._member
        if kind not in (member.writes if writes else member.reads | member.writes):
            verb = "writes" if writes else "reads"
            raise KeyError(f"{self.node!r} {verb} the {kind.kind!r} state and does not declare it")

    def knobs_of[T: StrictModel](self, kind: type[T]) -> T:
        return self._selected.reached_knobs(self._member, kind)

    def fire(self, member: type) -> NodeContext[Any]:
        """The context a fired member runs under, its own, declared by this controller's ``fires``."""
        if member not in cast("Controller", self._member).fires:
            raise KeyError(f"{self.node!r} fires {member.__name__} and does not declare it")
        return NodeContext(self._round, optimizers.member(self._selected.roster_one(member)))

    def slice[S: StateSlice](self, kind: type[S]) -> S:
        self._declares(kind, writes=False)
        return self._round.cycle.slices.get(kind)

    def put(self, value: StateSlice) -> None:
        self._declares(type(value), writes=True)
        self._round.cycle.slices.put(value)

    def recall[O: RoundOutput](self, kind: type[O], round_num: int) -> O:
        self._declares(kind, writes=False)
        return self._round.cycle.output(kind, round_num)

    def emit(self, output: RoundOutput) -> None:
        """Onto this context's round: banked at its close, or restated there where it already closed."""
        self._declares(type(output), writes=True)
        if (restated := self._round.cycle.emit(output, self.round_num)) is not None:
            self._bank(restated)

    def restate(self, *kinds: type[StateSlice]) -> None:
        """Bank what this node now holds of *kinds* onto the closed round: a write landing after its close."""
        for kind in kinds:
            self._declares(kind, writes=True)
        cycle = self._round.cycle
        closed = cycle.rounds[-1]
        state = closed.closed_state.holding(*(cycle.slices.get(kind) for kind in kinds))
        self._bank(closed.model_copy(update={"optimizer_state": state}))

    def access(self, kind: type[Pool]) -> Access:
        return self._selected.access(self._member, kind)

    @overload
    def drawn(self, kind: type[DemoPool]) -> tuple[Sample, ...]: ...
    @overload
    def drawn(self, kind: type[BlockPool]) -> tuple[PoolEntry, ...]: ...
    def drawn(self, kind: type[Pool]) -> tuple[Any, ...]:
        """The one reading of a pool, by its reader's declared draw: its entries in draw order, none under ``off``."""
        if self.access(kind) == "off":
            return ()
        demo = self._round.cycle.session.state.scoring.require_partition().demo
        return self._round.cycle.slices.get(kind).drawn({s.key: s for s in demo})

    def _bank(self, restated: RoundResult) -> None:
        """On the run's own ledger before the cycle seats it: a restatement that failed to land is held nowhere."""
        cycle = self._round.cycle
        session = cycle.session
        if restated.at_offset is not None:
            ledger = session.state.ledger
            assert ledger is not None, "a closed round is restated under the run's observers"
            ledger.append(session.store.campaigns.restated_state(session.hop, restated))
        cycle.seat(restated)

    @property
    def schema(self) -> PipelineSchema:
        return self._round.cycle.session.pipeline_schema

    @property
    def prompt_node(self) -> PipelineNode:
        """The ONE prompt node a ``prompt_arity: one`` member rewrites; ``preflight.py::refuse_unfit_pipeline`` refused any other roster before the mint."""
        (node,) = self.schema.prompt_nodes()
        return node

    def child(
        self,
        parents: Sequence[OptSearchPoint],
        *,
        overlay: dict[str, dict[str, Any]] | None = None,
        failures: Sequence[ValidationFailure] = (),
        take: Mapping[Locus, int] | None = None,
        prompt_loci: Mapping[Locus, Any] | None = None,
        changes_description: str = "",
        evidence_grounding: EvidenceGrounding | None = None,
    ) -> Proposal:
        """The ONE way a child is written, so the bench admits it as it is made; *overlay* lays config over the FIRST parent's."""
        schema = self.schema
        asked = overlay or {}
        prompts, config, carried = carried_writes(
            schema,
            prompt_loci=prompt_loci or {},
            overlay=asked,
            demo=self._demo_keys(prompt_loci or {}),
        )
        individual = OptSearchPoint.derive(
            parents,
            schema=schema,
            variation=self._variation,
            take=take,
            changes={**loci_of(config), **prompts},
            changes_description=changes_description,
            evidence_grounding=evidence_grounding,
        )
        admitted = admission_failures(individual, config, schema, carried=carried)
        return Proposal(
            individual=individual,
            overlay=asked,
            validation_failures=[*failures, *admitted],
        )

    def edit(
        self,
        proposal: Proposal,
        *,
        prompt_loci: Mapping[Locus, Any],
        changes_description: str | None = None,
    ) -> Proposal:
        schema = self.schema
        prompts, _, carried = carried_writes(
            schema, prompt_loci=prompt_loci, overlay={}, demo=self._demo_keys(prompt_loci)
        )
        individual = proposal.individual.edited(
            self._variation, schema=schema, changes=prompts, changes_description=changes_description
        )
        failures = [*proposal.validation_failures, *carried]
        if individual.prompts != proposal.individual.prompts:
            failures = [
                *(f for f in failures if f.reason != DROPPED_MANDATORY_PLACEHOLDER),
                *placeholder_failures(individual, schema),
            ]
        return proposal.model_copy(
            update={"individual": individual, "validation_failures": failures}
        )

    def _demo_keys(self, loci: Mapping[Locus, Any]) -> frozenset[str] | None:
        """The shots this member may write: what it draws of the demo pool, asked only where *loci* write one; ``None`` where it draws none."""
        if DemoPool not in self._member.draws:
            return None
        if not any(locus.key == SHOTS_KEY for locus in loci):
            return frozenset()
        return frozenset(s.key for s in self.drawn(DemoPool))

    @property
    def optimizer_name(self) -> str:
        return self._selected.name

    @property
    def knobs(self) -> K:
        return cast("K", self._selected.knobs(self.node))

    @property
    def node_config(self) -> dict[str, Any]:
        """Its own node's config as the manifest and overlay resolve it."""
        return self._selected.node_config(self.node)

    @property
    def model(self) -> str:
        return self._selected.model(self.node)

    def call_context(
        self, *, round_num: int | None = None, injections: InjectionBreakdown | None = None
    ) -> LLMCallContext:
        session = self._round.cycle.session
        return LLMCallContext(
            ledger=session.state.ledger,
            round_num=self.round_num if round_num is None else round_num,
            cache=session.store.optimizer_reuse,
            injections=InjectionBreakdown() if injections is None else injections,
        )

    def note_synthesized_call(self, payload: dict[str, Any]) -> None:
        """Where a round makes no call, so the audit trail and dashboard still see the node."""
        if (ledger := self._round.cycle.session.state.ledger) is not None:
            ledger.append(
                LLMCallRecord(
                    node=self.node,
                    round=self.round_num,
                    payload_kind="synthesized",
                    payload=payload,
                )
            )

    def race_standing(self, snapshot: RaceSnapshot) -> None:
        self._round.callbacks.on_race_standing(self.node, self.round_num, snapshot)

    def phase(self, phase: str, step: Literal["enter", "exit"], view: PhaseView) -> None:
        self._round.callbacks.on_phase(phase, step, round=self.round_num, view=view)

    def keep(self, individuals: Sequence[OptSearchPoint]) -> None:
        self._round.cycle.population = list(individuals)

    @property
    def _variation(self) -> Variation:
        mode = cast("Proposer", self._member).variation_mode
        node = (
            OPERATOR_SOURCE if mode == "operator" else node_source(self._selected.name, self.node)
        )
        return Variation(node=node, mode=mode)

    def rng(self, tag: str = "") -> random.Random:
        """A function of the run's seed, the round, this node and *tag* alone, so a resume redraws the same."""
        cycle = self._round.cycle
        clamp = cycle.config.optimization.determinism
        pinned = None if clamp is None else clamp.seed
        seed = cycle.session.campaign_id if pinned is None else pinned
        return random.Random(f"{seed}:{self.round_num}:{self.node}{tag}")

    def fill(self, **values: str) -> str:
        selected = self._selected
        template = running_prompt(self.node, selected.node_config(self.node), selected.manifest)
        return template.compile_prompt(**values)

    async def ask(self, prompt: str, idx: int | None = None) -> str:
        session = self._round.cycle.session
        # The seed keys the reuse cache: a resume replays its reply, a repeat anywhere else samples.
        seed = self.rng(f":{idx}:request").getrandbits(31)
        response = await llm_call(
            [{"role": "user", "content": prompt}],
            node=self.node,
            context=LLMCallContext(
                ledger=session.state.ledger,
                round_num=self.round_num,
                candidate_idx=idx,
                cache=session.store.optimizer_reuse,
            ),
            seed=seed,
        )
        return response.content

    async def ask_each(self, prompts: Mapping[int, str]) -> list[str]:
        """Every send lands before one's refusal is raised; a refused send is re-asked alone, into the room really left."""
        landed = await asyncio.gather(
            *(self.ask(prompt, idx) for idx, prompt in prompts.items()), return_exceptions=True
        )
        replies: list[str] = []
        for (idx, prompt), reply in zip(prompts.items(), landed, strict=True):
            if isinstance(reply, SendRefusedError):
                reply = await self.ask(prompt, idx)
            if isinstance(reply, BaseException):
                raise reply
            replies.append(reply)
        return replies

    async def measure_parent(self, individual_id: str, cells: list[Sample]) -> CellSheet:
        cycle = self._round.cycle
        arm = cycle.measured(individual_id)
        read = await measure_as_parent(
            self._round,
            arm.individual,
            cycle.points.measured(arm),
            cells,
            role=MeasurementRole.PARENT,
        )
        return read.rows

    async def show_next_round(self) -> None:
        shown = self.round_num + 1
        with at_round(shown):
            await self._selected.runtime.show_round(replace(self._round, round_num=shown))

    def decide(
        self, kind: str, inputs: dict[str, Any], outcome: Any, *, data: dict[str, Any] | None = None
    ) -> Any:
        """A resume re-derives it only where the runtime registers a replayer for *kind*; else it is archived."""
        return record_decision(
            self._round.cycle.pending_decisions,
            kind,
            inputs,
            outcome,
            node=self.node,
            data=data,
            round=self.round_num,
        )
