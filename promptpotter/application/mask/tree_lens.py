from __future__ import annotations

from typing import TYPE_CHECKING, cast

from promptpotter.application import optimizers
from promptpotter.application.mask.divergence import Verdict, find_divergences
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import Lens, MaskReading, MaskRecord
from promptpotter.application.mask.verdicts import make_abort_verdict, make_scoring_verdict
from promptpotter.application.scoring.formula import ScoringFormulaError
from promptpotter.domain.cycle_paths import CycleHop, CyclePath
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.infrastructure.store.lineage_queries import (
    ArmNode,
    CourseNode,
    LineageDivergence,
    build_lineage_tree,
    rank_moves,
)
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import Eliminator
    from promptpotter.infrastructure.store.read_model import Moment


def _abort_suppress(variant: str) -> frozenset[str]:
    """Read per call: the member table completes at a declared step, never at import."""
    variants = {
        name: gates
        for member in optimizers.registered().values()
        if member.kind is NodeKind.ELIMINATOR
        for name, gates in cast("Eliminator", member).abort_lenses.items()
    }
    if variant not in variants:
        raise BadRequestError(
            f"Unknown abort lens: {variant!r} (expected one of {sorted(variants)})"
        )
    return variants[variant]


def _mask_records(
    stores: Stores, tree: CourseNode, samples: frozenset[int] | None, lens: Lens | None
) -> dict[CyclePath, MaskRecord]:
    """Keyed by the course's PATH: one inner campaign id sits in several sibling ``.inner/`` sandboxes."""
    out: dict[CyclePath, MaskRecord] = {}

    def visit(node: CourseNode) -> None:
        path = tuple(node.path)
        if path not in out:
            leaf_store, leaf = resolve_cycle_path(stores, path)
            try:
                out[path] = load_mask_record(leaf_store, leaf.campaign_id, samples, lens=lens)
            except (ValueError, SyntaxError, ScoringFormulaError) as exc:
                raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc
        for arm in node.children:
            for run in arm.children:
                visit(run)

    visit(tree)
    return out


class _Overlay:
    def __init__(
        self,
        records: dict[CyclePath, MaskRecord],
        verdict: Verdict,
        *,
        subset_size: int | None,
    ):
        self.diverged: dict[tuple[CyclePath, int], LineageDivergence] = {}
        self.readings: dict[tuple[CyclePath, str], MaskReading | None] = {}
        self.subset_size = subset_size
        # Presence is what serves a `lens_value`: an `abort:` lens and a bare sample mask name none.
        self.criteria = {path: record.criterion for path, record in records.items()}
        dimmed: set[tuple[CyclePath, int]] = set()
        for path, record in records.items():
            sandbox, campaign_id = path[:-1], path[-1].campaign_id
            result = find_divergences(record, verdict)
            for d in result.divergences:
                hop = CycleHop(campaign_id=campaign_id, cycle_id=d.cycle_id)
                self.diverged[((*sandbox, hop), d.round)] = LineageDivergence(
                    alternative_candidate_id=d.alternative_candidate_id
                )
            dimmed.update(
                ((*sandbox, CycleHop(campaign_id=campaign_id, cycle_id=cid)), rnd)
                for cid, rnd in result.divergent
            )
            for cyc in record.cycles:
                course = (*sandbox, CycleHop(campaign_id=campaign_id, cycle_id=cyc.cycle_id))
                self.criteria.setdefault(course, record.criterion)
                for rnd_rec in cyc.rounds:
                    for cand in rnd_rec.candidates:
                        self.readings[(course, cand.candidate_id)] = cand.reading
        self.dimmed: frozenset[tuple[CyclePath, int]] = frozenset(dimmed)

    def apply(self, node: CourseNode) -> CourseNode:
        # Ranked here, not in the tree build: the lens values exist only once this fold stamps them.
        kids, shift = rank_moves([self._arm(k) for k in node.children])
        return node.model_copy(
            update={
                "children": kids,
                "lens_criterion": self.criteria.get(tuple(node.path)),
                "lens_shift": shift,
            }
        )

    def _arm(self, node: ArmNode) -> ArmNode:
        kids = [self.apply(k) for k in node.children]
        course = tuple(node.path)
        key = (course, node.round)
        read = (course, node.id) in self.readings
        reading = self.readings.get((course, node.id))
        lensed = self.criteria.get(course) is not None
        subset_n = (reading.n_scored if reading else 0) if read else None
        return node.model_copy(
            update={
                "children": kids,
                # On the SPINE node: the winner is who the lens would have replaced.
                "divergence": self.diverged.get(key) if node.reading.election.selected else None,
                "divergent": key in self.dimmed,
                "lens_value": reading.composite_fitness if reading and lensed else None,
                # A rate over part of the subset sat a different exam: short of the set, the count alone.
                "sample_set_accuracy": (
                    reading.accuracy if reading and subset_n == self.subset_size else None
                ),
                "sample_set_n": subset_n if self.subset_size is not None else None,
            }
        )


def lensed_tree(
    stores: Stores,
    path: CyclePath,
    lens: Lens | None,
    samples: frozenset[int] | None,
    moment: Moment | None = None,
) -> CourseNode:
    tree = build_lineage_tree(stores, path, moment)
    if lens is None and not samples:
        return tree
    if lens is not None and lens.kind == "abort":
        # An abort lens reads the firing log, not a score, so it loads the full set.
        verdict, samples = make_abort_verdict(_abort_suppress(lens.body)), None
    else:
        verdict = make_scoring_verdict()
    return _Overlay(
        _mask_records(stores, tree, samples, lens),
        verdict,
        subset_size=len(samples) if samples else None,
    ).apply(tree)


__all__ = ["CourseNode", "lensed_tree"]
