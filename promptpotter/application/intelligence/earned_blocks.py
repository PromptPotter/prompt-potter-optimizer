from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.scoring import ANSWER_SPACE_CAP, MeasuredCell
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.store.archive_queries import walked_answers
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_standing_rounds
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.infrastructure.store.layout import CampaignLayout, campaign_cycles_dir
from promptpotter.shared.measurement_context import instrument_mode

if TYPE_CHECKING:
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
        (
            MeasuredCell.from_wire(row).ground_truth
            for row in walked_answers(stores, walked.values())
        ),
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
) -> None:
    earned = [(cand, lift) for cand in closed.candidate_scores if (lift := _credible_lift(cand))]
    if not earned:
        return
    fit = _answer_space_signature(stores, closed, dataset)
    # Diffed against the individual the lift was READ against: a round's arms may have several parents.
    for cand, lift in earned:
        assert cand.vs_reference is not None and cand.vs_reference.a is not None
        reference = fields_of.get(cand.vs_reference.a.address.individual_id)
        if reference is None:
            continue
        delta = candidate_delta(cand.prompt_fields, reference, None, None)
        for field, text in delta.prompt.items():
            if field in _REUSABLE_FIELDS and (block := text.strip()):
                acc[(fit, field, block)].append(lift)


def mine_earned_blocks(stores: Stores) -> dict[str, list[EarnedBlock]]:
    """Empty under instrument mode: it reads the campaign TREE, which the evidence epoch never sees."""
    if instrument_mode() is not None:
        return {}

    acc: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for campaign_dir in stores.campaigns.iter_campaign_dirs():
        cycles_dir = campaign_cycles_dir(campaign_dir)
        if not cycles_dir.is_dir():
            continue
        # The manifest, never the directory name: a dataset name carrying `__` splits wrong.
        manifest = read_json_optional(CampaignLayout(campaign_dir).manifest)
        dataset = str((manifest or {}).get("dataset_name") or campaign_dir.name)
        for cycle_dir in sorted(p for p in cycles_dir.iterdir() if p.is_dir()):
            standing = scan_standing_rounds(ledger_chain(CycleDir(cycle_dir)))
            fields_of = {
                cand.candidate_id: cand.prompt_fields
                for held in standing.rounds.values()
                for cand in held.close.candidate_scores
            }
            for held in standing.rounds.values():
                _accumulate(stores, held.close, dataset, acc, fields_of)

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
