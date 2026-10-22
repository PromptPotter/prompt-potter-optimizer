"""Called from runner milestones, never from RunCallbacks, which stay display-only."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.adaptive_queue_mechanism import parent_grades
from promptpotter.application.intelligence.hard_sample_sorter import (
    arm_thetas,
    build_hard_samples,
    rank_hard_samples,
    read_hard_samples,
)
from promptpotter.application.intelligence.rasch import build_observations
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.application.runner.review_md import render_review_md
from promptpotter.application.scoring.closed_rounds import closed_rounds
from promptpotter.application.views.render.markdown import to_markdown
from promptpotter.application.views.view_models import (
    DigestStatusView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.domain.cycle_listing import CycleIndex
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.results import HardSampleOrder, RoundResult
from promptpotter.domain.ruler import LevelSeries
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    LedgerSpend,
    scan_ledger_spend,
    scan_ledger_spend_by_round,
)
from promptpotter.infrastructure.store.io import write_json, write_text
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.infrastructure.tracing.mlflow_server import register_export
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.cells import HardSamples
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore


def write_hard_samples_artifacts(session: Session, cycle: Cycle) -> None:
    """The ONE ``hard_samples.json``: campaign scope reads one cycle's, dataset scope folds per request."""
    if not session.state.cycle_id:
        return

    ruler = cycle.difficulty.ruler
    cells = cycle.frontier
    frontier = cycle.difficulty.scale.frontier(cells)
    view = build_hard_samples(
        build_observations(cycle.rounds),
        ruler,
        frontier=None if frontier is None or frontier.se is None else (frontier.theta, frontier.se),
        arm_theta=arm_thetas(cycle.rounds),
        parent_grades=parent_grades(cells),
        slots=session.bank.slot_of,
        cycle_id=session.state.cycle_id,
    )
    with graceful("hard_samples.json write failed"):
        write_json(
            CycleLayout(session.store.campaigns.cycle_dir(session.hop)).hard_samples,
            view.model_dump(mode="json"),
        )


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """One stream line is ONE candidate's reading (``current_id``), never fanned across its map."""
    if streams_dir is None:
        return {}, {}
    trajectory: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    streams = sorted(streams_dir.glob(f"round_{round_num:04d}_*.jsonl"))
    for rec in (rec for stream in streams for rec in iter_jsonl(stream)):
        cid = str(rec.get("current_id") or "")
        if not cid:
            continue
        trajectory.setdefault(cid, []).append(float(rec["p_best"]))
        last_seen[cid] = int(rec.get("sample_idx", -1))
    return trajectory, last_seen


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
    spend_by_round: dict[int, SpendRollup],
    spent: LedgerSpend,
    level_series: LevelSeries | None,
    hard_samples: HardSamples | None = None,
    streams_dir: Path | None = None,
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
        traj, _ = _load_p_best_trajectory(streams_dir, t.round)
        head, fold, election = t.head, t.fold, t.election
        selected = None if election is None else next(iter(election.selected), None)
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

    final_view = (
        FinalWinnerView(
            result_prompt_fields=final.result_prompt_fields,
            result_pipeline_params=final.result_pipeline_params or {},
        )
        if final
        else None
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
        final=final_view,
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


def write_log_md(session: Session, config: CampaignConfig) -> None:
    """Called once more AFTER the run ends: the stop and the winner reach the ledger after the last round rendered."""
    if not session.state.cycle_id:
        return
    with graceful("log.md render failed"):
        _render_cycle_log_md(session, config)
        _render_campaign_log_md(session)


def _spend_by_round(layout: CycleLayout) -> dict[int, SpendRollup]:
    return scan_ledger_spend_by_round(ledger_chain(CycleDir(layout.cycle_dir), moment=None))


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
            hard_samples=read_hard_samples(layout.hard_samples),
            streams_dir=layout.streams,
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
            streams_dir=root_layout.streams,
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
    scoring = session.scoring
    # A run stopped before its origin closed has no round and may have no scorer either.
    if not session.store.campaigns.standing_rounds(hop, moment=None).rounds:
        return []
    return closed_rounds(session.store, hop, scoring.require_scorer(), moment=None)


def publish_export(session: Session) -> None:
    """Read back off disk, so the registry holds what ``export.json`` says and nothing newer."""
    stores = session.store
    # A sandbox's winners are the recursion's measurements, never an answer the operator asked for.
    if stores.projects_root != stores.shared_root:
        return
    with graceful("Prompt registry write-back failed"):
        export = stores.campaigns.read_export(session.hop)
        if export is not None:
            register_export(export, tenant_id=stores.tenant_id)


def write_review_md(
    session: Session,
    *,
    accuracy_ceiling: float | None,
    optimizer: SelectedOptimizer,
    framing: TaskDecomposition,
) -> None:
    if not session.state.cycle_id:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        index = store.load(session.hop, moment=None)
        if not index:
            return
        rounds = _banked_rounds(session, session.hop)
        cycle_dir = store.cycle_dir(session.hop)
        campaign = store.load_campaign(session.campaign_id)
        round_audits = load_round_audits(cycle_dir, [r.round for r in rounds])
        context_object = [
            framing.pipeline_purpose,
            framing.optimization_goals,
            framing.key_challenges,
        ]
        content = render_review_md(
            index,
            rounds,
            round_audits=round_audits,
            context_object=context_object,
            accuracy_ceiling=accuracy_ceiling,
            optimizer=optimizer,
            # Read only once the cycle has ended: nothing renders the headline before then.
            bench=read_cycle_bench(session.store, campaign, session.hop)
            if index.final is not None and campaign is not None
            else None,
            spend=scan_ledger_spend(ledger_chain(CycleDir(cycle_dir), moment=None)).spend,
        )
        write_text(CycleLayout(cycle_dir).review_md, content)
