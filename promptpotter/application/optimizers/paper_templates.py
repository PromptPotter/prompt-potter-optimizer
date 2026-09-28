"""What a paper preset's llm nodes send and read back: the paper's own template filled from the
manifest's ``resolved_prompts``, and the answer read off the ``<prompt>`` markers it asks for."""

from __future__ import annotations

import functools
import importlib
import pkgutil
import random
import re
import sys
from typing import TYPE_CHECKING

from promptpotter.application.bench.llm_call import LLMCallContext, llm_call
from promptpotter.application.optimizer_manifest import running_prompt
from promptpotter.domain.optimizer_state import PARSE_FAILURE_MALFORMED, PARSE_FAILURE_TOOLING
from promptpotter.domain.wounds import ValidationFailure
from promptpotter.shared.hashing import module_source_digest, optimizer_prompt_shapers

if TYPE_CHECKING:
    from types import ModuleType

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.optimizers.nodes import RoundContext

__all__ = [
    "ask",
    "fill",
    "marked",
    "preset_source_digest",
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


def marked(text: str) -> str | None:
    """The last ``<prompt>`` block: an answer comes after any echo of the template's example."""
    found = [m.strip() for m in _PROMPT.findall(text) if m.strip()]
    return found[-1] if found else None


def unmarked(node: str, raw: str) -> ValidationFailure:
    """An empty reply is the provider's failure, not the prompt's: missing data, never a verdict."""
    reason = PARSE_FAILURE_MALFORMED if raw.strip() else PARSE_FAILURE_TOOLING
    return ValidationFailure(axis=f"{node}.output", value=raw[:300], allowed=[], reason=reason)


def walk_rng(cycle: Cycle, round_num: int, node: str) -> random.Random:
    """A function of the run's seed, the round and the node alone, so a resume redraws the same.
    The seed is the determinism clamp's where it pins one, else the campaign's id."""
    clamp = cycle.config.optimization.determinism
    pinned = None if clamp is None else clamp.seed
    seed = cycle.session.campaign_id if pinned is None else pinned
    return random.Random(f"{seed}:{round_num}:{node}")


@functools.cache
def preset_source_digest(members: str, *covered: ModuleType) -> str:
    """Every module of the preset package holding *members*, and this one, with every marked
    definition — ``OptimizerRuntime.source_digest`` for a preset whose prompts are these templates."""
    package = sys.modules[members.rpartition(".")[0]]
    hashed = (
        sys.modules[__name__],
        *(
            importlib.import_module(f"{package.__name__}.{m.name}")
            for m in pkgutil.iter_modules(package.__path__)
        ),
    )
    return module_source_digest(*hashed, *optimizer_prompt_shapers(hashed, covered=covered))
