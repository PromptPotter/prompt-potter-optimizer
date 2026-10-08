"""What every paper preset shares: the paper's template filled from the manifest's ``resolved_prompts``,
the answer read off its ``<prompt>`` markers, and a runtime with no pacing, review or L4 lever."""

from __future__ import annotations

import asyncio
import functools
import random
import re
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar

from promptpotter.application.bench.llm_call import LLMCallContext, llm_call
from promptpotter.application.optimizer_manifest import running_prompt
from promptpotter.application.optimizers import nodes, other_optimizer_packages
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.wounds import ValidationFailure
from promptpotter.shared.errors import SendRefusedError
from promptpotter.shared.hashing import module_source_digest, optimizer_prompt_shapers

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.resume_and_fork.decisions import GatingMode
    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import ReviewReading, RoundContext, WorkingState
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.results import OptimizerFact, RoundResult
    from promptpotter.domain.run_records import CheckpointKind
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = [
    "PaperRuntime",
    "ask",
    "ask_each",
    "child",
    "fill",
    "marked",
    "preset_source_digest",
    "reply_fields",
    "rewritten",
    "task_description",
    "unmarked",
    "walk_rng",
]

_PROMPT = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)


def task_description(cycle: Cycle) -> str:
    """The campaign's framing, then the origin's answer format — the extraction contract the
    scorer reads, which a paper's hand-written task descriptions state (CAPO App. D.1)."""
    origin = cycle.origin_round.opt_sp
    assert origin is not None, "round 0 closes with the origin's individual"
    parts = [v for v in cycle.framing.to_dict().values() if v]
    if origin.answer_format:
        parts.append(origin.answer_format)
    if not parts:
        raise ValueError(
            f"{cycle.optimizer.name} fills its templates with the task description, and this "
            "campaign declares no framing (task_context) and its origin no answer_format"
        )
    return "\n".join(parts)


def rewritten(text: str) -> dict[str, Any]:
    """The prompt fields of a child whose whole prompt is *text*: it rides ``instruction`` and
    every other field empties, so an operator replaces the ``render()`` it was shown — the
    origin's several fields read as one text."""
    return {**dict.fromkeys(PROMPT_STRING_FIELDS, ""), "instruction": text}


def fill(cycle: Cycle, node: str, **values: str) -> str:
    selected = cycle.optimizer
    template = running_prompt(node, selected.node_config(node), selected.document)
    return template.compile_prompt(**values)


async def ask(ctx: RoundContext, node: str, idx: int | None, prompt: str) -> str:
    session = ctx.cycle.session
    # The seed keys the reuse cache: a resume replays its reply, a repeat anywhere else samples.
    seed = walk_rng(ctx.cycle, ctx.round_num, f"{node}:{idx}:request").getrandbits(31)
    response = await llm_call(
        [{"role": "user", "content": prompt}],
        node=node,
        context=LLMCallContext(
            ledger=session.state.ledger,
            round_num=ctx.round_num,
            candidate_idx=idx,
            cache=session.store.optimizer_reuse,
        ),
        seed=seed,
    )
    return response.content


async def ask_each(ctx: RoundContext, node: str, prompts: Mapping[int, str]) -> list[str]:
    """One reply per prompt, keyed by its candidate index. Every send lands before one's refusal is
    raised: a sibling cut mid-flight is never billed, so its hold binds the ceiling at full bound.
    A refused send is then asked again, alone: its siblings were held at their bounds when it was
    refused, and what they billed is the room that is really left."""
    landed = await asyncio.gather(
        *(ask(ctx, node, idx, prompt) for idx, prompt in prompts.items()), return_exceptions=True
    )
    replies: list[str] = []
    for (idx, prompt), reply in zip(prompts.items(), landed, strict=True):
        if isinstance(reply, SendRefusedError):
            reply = await ask(ctx, node, idx, prompt)
        if isinstance(reply, BaseException):
            raise reply
        replies.append(reply)
    return replies


def marked(text: str) -> str | None:
    """The last ``<prompt>`` block: an answer comes after any echo of the template's example."""
    found = [m.strip() for m in _PROMPT.findall(text) if m.strip()]
    return found[-1] if found else None


def unmarked(node: str, raw: str) -> ValidationFailure:
    """An empty reply is the provider's failure, not the prompt's: missing data, never a verdict."""
    reason = PARSE_FAILURE_MALFORMED if raw.strip() else PARSE_FAILURE_TOOLING
    return ValidationFailure(axis=f"{node}.output", value=raw[:300], allowed=[], reason=reason)


def reply_fields(
    node: str, raw: str, parse: Callable[[str], str | None] = marked
) -> tuple[dict[str, Any], list[ValidationFailure]]:
    """An operator's reply as the prompt fields it rewrites; one *parse* cannot read rewrites
    none and is the failure that keeps its individual from being measured."""
    text = parse(raw)
    return ({}, [unmarked(node, raw)]) if text is None else (rewritten(text), [])


def child(
    source: str,
    node: str,
    parents: Sequence[OptSearchPoint],
    raw: str,
    changes: str,
    *,
    parse: Callable[[str], str | None] = marked,
    **fields: Any,
) -> CandidateProposal:
    """The proposal *node*'s reply makes of *parents*, its lineage stamped *source*; *fields*
    are what the operator sets beside the prompt."""
    rewrite, failures = reply_fields(node, raw, parse)
    individual = OptSearchPoint.derive(
        parents,
        source=source,
        changes_description=changes,
        **rewrite,
        **fields,
    )
    return CandidateProposal(opt_sp=individual, validation_failures=failures)


def walk_rng(cycle: Cycle, round_num: int, node: str) -> random.Random:
    """A function of the run's seed, the round and the node alone, so a resume redraws the same.
    The seed is the determinism clamp's where it pins one, else the campaign's id."""
    clamp = cycle.config.optimization.determinism
    pinned = None if clamp is None else clamp.seed
    seed = cycle.session.campaign_id if pinned is None else pinned
    return random.Random(f"{seed}:{round_num}:{node}")


@functools.cache
def preset_source_digest(operators: ModuleType, *covered: ModuleType) -> str:
    """This module and the preset's *operators* — what its llm nodes send — with every marked
    definition outside the other optimizers' packages: ``OptimizerRuntime.source_digest``."""
    hashed = (sys.modules[__name__], operators)
    shapers = optimizer_prompt_shapers(
        hashed, covered=covered, foreign=other_optimizer_packages(operators.__name__)
    )
    return module_source_digest(*hashed, *shapers)


class PaperRuntime(ABC):
    """The ``OptimizerRuntime`` of a preset that runs a paper's algorithm as its manifest declares
    it. A preset names itself, its ``operators`` and its decision kinds."""

    name: ClassVar[str]
    manifest_dir: ClassVar[Path]
    operators: ClassVar[ModuleType]
    checkpoint_gating: ClassVar[Mapping[CheckpointKind, GatingMode]]
    replayers: ClassVar[Mapping[str, Replayer]]
    own_axes: ClassVar[dict[str, set[str]]] = {}
    priced_surface: ClassVar[Mapping[str, int]] = {}
    phases: ClassVar[tuple[nodes.OptimizerPhase, ...]] = ()
    response_models: ClassVar[Mapping[str, type[BaseModel]]] = {}

    @abstractmethod
    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> WorkingState: ...

    @abstractmethod
    def arms(self, selected: SelectedOptimizer) -> int:
        """The most arms one round races."""

    @abstractmethod
    def round_cells_ceiling(self, selected: SelectedOptimizer, pool: int) -> int: ...

    @abstractmethod
    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]: ...

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        # Unless a node reads a package off the rounds before it, a repair drifts none.
        return {}

    def pacing(self, selected: SelectedOptimizer) -> nodes.OptimizerPacing:
        return nodes.OptimizerPacing(
            patience=None, stalls_left=None, arms_per_round=self.arms(selected), limits=()
        )

    def opening(self, ctx: RoundContext) -> nodes.RoundOpening:
        return nodes.standing_opening(ctx)

    def complete(self) -> None:
        return None

    def source_digest(self, *covered: ModuleType) -> str:
        return preset_source_digest(self.operators, *covered)

    def override_param_types(self, node: str) -> dict[str, str]:
        return {}

    def override_levers(self, node: str, declared: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        cycle: Cycle,
        drifted: list[RoundResult],
    ) -> None:
        return None

    def review(
        self,
        selected: SelectedOptimizer,
        rounds: list[RoundResult],
        audits: list[dict[str, Any] | None],
        *,
        context_object: list[str],
    ) -> ReviewReading | None:
        return None
