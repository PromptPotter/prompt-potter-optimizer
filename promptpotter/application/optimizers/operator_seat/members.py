"""The arm gate's seat, a preset every manifest walks as its `arm_gate`: an operator's arm is a child the bench admits as any other."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from promptpotter.application.bench.node_context import Children
from promptpotter.application.optimizers import nodes
from promptpotter.domain.opt_search_point import Locus, loci_of
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.pool import DemoPool
from promptpotter.domain.search_point import SHOTS_KEY
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from collections.abc import Mapping

    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.optimizers.nodes import CycleRead, Draw
    from promptpotter.domain.arms import Proposal
    from promptpotter.domain.command_kinds import SubmittedArm
    from promptpotter.domain.opt_search_point import OptSearchPoint, VariationMode
    from promptpotter.domain.pool import Pool
    from promptpotter.domain.strict_model import StrictModel


class OperatorSeat(nodes.Member):
    name: ClassVar[str] = "operator_seat"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    variation_mode: ClassVar[VariationMode] = "operator"
    knobs: ClassVar[type[StrictModel]] = nodes.ShotKnobs
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset({Children})
    draws: ClassVar[Mapping[type[Pool], Draw]] = {
        DemoPool: nodes.CountKnob(nodes.ShotKnobs, "k_max")
    }

    def author(self, ctx: NodeContext, parent: OptSearchPoint, arm: SubmittedArm) -> Proposal:
        # An undeclared node or field, or a shot the seat does not draw, fails at `Children.child`.
        k_max = ctx.knobs(nodes.ShotKnobs).k_max
        shots = {Locus(node, SHOTS_KEY): keys for node, keys in arm.shots.items()}
        over = [
            ValidationFailure(
                axis=locus.name,
                value=str(len(keys)),
                allowed=[str(k_max)],
                reason="shots_over_k_max",
            )
            for locus, keys in shots.items()
            if len(keys) > k_max
        ]
        return ctx.read(Children).child(
            [parent],
            overlay=arm.pipeline_overlay,
            failures=over,
            prompt_loci={**loci_of(arm.prompt_fields), **shots},
            changes_description=arm.changes_description,
        )


MEMBERS = (OperatorSeat(),)
