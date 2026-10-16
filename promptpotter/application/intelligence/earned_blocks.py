from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.scoring import ANSWER_SPACE_CAP
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.store.archive_queries import (
    as_of_moment,
    campaign_fenced,
    walked_answers,
)
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_standing_rounds

if TYPE_CHECKING:
    from promptpotter.domain.paired_reading import MemberAddress
    from promptpotter.domain.results import ScoredCandidate
    from promptpotter.domain.run_records import RoundClosedRecord
    from promptpotter.infrastructure.store.stores import Stores

__all__ = ["answer_space_signature", "earned_library_for", "mine_earned_blocks"]

# The long fields (instruction, problem_description) are task-specific, not transferable.
_REUSABLE_FIELDS: frozenset[str] = frozenset(
    {"persona", "task_intent", "thinking_style", "answer_format"}
)

# An open answer space is the ABSENCE of a shape: keyed by its dataset, it transfers nowhere else.
OPEN_ANSWER_SPACE = "OPEN"


class EarnedBlock:
    __slots__ = ("field", "mean_lift", "n", "text")

    def __init__(self, field: str, text: str, mean_lift: float, n: int) -> None:
        self.field = field
        self.text = text
        self.mean_lift = mean_lift
        self.n = n


def answer_space_signature(labels: Iterable[Any], *, dataset: str) -> str:
    distinct = {label for label in labels if isinstance(label, str) and label}
    if not distinct or len(distinct) > ANSWER_SPACE_CAP:
        return f"{OPEN_ANSWER_SPACE}:{dataset}"
    return "|".join(sorted(distinct))


def _answer_space_signature(stores: Stores, closed: RoundClosedRecord, dataset: str) -> str:
    walked = {cell[0]: cell for cells in closed.cells.arms.values() for cell in cells}
    return answer_space_signature(
        (cell.ground_truth for cell in walked_answers(stores, walked.values())),
        dataset=dataset,
    )


def _credible_lift(cand: ScoredCandidate) -> float | None:
    """Accuracy, never the composite: blocks pool across campaigns, each pricing cells its own way."""
    lift = cand.vs_reference.on_whole_set if cand.vs_reference else None
    if lift is None or lift.estimate.ci_lo <= 0:
        return None
    return lift.estimate.value


def _accumulate(
    stores: Stores,
    closed: RoundClosedRecord,
    dataset: str,
    acc: dict[tuple[str, str, str], list[float]],
    fields_of: Mapping[str, dict[str, Any]],
    counted: set[MemberAddress],
) -> None:
    earned: list[tuple[ScoredCandidate, str, float]] = []
    for cand in closed.candidate_scores:
        if (lift := _credible_lift(cand)) is None:
            continue
        reading = cand.vs_reference
        assert reading is not None and reading.a is not None and reading.b is not None
        # A reading is its address: a fork's chain and a repair's re-close each carry it again.
        if reading.b.address not in counted:
            counted.add(reading.b.address)
            earned.append((cand, reading.a.address.individual_id, lift))
    if not earned:
        return
    fit = _answer_space_signature(stores, closed, dataset)
    # Diffed against the individual the lift was READ against: a round's arms may have several parents.
    for cand, reference_id, lift in earned:
        reference = fields_of.get(reference_id)
        if reference is None:
            continue
        delta = candidate_delta(cand.prompt_fields, reference, None, None)
        for field, text in delta.prompt.items():
            if field in _REUSABLE_FIELDS and (block := text.strip()):
                acc[(fit, field, block)].append(lift)


def mine_earned_blocks(stores: Stores) -> dict[str, list[EarnedBlock]]:
    """The campaign TREE, so empty for an arm or an instrument's cell; a task re-read at a moment
    mines the tree as it stood then."""
    if campaign_fenced():
        return {}
    moment = as_of_moment()

    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    counted: set[MemberAddress] = set()
    for campaign in stores.campaigns.list_campaigns(lifecycle="all"):
        for cycle_dir in stores.campaigns.campaign_cycle_dirs(campaign.campaign_id):
            standing = scan_standing_rounds(ledger_chain(CycleDir(cycle_dir), moment=moment))
            fields_of = {
                cand.candidate_id: cand.prompt_fields
                for held in standing.rounds.values()
                for cand in held.close.candidate_scores
            }
            for held in standing.rounds.values():
                _accumulate(stores, held.close, campaign.dataset_name, acc, fields_of, counted)

    by_fit: dict[str, list[EarnedBlock]] = defaultdict(list)
    for (fit, field, block), lifts in acc.items():
        by_fit[fit].append(EarnedBlock(field, block, sum(lifts) / len(lifts), len(lifts)))
    for blocks in by_fit.values():
        blocks.sort(key=lambda b: b.mean_lift, reverse=True)
    return dict(by_fit)


def earned_library_for(
    stores: Stores, fit_signature: str, *, per_field_cap: int = 3
) -> dict[str, tuple[str, ...]]:
    earned = mine_earned_blocks(stores).get(fit_signature, [])
    by_field: dict[str, list[str]] = defaultdict(list)
    for block in earned:
        if len(by_field[block.field]) < per_field_cap:
            by_field[block.field].append(block.text)
    return {field: tuple(texts) for field, texts in by_field.items() if texts}
