"""What CAPO's llm nodes send and read back (arXiv 2504.16005 App. B, D): the paper's own template
filled from the manifest's ``resolved_prompts``, and the answer read off the markers it asks for."""

from __future__ import annotations

import ast
import random
import re
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.llm_call.call import LLMCallContext, llm_call
from promptpotter.domain.opt_search_point import OptSearchPoint, PromptTemplate, node_source
from promptpotter.domain.optimizer_state import CAPO_MANIFEST, L1_PARSE_FAILURE_MALFORMED
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimizers.nodes import RoundContext

__all__ = [
    "INIT_NODE",
    "ask",
    "fill",
    "initial_population",
    "marked",
    "task_description",
    "unmarked",
    "walk_rng",
]

INIT_NODE = "capo_init"
"""The llm node generating the initial instructions. It runs once, when the first crossover finds
the population empty, so the manifest declares it in a one-step pipeline of its own."""

_PROMPT = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)


def task_description(cycle: Cycle) -> str:
    """The campaign's framing, then the origin's answer format — the extraction contract the
    scorer reads, which the paper's hand-written task descriptions state (App. D.1)."""
    origin = cycle.origin_round.opt_sp
    assert origin is not None, "round 0 closes with the origin's individual"
    parts = [v for v in cycle.framing.to_dict().values() if v]
    if origin.answer_format:
        parts.append(origin.answer_format)
    if not parts:
        raise ValueError(
            "CAPO fills its templates with the task description, and this campaign declares no "
            "framing (task_context) and its origin no answer_format"
        )
    return "\n".join(parts)


def fill(cycle: Cycle, node: str, **values: str) -> str:
    body = cycle.optimizer.prompt_body(node, base=False)
    if body is None:
        raise KeyError(
            f"optimizer {cycle.optimizer.name!r}: node {node!r} names no resolved prompt"
        )
    return PromptTemplate(**body).compile_prompt(**values)


async def ask(ctx: RoundContext, node: str, idx: int | None, prompt: str) -> str:
    session = ctx.cycle.session
    response = await llm_call(
        [{"role": "user", "content": prompt}],
        node=node,
        context=LLMCallContext(
            ledger=session.state.ledger,
            round_num=ctx.round_num,
            candidate_idx=idx,
            cache=session.store.optimizer_reuse,
        ),
    )
    return response.content


def marked(text: str) -> str | None:
    """The last ``<prompt>`` block: an answer comes after any echo of the template's example."""
    found = [m.strip() for m in _PROMPT.findall(text) if m.strip()]
    return found[-1] if found else None


def unmarked(node: str, raw: str) -> ValidationFailure:
    return ValidationFailure(
        axis=f"{node}.output", value=raw[:300], allowed=[], reason=L1_PARSE_FAILURE_MALFORMED
    )


def walk_rng(cycle: Cycle, round_num: int, node: str) -> random.Random:
    """A function of the run's seed, the round and the node alone, so a resume redraws the same."""
    clamp = cycle.config.optimization.determinism
    return random.Random(f"{None if clamp is None else clamp.seed}:{round_num}:{node}")


async def initial_population(ctx: RoundContext, *, size: int, k_max: int) -> list[OptSearchPoint]:
    """App. D.2's instructions, ``size`` of them drawn at random, each given 0..k_max demo-pool
    shots at random (Alg. 1 lines 3-8). Every one derives from the origin, keeping its other fields."""
    cycle = ctx.cycle
    origin = cycle.origin_round.opt_sp
    assert origin is not None, "round 0 closes with the origin's individual"
    raw = await ask(
        ctx, INIT_NODE, None, fill(cycle, INIT_NODE, task_description=task_description(cycle))
    )
    start, end = raw.find("["), raw.rfind("]")
    listed = ast.literal_eval(raw[start : end + 1]) if 0 <= start < end else None
    if not isinstance(listed, list) or not all(isinstance(s, str) for s in listed):
        raise ValueError(f"{INIT_NODE} answered no array of instructions: {raw[:300]!r}")
    instructions = [s.strip() for s in listed if s.strip()]
    pool = [s.id for s in cycle.session.scoring.require_partition().demo]
    rng = walk_rng(cycle, ctx.round_num, INIT_NODE)
    drawn = rng.sample(instructions, min(size, len(instructions)))
    return [
        OptSearchPoint.derive(
            [origin],
            source=node_source(CAPO_MANIFEST, INIT_NODE),
            changes_description=f"initial instruction {n + 1}",
            instruction=text,
            shot_ids=rng.sample(pool, min(rng.randint(0, k_max), len(pool))),
        )
        for n, text in enumerate(drawn)
    ]
