"""A cycle's registry export: the one ``ExportProjection``."""

from __future__ import annotations

from promptpotter.application.bench.line import own_level
from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.initialization.session import drawn_partition
from promptpotter.application.pipeline_resolve import resolved
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.export import build_prompt_export
from promptpotter.domain.run_standing import declared_selection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    LEDGER_SPEND,
    ClosedBenchFold,
)
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
from promptpotter.infrastructure.store.read_model import LedgerIndex, Projection
from promptpotter.infrastructure.store.stores import Stores


class ExportProjection(Projection):
    """The ONE writer of a cycle's ``export.json``, at its drainer's close: a run's or a pass's.
    *dataset* is the definition its drainer measures, whose task framing no record holds."""

    derives = "export.json"

    def __init__(self, stores: Stores, at: CampaignCycle, dataset: ResolvedDataset | None) -> None:
        self._stores, self._at, self._dataset = stores, at, dataset

    def drain(self) -> None:
        _render_export(self._stores, self._at, self._dataset)


def _render_export(stores: Stores, at: CampaignCycle, dataset: ResolvedDataset | None) -> None:
    """An ended cycle's winner as its ledger states it; a pause or a cycle no round closed has none."""
    store, hop = stores.campaigns, at.hop
    index = store.load(hop, moment=None)
    standing = store.standing_rounds(hop, moment=None)
    series = standing.level_series
    bench = LedgerIndex.of(store.ledger(hop)).view(ClosedBenchFold)
    if (
        index is None
        or index.final is None
        or index.stop_reason is None
        or index.finished_at is None
        or series is None
        or not standing.rounds
        or bench is None
    ):
        return
    # Off the records, as every reader grades it: the cycle's resolution and the bank it drew.
    res = resolved(stores, at)
    drawn = drawn_partition(stores.campaigns, hop)
    bank = stores.tenant_datasets.bank(drawn.dataset_hash)
    if bank is None:
        return
    scorer = res.scorer
    rounds = closed_rounds(stores, hop, scorer, moment=None)
    winner = declared_selection(rounds)
    export = build_prompt_export(
        winner,
        rounds,
        tool_version=APP_VERSION,
        campaign_id=hop.campaign_id,
        cycle_id=hop.cycle_id,
        dataset_name=at.campaign.dataset_name,
        dataset_hash=drawn.dataset_hash,
        treatment=at.campaign.treatment,
        stop_reason=index.stop_reason,
        finished_at=index.finished_at,
        formula=index.scorer_cell_formula,
        own=own_level(winner.results, scorer.fitness_unit),
        level=series.levels[winner.round],
        schema=res.schema,
        framing=campaign_framing(stores, res.config, dataset),
        demo=drawn.restored(bank.samples).demo,
        bench=bench,
        spend=store.read(hop, LEDGER_SPEND).spend,
    )
    store.write_export(hop, export)
