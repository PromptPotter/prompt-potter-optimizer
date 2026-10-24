from __future__ import annotations

from typing import TYPE_CHECKING, cast

from promptpotter.application import optimizers
from promptpotter.application.mask.divergence import (
    Verdict,
    find_divergences,
    make_abort_verdict,
    make_scoring_verdict,
)
from promptpotter.application.mask.load import load_mask_record
from promptpotter.application.mask.record import Lens, MaskReading, MaskRecord
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, decode_cycle_path
from promptpotter.domain.hierarchy import (
    HierarchyCampaign,
    HierarchyCandidate,
    HierarchyCourse,
    HierarchyEntry,
    LensShift,
    LineageDivergence,
    RankMove,
)
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.infrastructure.store.stores import Stores, resolve_cycle_path
from promptpotter.shared.errors import BadRequestError

if TYPE_CHECKING:
    from promptpotter.application.optimizers.nodes import Eliminator


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


def _courses(entry: HierarchyEntry) -> list[HierarchyCourse]:
    out = [entry] if isinstance(entry, HierarchyCourse) else []
    for child in entry.children:
        out += _courses(child)
    return out


def _candidates(entry: HierarchyEntry) -> list[HierarchyCandidate]:
    """A course's own: its rounds' candidates, never an inner campaign's under them."""
    return [c for rnd in entry.children for c in rnd.children if isinstance(c, HierarchyCandidate)]


def _mask_records(
    stores: Stores, root: HierarchyCampaign, samples: frozenset[int] | None, lens: Lens | None
) -> dict[CyclePath, MaskRecord]:
    """One per campaign, keyed by a course's PATH: one inner campaign id sits in several sibling
    ``.inner/`` sandboxes, so the sandbox the path names is half its identity."""
    out: dict[CyclePath, MaskRecord] = {}
    seen: set[tuple[CyclePath, str]] = set()
    for course in _courses(root):
        path = decode_cycle_path(course.address)
        if (campaign := (path[:-1], path[-1].campaign_id)) in seen:
            continue
        seen.add(campaign)
        leaf_store, leaf = resolve_cycle_path(stores, path)
        out[path] = load_mask_record(leaf_store, leaf.campaign_id, samples, lens=lens)
    return out


def _composite(c: HierarchyCandidate) -> float | None:
    own = c.facts.reading.own
    return None if own is None or own.composite is None else own.composite.value


def _ranks(values: dict[str, float]) -> dict[str, int]:
    return {
        address: i + 1
        for i, (address, _) in enumerate(sorted(values.items(), key=lambda kv: (-kv[1], kv[0])))
    }


def _rank_moves(
    kids: list[HierarchyCandidate],
) -> tuple[dict[str, RankMove | None], LensShift | None]:
    by_composite = _ranks({k.address: v for k in kids if (v := _composite(k)) is not None})
    by_lens = _ranks({k.address: v for k in kids if (v := k.facts.lens_value) is not None})
    moves: dict[str, RankMove | None] = {}
    for k in kids:
        before, after = by_composite.get(k.address), by_lens.get(k.address)
        moves[k.address] = (
            None
            if before is None or after is None
            else "up"
            if after < before
            else "down"
            if after > before
            else "unchanged"
        )
    if not by_lens:
        return moves, None
    live = {k.address for k in kids if k.tag("superseded") is None}

    def first(ranks: dict[str, int]) -> str | None:
        top = next((address for address, rank in ranks.items() if rank == 1), None)
        return top if top in live else None

    top_composite = next((a for a, r in by_composite.items() if r == 1), None)
    top_lens = next((a for a, r in by_lens.items() if r == 1), None)
    return moves, LensShift(
        top_composite=first(by_composite),
        top_lens=first(by_lens),
        top_changed=top_composite is not None
        and top_lens is not None
        and top_composite != top_lens,
        moved_up=sum(moves[a] == "up" for a in live),
        moved_down=sum(moves[a] == "down" for a in live),
        unchanged=sum(moves[a] == "unchanged" for a in live),
    )


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
                        self.readings[(course, cand.key)] = cand.reading
        self.dimmed: frozenset[tuple[CyclePath, int]] = frozenset(dimmed)

    def apply(self, entry: HierarchyEntry) -> HierarchyEntry:
        if not isinstance(entry, HierarchyCourse):
            return entry.model_copy(update={"children": [self.apply(c) for c in entry.children]})
        path = decode_cycle_path(entry.address)
        read = {c.address: self._candidate(path, c) for c in _candidates(entry)}
        # Ranked here, not in the build: the lens values exist only once this fold stamps them.
        moves, shift = _rank_moves(list(read.values()))
        moved = {
            address: c.model_copy(
                update={"facts": c.facts.model_copy(update={"lens_rank_move": moves[address]})}
            )
            for address, c in read.items()
        }
        return entry.model_copy(
            update={
                "children": [
                    rnd.model_copy(update={"children": [moved[c.address] for c in rnd.children]})
                    for rnd in entry.children
                ],
                "facts": entry.facts.model_copy(
                    update={"lens_criterion": self.criteria.get(path), "lens_shift": shift}
                ),
            }
        )

    def _candidate(self, course: CyclePath, c: HierarchyCandidate) -> HierarchyCandidate:
        arm = c.facts.reading.arm
        key = (course, arm.round)
        read = (course, arm.key) in self.readings
        reading = self.readings.get((course, arm.key))
        lensed = self.criteria.get(course) is not None
        subset_n = (reading.n_scored if reading else 0) if read else None
        return c.model_copy(
            update={
                "children": [self.apply(k) for k in c.children],
                "facts": c.facts.model_copy(
                    update={
                        # On the crowned candidate: the winner is who the lens would have replaced.
                        "divergence": self.diverged.get(key)
                        if c.facts.reading.election.selected
                        else None,
                        "divergent": key in self.dimmed,
                        "lens_value": reading.composite_fitness if reading and lensed else None,
                        # A rate over part of the subset sat a different exam: short of it, the count alone.
                        "sample_set_accuracy": (
                            reading.accuracy if reading and subset_n == self.subset_size else None
                        ),
                        "sample_set_n": subset_n if self.subset_size is not None else None,
                    }
                ),
            }
        )


def lensed(
    stores: Stores,
    root: HierarchyCampaign,
    lens: Lens | None,
    samples: frozenset[int] | None,
) -> HierarchyCampaign:
    """The subtree as the request's lens and sample mask read it."""
    if lens is not None and lens.kind == "abort":
        # An abort lens reads the firing log, not a score, so it loads the full set.
        verdict, samples = make_abort_verdict(_abort_suppress(lens.body)), None
    else:
        verdict = make_scoring_verdict()
    overlay = _Overlay(
        _mask_records(stores, root, samples, lens),
        verdict,
        subset_size=len(samples) if samples else None,
    )
    out = overlay.apply(root)
    assert isinstance(out, HierarchyCampaign)
    return out
