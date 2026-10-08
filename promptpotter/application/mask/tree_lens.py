"""The lens folded over the served lineage tree — the counterfactual every entry point reads: one
record per course, the divergence markers, the dimmed subtree, each arm's value and sibling rank."""

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
    LineageDivergence,
    LineageNode,
    build_lineage_tree,
    rank_siblings,
)
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import Eliminator


def _abort_suppress(variant: str) -> frozenset[str]:
    """The gates an ``abort:`` variant switches off. Read per call: the member table completes at
    a declared step, never at import."""
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
    stores: Stores, tree: LineageNode, samples: frozenset[int] | None, lens: Lens | None
) -> dict[CyclePath, MaskRecord]:
    """One ``MaskRecord`` per campaign the tree spans, keyed by the course's OWN PATH: an inner
    campaign id is content-addressed on the CELL, so one id sits in several sibling ``.inner/`` sandboxes."""
    out: dict[CyclePath, MaskRecord] = {}

    def visit(node: LineageNode) -> None:
        if node.kind == "course" and node.path:
            path = tuple(node.path)
            if path not in out:
                leaf_store, leaf = resolve_cycle_path(stores, path)
                try:
                    out[path] = load_mask_record(leaf_store, leaf.campaign_id, samples, lens=lens)
                except (ValueError, SyntaxError, ScoringFormulaError) as exc:
                    raise BadRequestError(f"Invalid mask scoring formula: {exc}") from exc
        for kid in node.children:
            visit(kid)

    visit(tree)
    return out


class _Overlay:
    """The lens folded over the tree's records, keyed by COURSE PATH as :func:`_mask_records` keys
    them. A record is loaded AT one course, so every cycle it names shares that course's prefix."""

    def __init__(
        self,
        records: dict[CyclePath, MaskRecord],
        verdict: Verdict,
        *,
        serves_value: bool,
        serves_subset: bool,
    ):
        self.diverged: dict[tuple[CyclePath, int], LineageDivergence] = {}
        self.readings: dict[tuple[CyclePath, str], MaskReading | None] = {}
        self.serves_value = serves_value
        self.serves_subset = serves_subset
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
                for rnd_rec in cyc.rounds:
                    for cand in rnd_rec.candidates:
                        self.readings[(course, cand.candidate_id)] = cand.reading
        self.dimmed: frozenset[tuple[CyclePath, int]] = frozenset(dimmed)

    def apply(self, node: LineageNode) -> LineageNode:
        # Ranked here, not in the tree build: the lens is a property of the REQUEST, so its
        # values only exist once this fold has stamped them.
        kids = rank_siblings([self.apply(k) for k in node.children], "lens_value")
        if node.kind == "course" and node.path:
            criterion = self.criteria.get(tuple(node.path)) if self.serves_value else None
            return node.model_copy(update={"children": kids, "lens_criterion": criterion})
        if node.kind != "candidate" or node.round is None or not node.path:
            return node.model_copy(update={"children": kids})
        course = tuple(node.path)
        key = (course, node.round)
        read = (course, node.id) in self.readings
        reading = self.readings.get((course, node.id))
        return node.model_copy(
            update={
                "children": kids,
                # The marker sits on the SPINE node — the winner is who the lens would have
                # replaced, so it is the node the fork would have happened at.
                "divergence": self.diverged.get(key) if node.is_selected else None,
                "divergent": key in self.dimmed,
                "lens_value": (
                    reading.composite_fitness if reading and self.serves_value else None
                ),
                "sample_set_accuracy": (
                    reading.accuracy if reading and self.serves_subset else None
                ),
                "sample_set_n": (
                    (reading.n_scored if reading else 0) if read and self.serves_subset else None
                ),
            }
        )


def lensed_tree(
    stores: Stores, path: CyclePath, lens: Lens | None, samples: frozenset[int] | None
) -> LineageNode:
    """The lineage tree at *path* under *lens* and the sample-set mask *samples*; neither ⇒ the raw
    read. An unknown ``abort:`` variant or an ungradable formula is refused as the caller's input."""
    tree = build_lineage_tree(stores, path)
    if lens is None and not samples:
        return tree
    if lens is not None and lens.kind == "abort":
        # An abort lens reads the firing log rather than a score, so it loads the full set.
        verdict, samples = make_abort_verdict(_abort_suppress(lens.body)), None
    else:
        verdict = make_scoring_verdict()
    return _Overlay(
        _mask_records(stores, tree, samples, lens),
        verdict,
        serves_value=lens is not None and lens.kind != "abort",
        serves_subset=bool(samples),
    ).apply(tree)


__all__ = ["lensed_tree"]
