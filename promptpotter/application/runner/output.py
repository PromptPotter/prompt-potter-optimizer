"""A run's markdown artifacts and its registry export: the one ``RunArtifacts`` projection."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar

from promptpotter.application.bench.task_context import campaign_framing
from promptpotter.application.intelligence.hard_sample_sorter import (
    rank_hard_samples,
)
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.runner.bench import own_level
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.application.runner.review_md import render_review_md
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.scoring.measurement_log import cycle_hard_samples
from promptpotter.application.views.render.markdown import to_markdown
from promptpotter.application.views.view_models import (
    DigestStatusView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.config.settings import APP_VERSION
from promptpotter.domain.cells import HardSampleOrder
from promptpotter.domain.cycle_index import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.export import build_prompt_export
from promptpotter.domain.results import RoundResult
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.run_records import RaceStandingRecord
from promptpotter.domain.run_standing import declared_selection
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import round_audits
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    LedgerSpend,
    scan_closed_bench,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
)
from promptpotter.infrastructure.store.io import write_text
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex, RecordClasses

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.cells import HardSamples
    from promptpotter.domain.run_records import RoundClosedRecord
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


class _PBestTrajectories:
    """One standing is ONE candidate's reading (``current``), never fanned across its map."""

    records: ClassVar[RecordClasses] = (RaceStandingRecord,)

    def __init__(self) -> None:
        self._by_round: dict[int, dict[str, list[float]]] = {}

    def feed(self, record: RaceStandingRecord) -> None:
        held = self._by_round.setdefault(record.round, {})
        held.setdefault(record.current, []).append(record.p_best)

    def value(self) -> dict[int, dict[str, list[float]]]:
        return self._by_round


def _sample_queries(rounds: list[RoundResult]) -> dict[str, str]:
    out: dict[str, str] = {}
    for rr in rounds:
        for cell in rr.results:
            out.setdefault(cell.key, cell.facts.query)
    return out


def from_disk_log(
    index: CycleIndex,
    rounds: list[RoundResult],
    *,
    hop: CycleHop,
    spend_by_round: dict[int, SpendRollup],
    spent: LedgerSpend,
    level_series: LevelSeries | None,
    hard_samples: HardSamples | None = None,
    p_best: dict[int, dict[str, list[float]]] | None = None,
    fork_indices: Sequence[CycleIndex] = (),
    hard_sample_order: HardSampleOrder = "info_gain",
) -> LogMdView:
    final = index.final
    status = DigestStatusView(
        campaign_id=index.cycle_id,
        optimizer=next((t.closed_state.manifest for t in rounds), None),
        stop_reason=index.stop_reason,
        standing=index.standing,
        spent=spent,
        rounds_completed=len(index.rounds),
        started_at=final.started_at if final else None,
        finished_at=index.finished_at,
        gen_only_rounds=sum(1 for t in rounds if t.generation_only),
    )

    round_views: list[RoundDigestView] = []
    for t in rounds:
        traj = {} if p_best is None else p_best.get(t.round, {})
        head, fold, election = t.head, t.fold, t.election
        selected = None if election is None else election.selected
        fit = None if selected is None else t.fit(selected)
        round_views.append(
            RoundDigestView(
                round=t.round,
                label="" if head is None else head.label,
                fold=fold,
                improved=t.improved,
                changes_description="" if head is None else head.changes_description.strip(),
                facts=tuple(t.optimizer_facts),
                composite_floor=(
                    fit.vs_reference.reference_level("objective")
                    if fit and fit.vs_reference
                    else None
                ),
                level=None if level_series is None else level_series.reading(t.round),
                verdict_reason=None if election is None else election.verdict_reason,
                overlap=t.overlap,
                p_best_trajectory=traj,
                candidate_labels={key: arm.label for key, arm in t.panel.items()},
                winner_id=selected or "",
                spend=spend_by_round.get(t.round),
            )
        )

    hard: HardSamplesView | None = None
    if hard_samples is not None:
        measured = hard_samples.sample_order
        resolved, ranked = rank_hard_samples(
            hard_samples,
            {key: hard_samples.positions[key] for key in measured},
            measured=set(measured),
            order=hard_sample_order,
        )
        hard = HardSamplesView(
            artifact=hard_samples,
            sample_query_lookup=_sample_queries(rounds),
            order=resolved,
            ranked=tuple(ranked),
        )
    fork_views = tuple(_fork_summary_from_index(fi) for fi in fork_indices if fi.rounds)
    return LogMdView(
        status=status,
        rounds=tuple(round_views),
        # Declared at run init, so a RUNNING cycle's digest names the formula its numbers carry.
        formula=index.scorer_cell_formula,
        hard_samples=hard,
        # The winner is the package's to state, so an ended cycle's digest names where it is.
        exported=hop if final else None,
        forks=fork_views,
    )


def _fork_summary_from_index(fork_index: CycleIndex) -> ForkSummaryView:
    return ForkSummaryView(
        cycle_id=fork_index.cycle_id,
        kind=sibling_kind(fork_index.cycle_id),
        standing=fork_index.standing,
        n_rounds=len(fork_index.rounds),
        stop_reason=fork_index.stop_reason,
    )


class RunArtifacts(Projection):
    """The ONE writer of a cycle's ``log.md``, ``review.md`` and ``export.json`` and its campaign's ``log.md``:
    rendered off the ledger at each round close and once more at its drainer's close, where the
    stop and the winner have landed; a fault is its ledger's warning."""

    def __init__(self, session: Session, config: CampaignConfig) -> None:
        self._session, self._config = session, config

    def _handle_round_closed(self, record: RoundClosedRecord) -> None:  # noqa: ARG002 -- the close is the cue
        self._render()

    def drain(self) -> None:
        _render_export(self._session, self._config)
        self._render()

    def _render(self) -> None:
        _render_cycle_log_md(self._session, self._config)
        _render_campaign_log_md(self._session)
        _render_review_md(self._session, self._config)


def _spend_by_round(layout: CycleLayout) -> dict[int, SpendRollup]:
    return scan_ledger_spend_by_round(ledger_chain(CycleDir(layout.cycle_dir), moment=None))


def _p_best(layout: CycleLayout) -> dict[int, dict[str, list[float]]]:
    """The cycle's own standings: a fork's prefix is its parent's to report."""
    return LedgerIndex.of(layout.ledger, ()).view(_PBestTrajectories)


def _spent(layout: CycleLayout) -> LedgerSpend:
    return scan_ledger_spend(ledger_chain(CycleDir(layout.cycle_dir), moment=None))


def _render_cycle_log_md(session: Session, config: CampaignConfig) -> None:
    store, hop = session.store.campaigns, session.hop
    index = store.load(hop, moment=None)
    if not index:
        return
    rounds = _banked_rounds(session, hop)
    layout = CycleLayout(store.cycle_dir(hop))
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hop=hop,
            hard_samples=cycle_hard_samples(session.store, hop) if rounds else None,
            p_best=_p_best(layout),
            hard_sample_order=config.hard_sample_order,
            spend_by_round=_spend_by_round(layout),
            spent=_spent(layout),
            level_series=store.standing_rounds(hop, moment=None).level_series,
        )
    )
    write_text(layout.log_md, content)


def _render_campaign_log_md(session: Session) -> None:
    store, campaign_id = session.store.campaigns, session.campaign_id
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return
    root = campaign.root_hop
    index = store.load(root, moment=None)
    if not index:
        return
    root_layout = CycleLayout(store.cycle_dir(root))
    content = to_markdown(
        from_disk_log(
            index,
            _banked_rounds(session, root),
            hop=root,
            p_best=_p_best(root_layout),
            fork_indices=_load_sibling_indices(store, campaign_id, exclude=root.cycle_id),
            spend_by_round=_spend_by_round(root_layout),
            spent=_spent(root_layout),
            level_series=store.standing_rounds(root, moment=None).level_series,
        )
    )
    write_text(CampaignLayout(store.campaign_root_dir(campaign_id)).log_md, content)


def _load_sibling_indices(
    store: CampaignStore, campaign_id: str, *, exclude: str
) -> list[CycleIndex]:
    hops = (
        CycleHop(campaign_id=campaign_id, cycle_id=cycle_dir.name)
        for cycle_dir in store.campaign_cycle_dirs(campaign_id)
        if cycle_dir.name != exclude
    )
    return [index for hop in hops if (index := store.load(hop, moment=None)) is not None]


def _banked_rounds(session: Session, hop: CycleHop) -> list[RoundResult]:
    scoring = session.state.scoring
    # A run stopped before its origin closed has no round and may have no scorer either.
    if not session.store.campaigns.standing_rounds(hop, moment=None).rounds:
        return []
    return closed_rounds(session.store, hop, scoring.require_scorer(), moment=None)


def _render_export(session: Session, config: CampaignConfig) -> None:
    """An ended cycle's winner as its ledger states it; a pause or a cycle no round closed has none."""
    store, hop = session.store.campaigns, session.hop
    index = store.load(hop, moment=None)
    series = store.standing_rounds(hop, moment=None).level_series
    rounds = _banked_rounds(session, hop)
    campaign = store.load_campaign(session.campaign_id)
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
        treatment=None if campaign is None else campaign.treatment,
        stop_reason=index.stop_reason,
        finished_at=index.finished_at,
        formula=index.scorer_cell_formula,
        own=own_level(winner.results),
        level=series.reading(winner.round),
        schema=session.pipeline_schema,
        framing=campaign_framing(session.store, config, session.dataset),
        demo=session.state.scoring.require_partition().demo,
        bench=bench,
    )
    store.write_export(hop, export)


def _render_review_md(session: Session, config: CampaignConfig) -> None:
    store = session.store.campaigns
    index = store.load(session.hop, moment=None)
    rounds = _banked_rounds(session, session.hop)
    if not index or not rounds:
        return
    cycle_dir = store.cycle_dir(session.hop)
    campaign = store.load_campaign(session.campaign_id)
    framing = campaign_framing(session.store, config, session.dataset)
    content = render_review_md(
        index,
        rounds,
        round_audits=round_audits(cycle_dir, [r.round for r in rounds]),
        context_object=[
            framing.pipeline_purpose,
            framing.optimization_goals,
            framing.key_challenges,
        ],
        accuracy_ceiling=config.accuracy_ceiling,
        optimizer=select_optimizer(config.optimization),
        # Read only once the cycle has ended: nothing renders the headline before then.
        bench=read_cycle_bench(session.store, campaign, session.hop)
        if index.final is not None and campaign is not None
        else None,
        spend=scan_ledger_spend(ledger_chain(CycleDir(cycle_dir), moment=None)).spend,
    )
    write_text(CycleLayout(cycle_dir).review_md, content)
