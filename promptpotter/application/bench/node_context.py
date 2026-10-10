from __future__ import annotations

import asyncio
import random
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.children import (
    DROPPED_MANDATORY_PLACEHOLDER,
    admission_failures,
    placeholder_failures,
)
from promptpotter.application.bench.llm_call import LLMCallContext, llm_call
from promptpotter.application.bench.resume_and_fork.decisions import record_decision
from promptpotter.application.optimizer_manifest import running_prompt
from promptpotter.application.pipeline_resolve import merge_pipeline_params
from promptpotter.application.scoring.query_loop import ArmSlot
from promptpotter.application.scoring.search_point_scorer import score_search_point
from promptpotter.domain.opt_search_point import OptSearchPoint, Variation, node_source
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import CandidateProposal, round_document_digest
from promptpotter.domain.run_records import RoundProposedRecord
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import SendRefusedError, graceful
from promptpotter.shared.measurement_context import (
    NO_ROUND_SLOT,
    MeasuredCandidate,
    MeasurementRole,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.difficulty import DifficultyView
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import RoundContext, WorkingState
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.connector import MeasuredUnit
    from promptpotter.domain.opt_search_point import EvidenceGrounding
    from promptpotter.domain.optimizer_state import RoundPayload
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.wounds import ValidationFailure

__all__ = ["NodeContext", "measure_as_parent"]


async def measure_as_parent(
    cycle: Cycle, sp: JobSearchPoint, individual_id: str, cells: list[Sample]
) -> CellSheet:
    walked = await score_search_point(
        sp,
        cells,
        cycle.session,
        label=MeasurementRole.PARENT,
        sample_index=cycle.sample_index,
        slot=ArmSlot(NO_ROUND_SLOT, 0, individual_id),
        measured=MeasuredCandidate(
            idx=NO_ROUND_SLOT,
            candidate_id=individual_id,
            label=f"parent:{individual_id[:8]}",
            role=MeasurementRole.PARENT,
        ),
    )
    return walked.sheet


class NodeContext[K: StrictModel]:
    """Nothing here hands out the cycle, its session or a store."""

    def __init__(self, round_: RoundContext, node: str) -> None:
        self._round = round_
        self.node = node

    @property
    def round_num(self) -> int:
        return self._round.round_num

    @property
    def is_final_round(self) -> bool:
        return self._round.is_final_round

    # A DETACHED round replays nothing, and nothing it keeps, banks or decides lands on the cycle.

    def banked_proposals(self) -> list[CandidateProposal] | None:
        session = self._round.cycle.session
        if self._round.detached or not session.state.cycle_id:
            return None
        proposed = session.store.campaigns.round_proposals(session.hop, self.round_num)
        return None if proposed is None else list(proposed.proposals)

    def bank_proposals(self, proposals: Sequence[CandidateProposal]) -> None:
        """Banked beside what they were composed from, so a resume asks whether that round still says the same."""
        ledger = self._round.cycle.session.state.ledger
        if self._round.detached or ledger is None:
            return
        rounds = self.rounds
        ledger.append(
            RoundProposedRecord(
                round=self.round_num,
                consumed=round_document_digest(rounds[-1]) if rounds else "",
                proposals=list(proposals),
            )
        )

    def restate(self, payload: RoundPayload) -> None:
        cycle = self._round.cycle
        closed = cycle.rounds[-1]
        state = closed.optimizer_state.model_copy(update={"payload": payload})
        restated = cycle.seat(closed.model_copy(update={"optimizer_state": state}))
        session = cycle.session
        if self._round.detached or not session.state.cycle_id:
            return
        with graceful(f"round {restated.round} optimizer state not restated"):
            session.store.campaigns.restate_optimizer_state(session.hop, restated)

    @property
    def schema(self) -> PipelineSchema | None:
        return self._round.cycle.session.pipeline_schema

    def child(
        self,
        parents: Sequence[OptSearchPoint],
        *,
        overlay: dict[str, dict[str, Any]] | None = None,
        failures: Sequence[ValidationFailure] = (),
        take: Mapping[str, int] | None = None,
        changes_description: str = "",
        evidence_grounding: EvidenceGrounding | None = None,
        **changes: Any,
    ) -> CandidateProposal:
        """The ONE way a child is written, so the bench admits it as it is made; *overlay* lays over the FIRST parent's."""
        schema = self.schema
        asked = overlay or {}
        merged = (
            merge_pipeline_params(parents[0].pipeline_params, asked, schema)
            if asked and schema
            else None
        )
        individual = OptSearchPoint.derive(
            parents,
            variation=self.variation,
            take=take,
            config=None if merged is None or schema is None else (merged, schema),
            changes_description=changes_description,
            evidence_grounding=evidence_grounding,
            **changes,
        )
        admitted = admission_failures(individual, asked, schema) if schema else []
        return CandidateProposal(
            opt_sp=individual,
            pipeline_overlay=asked,
            validation_failures=[*failures, *admitted],
        )

    def edit(
        self,
        proposal: CandidateProposal,
        *,
        changes_description: str | None = None,
        **changes: Any,
    ) -> CandidateProposal:
        individual = proposal.opt_sp.edited(
            self.variation, changes_description=changes_description, **changes
        )
        failures = proposal.validation_failures
        schema = self.schema
        if schema and individual.prompt_fields() != proposal.opt_sp.prompt_fields():
            failures = [
                *(f for f in failures if f.reason != DROPPED_MANDATORY_PLACEHOLDER),
                *placeholder_failures(individual, schema),
            ]
        return proposal.model_copy(update={"opt_sp": individual, "validation_failures": failures})

    @property
    def optimizer(self) -> SelectedOptimizer:
        return self._round.cycle.optimizer

    @property
    def knobs(self) -> K:
        return cast("K", self.optimizer.knobs(self.node))

    def knobs_of[T: StrictModel](self, node: str, kind: type[T]) -> T:
        return self.optimizer.knobs_of(node, kind)

    def sibling(self, node: str) -> NodeContext[Any]:
        return NodeContext(self._round, node)

    @property
    def config(self) -> CampaignConfig:
        return self._round.cycle.config

    @property
    def state(self) -> WorkingState:
        return self._round.cycle.working_state

    @property
    def callbacks(self) -> RunCallbacks:
        return self._round.callbacks

    @property
    def parent(self) -> OptSearchPoint:
        return self._round.cycle.opt_sp

    @property
    def population(self) -> Sequence[OptSearchPoint]:
        return self._round.cycle.population

    def keep(self, individuals: Sequence[OptSearchPoint]) -> None:
        if not self._round.detached:
            self._round.cycle.population = list(individuals)

    @property
    def variation(self) -> Variation:
        selected = self.optimizer
        llm = selected.node(self.node).kind is NodeKind.LLM
        return Variation(
            node=node_source(selected.name, self.node), mode="llm" if llm else "deterministic"
        )

    @property
    def measured_unit(self) -> MeasuredUnit:
        return self._round.cycle.session.backend_client.measured_unit

    @property
    def parent_point(self) -> JobSearchPoint | None:
        return self._round.cycle.tracking.current_sp

    @property
    def parent_rows(self) -> list[GradedCell]:
        """The cycle's frontier: each sample's latest cell, of WHICHEVER individual measured it."""
        return self._round.cycle.tracking.current_results

    @property
    def parent_accuracy(self) -> float | None:
        """On the panel it was LAST scored on; ``None`` is unmeasured."""
        return self._round.cycle.tracking.current_accuracy

    @property
    def origin(self) -> OptSearchPoint:
        origin = self._round.cycle.origin_round.opt_sp
        assert origin is not None, "round 0 closes with the origin's individual"
        return origin

    @property
    def rounds(self) -> Sequence[RoundResult]:
        return self._round.cycle.rounds

    @property
    def framing(self) -> Any:
        return self._round.cycle.framing

    @property
    def demo_pool(self) -> tuple[Sample, ...]:
        return self._round.cycle.session.scoring.require_partition().demo

    @property
    def difficulty(self) -> DifficultyView:
        return self._round.cycle.difficulty

    @property
    def sample_index(self) -> SampleIndex | None:
        """As of the last CLOSED round; ``None`` before a run opens one."""
        return self._round.cycle.sample_index

    def rng(self, tag: str = "") -> random.Random:
        """A function of the run's seed, the round, this node and *tag* alone, so a resume redraws the same."""
        cycle = self._round.cycle
        clamp = cycle.config.optimization.determinism
        pinned = None if clamp is None else clamp.seed
        seed = cycle.session.campaign_id if pinned is None else pinned
        return random.Random(f"{seed}:{self.round_num}:{self.node}{tag}")

    def fill(self, node: str | None = None, /, **values: str) -> str:
        name = node or self.node
        selected = self.optimizer
        template = running_prompt(name, selected.node_config(name), selected.document)
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
        return await measure_as_parent(
            cycle, cycle.searchpoint(individual_id), individual_id, cells
        )

    async def show_next_round(self) -> None:
        await self.optimizer.runtime.show_round(replace(self._round, round_num=self.round_num + 1))

    def decide(
        self, kind: str, inputs: dict[str, Any], outcome: Any, *, data: dict[str, Any] | None = None
    ) -> Any:
        """A resume re-derives it only where the runtime registers a replayer for *kind*; else it is archived."""
        return record_decision(
            [] if self._round.detached else self._round.cycle.pending_decisions,
            kind,
            inputs,
            outcome,
            node=self.node,
            data=data,
            round=self.round_num,
        )
