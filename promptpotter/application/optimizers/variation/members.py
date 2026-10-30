"""Parent choice and recombination, any manifest's to name: what one optimizer built, every other uses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import Field

from promptpotter.application.bench.node_context import Children, Lineage, Replay, Target
from promptpotter.application.optimizers import nodes
from promptpotter.domain.campaign_config import Estimand, Knob, Scope
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import OptimizerFact
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    import random
    from collections.abc import Callable, Sequence

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.nodes import (
        CycleRead,
        Panel,
        PromptArity,
        Proposals,
        Proposing,
    )
    from promptpotter.domain.opt_search_point import Locus, OptSearchPoint, VariationMode
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import RoundResult


logger = logging.getLogger(__name__)

MatingPool = Literal["population", "measured"]


class MatingKnobs(StrictModel):
    offspring: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0,
        description="The offspring one round adds to the arms it is handed, each a main parent "
        "and a donor; 0 is crossover off. A child with no main, or no donor apart from its main, "
        "is dropped.",
    )
    donor_from: Annotated[MatingPool, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        description="What the DONOR, the parent giving a minority of loci, is drawn from, any "
        "individual but the main; the main, the line the child continues, is always drawn from the "
        "population. `population`: the individuals the selector carried. `measured`: every "
        "individual a closed round of the campaign measured, its losers included."
    )
    tournament: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=1,
        description="Entries drawn per parent, the one with the highest θ taken: every θ is read "
        "on the round's one scale over all the cells it was measured on, and an entry with no "
        "θ, or one under a caveat, ranks below every read one. 1 draws each parent uniformly at "
        "random, never by score.",
    )


def _fittest(
    drawn: Sequence[OptSearchPoint], rank: Callable[[OptSearchPoint], tuple[bool, float]] | None
) -> OptSearchPoint:
    # `max` keeps the first of a tie: draw order breaks it.
    return drawn[0] if rank is None else max(drawn, key=rank)


class Mating(nodes.Proposer):
    """Parent choice: each child is its main's copy naming the main, then the donor, added after the
    arms it is handed; the recombining nodes after it write it."""

    name: ClassVar[str] = "mating"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    proposes: ClassVar[Proposing] = "adds"
    variation_mode: ClassVar[VariationMode] = "deterministic"
    knobs: ClassVar[type[StrictModel]] = MatingKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Children, Lineage, Replay})

    def arms(self, selected: SelectedOptimizer) -> int:
        return selected.knobs_of(self.name, MatingKnobs).offspring

    async def propose(self, ctx: NodeContext, _panel: Panel, proposals: Proposals) -> Proposals:
        knobs = ctx.knobs(MatingKnobs)
        if knobs.offspring == 0:
            return proposals
        lineage = ctx.read(Lineage)
        mains = list(lineage.population)
        donors = list(lineage.population if knobs.donor_from == "population" else lineage.measured)

        def rank(individual: OptSearchPoint) -> tuple[bool, float]:
            theta = lineage.theta(individual.id)
            return (False, 0.0) if theta is None else (True, theta)

        by = rank if knobs.tournament > 1 else None
        rng = ctx.read(Replay).rng()
        pairs: list[tuple[OptSearchPoint, OptSearchPoint]] = []
        for _ in range(knobs.offspring if mains else 0):
            main = _fittest(rng.sample(mains, min(knobs.tournament, len(mains))), by)
            if rivals := [donor for donor in donors if donor.id != main.id]:
                drawn = rng.sample(rivals, min(knobs.tournament, len(rivals)))
                pairs.append((main, _fittest(drawn, by)))
        if dropped := knobs.offspring - len(pairs):
            logger.info(
                "%d offspring dropped: %d main(s), a child needing a %s donor apart from it",
                dropped,
                len(mains),
                knobs.donor_from,
            )
        children = ctx.read(Children)
        return nodes.Proposals(
            [
                *proposals.proposals,
                *(
                    children.child(
                        [main, donor],
                        changes_description=f"crossover of {main.id[:6]}, donor {donor.id[:6]}",
                    )
                    for main, donor in pairs
                ),
            ]
        )


def crossable(parents: Sequence[OptSearchPoint], schema: PipelineSchema) -> list[Locus]:
    """The loci the donor, the second parent, can give the main: each the two differ on, shots left
    to `shot_crossover`, a config locus the donor does not set having no value to give."""
    first, second = (parent.loci() for parent in parents)
    return [
        locus
        for locus in dict.fromkeys((*first, *second))
        if (kind := schema.locus_kind(*locus)) != "shots"
        and first.get(locus) != second.get(locus)
        and (kind != "config" or locus in second)
    ]


def most_donated(crossable: int) -> int:
    """The most loci a donor gives a pair differing on *crossable*: under half, so the main stays
    the major contributor, and one where they differ on two. 0 under two: any child is a parent."""
    return 0 if crossable < 2 else max(1, (crossable - 1) // 2)


def donor_take(
    parents: Sequence[OptSearchPoint], schema: PipelineSchema, rng: random.Random
) -> dict[Locus, int] | None:
    """The take the donor gives: k crossable loci drawn at random, k uniform over 1 to
    `most_donated`. None where under two are crossable."""
    loci = crossable(parents, schema)
    if not (most := most_donated(len(loci))):
        return None
    return dict.fromkeys(rng.sample(loci, rng.randint(1, most)), 1)


class LocusCrossover(nodes.Proposer):
    """Crossover of each child naming a main and a donor, every other passed on untouched: the donor
    gives a minority of loci (`donor_take`), each value a parent's, so it invents nothing."""

    name: ClassVar[str] = "locus_crossover"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    prompt_arity: ClassVar[PromptArity] = "each"
    proposes: ClassVar[Proposing] = "edits"
    variation_mode: ClassVar[VariationMode] = "deterministic"
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Children, Lineage, Replay, Target})

    async def propose(self, ctx: NodeContext, _panel: Panel, proposals: Proposals) -> Proposals:
        rng = ctx.read(Replay).rng()
        lineage, children = ctx.read(Lineage), ctx.read(Children)
        schema = ctx.read(Target).schema
        out, clones = [], []
        for proposal in proposals.proposals:
            if len(proposal.individual.lineage.parent_ids) != 2:
                out.append(proposal)
                continue
            if (take := donor_take(lineage.parents(proposal.individual), schema, rng)) is None:
                clones.append(proposal.individual.lineage.changes_description)
                continue
            out.append(children.edit(proposal, take=take))
        if clones:
            logger.info(
                "%d offspring dropped, each pair under two crossable loci: %s", len(clones), clones
            )
        return nodes.Proposals(out)


class SurvivalKnobs(StrictModel):
    size: Annotated[int, Knob(Scope.POLICY, Estimand.SELECTION, Estimand.STOPPING)] = Field(
        ge=1,
        description="μ, the individuals carried into the next round: the selector's ranking, "
        "best first, cut to this many. A race reading μ cuts an arm once μ others are "
        "significantly better, since it can no longer be among them.",
    )


class Survival(nodes.Survivor):
    """Top-μ survival over whatever the selector ranks: the selector orders, this node cuts."""

    name: ClassVar[str] = "survival"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    knobs: ClassVar[type[StrictModel]] = SurvivalKnobs

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        if round_result.round == 0:
            return []
        kept = len(round_result.closed_state.population)
        size = selected.knobs_of(self.name, SurvivalKnobs).size
        return [
            OptimizerFact(
                key="population",
                label="Population",
                text=f"{kept} kept (μ {size})",
                value=kept,
                kind="stat",
            )
        ]

    def survive(
        self, ctx: NodeContext, ranked: Sequence[OptSearchPoint]
    ) -> Sequence[OptSearchPoint]:
        return ranked[: ctx.knobs(SurvivalKnobs).size]


MEMBERS = (Mating(), LocusCrossover(), Survival())
