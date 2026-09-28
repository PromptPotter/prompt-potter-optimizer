"""CAPO's node implementations, each registered under the node name its manifest uses."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, ClassVar, cast

from pydantic import Field

from promptpotter.application.campaign_config import Estimand, Knob, Scope
from promptpotter.application.optimizers import nodes
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import Panel, Population, RoundContext

__all__ = ["MEMBERS", "FewShotKnobs", "cross_shots", "mutate_shots"]


class FewShotKnobs(StrictModel):
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0, description="The most shots an individual may carry after the mutation."
    )


def mutate_shots(
    shots: Sequence[int], pool: Sequence[int], *, k_max: int, rng: random.Random
) -> list[int]:
    """Add a pool row not already carried while under *k_max*, drop one, or keep them — each with
    probability 1/3, a move with nothing to act on keeping instead — then shuffle the order."""
    out = list(shots)
    move = rng.randrange(3)
    unused = [i for i in pool if i not in out]
    if move == 0 and len(out) < k_max and unused:
        out.append(rng.choice(unused))
    elif move == 1 and out:
        out.pop(rng.randrange(len(out)))
    rng.shuffle(out)
    return out


def cross_shots(parents: Sequence[Sequence[int]], *, rng: random.Random) -> list[int]:
    """A crossover child's shots: a sample of the parents' union, as many as their mean count
    rounded down. The recombining node calls it, being the one that holds the parents."""
    union = list(dict.fromkeys(i for shots in parents for i in shots))
    n = sum(len(shots) for shots in parents) // len(parents)
    return rng.sample(union, min(n, len(union)))


class FewShot:
    """Mutates the shots of every individual the proposer before it made, off the demo pool. Its
    draw is a function of the run's seed, the round and the individual's position alone."""

    name: ClassVar[str] = "few_shot"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    knobs: ClassVar[type[StrictModel]] = FewShotKnobs
    couplings: ClassVar[tuple[nodes.MemberCoupling, ...]] = ()

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population:
        if population is None:
            raise ValueError(
                "few_shot edits the shots of individuals a proposer before it made; a manifest "
                "walking it first hands it none"
            )
        cycle = ctx.cycle
        k_max = cast("FewShotKnobs", cycle.optimizer.knobs(self.name)).k_max
        pool = [s.id for s in cycle.session.scoring.require_partition().demo]
        clamp = cycle.config.optimization.determinism
        seed = None if clamp is None else clamp.seed
        proposals, individuals = [], []
        for i, (proposal, individual) in enumerate(
            zip(population.proposals, population.individuals, strict=True)
        ):
            rng = random.Random(f"{seed}:{ctx.round_num}:{i}")
            shots = mutate_shots(individual.shot_ids, pool, k_max=k_max, rng=rng)
            mutated = individual.model_copy(update={"shot_ids": shots})
            proposals.append(proposal.model_copy(update={"opt_sp": mutated}))
            individuals.append(mutated)
        return replace(population, proposals=proposals, individuals=individuals)


MEMBERS = (FewShot(),)
