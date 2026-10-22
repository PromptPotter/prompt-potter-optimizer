from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from promptpotter.application.datasets.authored import scorer_of
from promptpotter.application.initialization.session import LaunchState
from promptpotter.application.pipeline_resolve import resolve_campaign_config
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.results import RoundOutcome, RoundResult
from promptpotter.domain.ruler import RulerScope
from promptpotter.domain.scoring import CellSheet, MeasuredCell, WalkedCell
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.archive_queries import walked_answers
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    StandingRound,
    scan_standing_rounds,
)
from promptpotter.infrastructure.store.io import unlink_robust, write_json
from promptpotter.infrastructure.store.layout import ROUND_GLOB, CycleLayout, round_number
from promptpotter.infrastructure.store.read_model import LedgerSpan, Moment, derived
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import NotFoundError, graceful

if TYPE_CHECKING:
    from promptpotter.domain.bench import PartitionRecord
    from promptpotter.domain.run_records import (
        RoundClosedRecord,
        RoundEnteredRecord,
        RoundRestatedRecord,
    )
    from promptpotter.domain.sample import Bank
    from promptpotter.domain.scoring import Scorer


def campaign_scorer(stores: Stores, campaign_id: str) -> Scorer | None:
    campaign = stores.campaigns.load_campaign(campaign_id)
    if campaign is None:
        return None
    return scorer_of(
        resolve_campaign_config(stores, campaign, campaign.root_hop), verifier_graded=False
    )


def campaign_ruler_scope(stores: Stores, campaign_id: str) -> RulerScope | None:
    """The scale a campaign's own cells are read on: its scorer over its own dataset."""
    campaign = stores.campaigns.load_campaign(campaign_id)
    scorer = campaign_scorer(stores, campaign_id)
    if campaign is None or scorer is None:
        return None
    return RulerScope(scorer=scorer.id, dataset=campaign.dataset_name)


def walked_rows(stores: Stores, cells: Sequence[WalkedCell], scorer: Scorer) -> CellSheet:
    """Replicates kept (one row per sample is ``CellSheet.standing``); the sheet is shared, so read-only."""
    replayed = {answer: was for _, answer, was in cells}

    def read(facts: MeasuredCell, was_replayed: bool) -> MeasuredCell:
        # Stamped BEFORE the grade, as the walk does: a formula may read the clock.
        return facts.replayed() if was_replayed else replace(facts, cached=False)

    def grade() -> CellSheet:
        return scorer.sheet(
            read(facts, replayed[answer])
            for facts in walked_answers(stores, cells)
            if (answer := facts.answer) is not None
        )

    held = derived(
        ("walked_rows", stores.archive.base_dir, scorer.id, tuple(cells)),
        sig=stores.archive.files_signature({answer.partition(".")[0] for answer in replayed}),
        compute=grade,
    )
    return held or scorer.sheet(())


def cycle_instrument(
    drawn: PartitionRecord | None, standing: Mapping[int, StandingRound]
) -> str | None:
    """The read-side spelling of ``Session.instrument_id``; a cycle that drew no bank names none."""
    if drawn is None:
        return None
    origin = standing.get(0)
    return instrument_of(
        drawn.dataset_hash,
        None if origin is None else origin.close.origin.individual.pipeline_params,
    )


def cycle_bank(stores: Stores, hop: CycleHop) -> Bank:
    drawn = stores.campaigns.read_bank_partition(hop)
    bank = None if drawn is None else stores.tenant_datasets.bank(drawn.dataset_hash)
    if bank is None:
        raise NotFoundError(f"cycle {hop.cycle_id} names no bank still held")
    return bank


def closed_rounds(
    stores: Stores,
    hop: CycleHop,
    scorer: Scorer,
    *,
    before_round: int | None = None,
    moment: Moment | None,
) -> list[RoundResult]:
    """What a round IS to a resume, a fork and a served read; ``rounds/round_NNNN.json`` is a checkout of it."""
    return [
        _with_rows(stores, hop, held, scorer)
        for n, held in stores.campaigns.standing_rounds(hop, moment=moment).rounds.items()
        if before_round is None or n < before_round
    ]


def closed_round(
    stores: Stores,
    hop: CycleHop,
    round_num: int,
    scorer: Scorer,
    *,
    moment: Moment | None,
) -> RoundResult | None:
    held = stores.campaigns.standing_rounds(hop, moment=moment).rounds.get(round_num)
    return None if held is None else _with_rows(stores, hop, held, scorer)


def _with_rows(stores: Stores, hop: CycleHop, held: StandingRound, scorer: Scorer) -> RoundResult:
    def rows(cells: list[WalkedCell]) -> CellSheet:
        walked = {key for key, _, _ in cells}
        taken = walked_rows(stores, cells, scorer)
        if len(taken) != len(walked):
            kept = {cell.key for cell in taken}
            raise NotFoundError(
                f"round {close.round} of cycle {hop.cycle_id} closed on answers the measurement "
                f"archive no longer holds (samples {sorted(walked - kept)}). Its rows "
                "cannot be read back; rewind before it (`resume --from`) to walk it again."
            )
        return taken

    close = held.close
    return RoundResult(
        **{name: getattr(close, name) for name in RoundOutcome.model_fields},
        at_offset=held.at_offset,
        cells={k: rows(v) for k, v in close.cells.arms.items()},
        overlap_results={k: rows(v) for k, v in close.cells.overlap.items()},
    )


class RoundFileProjection(Projection):
    """The ONE writer of ``rounds/round_NNNN.json``; a failed checkout is logged, the ledger holding the round."""

    def __init__(self, stores: Stores, hop: CycleHop, state: LaunchState) -> None:
        self._stores, self._hop, self._state = stores, hop, state
        self._layout = CycleLayout(stores.campaigns.cycle_dir(hop))
        self._ledger = stores.campaigns.ledger(hop)
        self._synced = False

    def _handle_round_entered(self, record: RoundEnteredRecord) -> None:
        if not self._layout.rounds.exists():
            return
        for path in sorted(self._layout.rounds.glob(ROUND_GLOB)):
            if (n := round_number(path)) is not None and n >= record.round:
                unlink_robust(path)

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:
        self._check_out(record.round)

    def _handle_round_restated(self, record: RoundRestatedRecord) -> None:
        self._check_out(record.round)

    def _check_out(self, round_num: int) -> None:
        with graceful(f"Round {round_num} checkout failed"):
            wanted = {round_num}
            if not self._synced:
                own = scan_standing_rounds([LedgerSpan(self._ledger)]).rounds
                wanted |= {n for n in own if not self._layout.round_file(n).exists()}
                self._synced = True
            scorer = self._state.scoring.require_scorer()
            for n in sorted(wanted):
                closed = closed_round(self._stores, self._hop, n, scorer, moment=None)
                if closed is not None:
                    write_json(self._layout.round_file(n), closed.model_dump(mode="json"))
