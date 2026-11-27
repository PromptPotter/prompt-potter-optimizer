"""A cycle's registry export: the one ``ExportProjection``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.runner.bench import own_level
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.export import build_prompt_export
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_standing import declared_selection
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    scan_closed_bench,
    scan_ledger_spend,
)

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session


class ExportProjection(Projection):
    """The ONE writer of a cycle's ``export.json``, at its drainer's close: a run's or a pass's."""

    derives = "export.json"

    def __init__(self, session: Session, config: CampaignConfig) -> None:
        self._session, self._config = session, config

    def drain(self) -> None:
        _render_export(self._session, self._config)


def _banked_rounds(session: Session, hop: CycleHop) -> list[RoundResult]:
    scoring = session.state.scoring
    # A run stopped before its origin closed has no round and may have no scorer either.
    if not session.store.campaigns.standing_rounds(hop, moment=None).rounds:
        return []
    return closed_rounds(session.store, hop, scoring.scorer, moment=None)


def _render_export(session: Session, config: CampaignConfig) -> None:
    """An ended cycle's winner as its ledger states it; a pause or a cycle no round closed has none."""
    store, hop = session.store.campaigns, session.hop
    index = store.load(hop, moment=None)
    series = store.standing_rounds(hop, moment=None).level_series
    rounds = _banked_rounds(session, hop)
    bench = scan_closed_bench(store.ledger(hop))
    if (
        index is None
        or index.final is None
        or index.stop_reason is None
        or index.finished_at is None
        or series is None
        or not rounds
        or bench is None
    ):
        return
    winner = declared_selection(rounds)
    export = build_prompt_export(
        winner,
        rounds,
        tool_version=APP_VERSION,
        campaign_id=session.campaign_id,
        cycle_id=session.cycle_id,
        dataset_name=session.measured_dataset,
        dataset_hash=session.bank.dataset_hash,
        treatment=session.bound_campaign.treatment,
        stop_reason=index.stop_reason,
        finished_at=index.finished_at,
        formula=index.scorer_cell_formula,
        own=own_level(winner.results, session.state.scoring.scorer.fitness_unit),
        level=series.levels[winner.round],
        schema=session.pipeline_schema,
        framing=campaign_framing(session.store, config, session.dataset),
        demo=session.state.scoring.partition.demo,
        bench=bench,
        spend=scan_ledger_spend(store.chain(hop)).spend,
    )
    store.write_export(hop, export)
