"""Called from runner milestones, never from RunCallbacks, which stay display-only."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.adaptive_queue_mechanism import parent_grades
from promptpotter.application.intelligence.exploration import build_observations
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples,
    rank_hard_samples,
    read_hard_samples,
    stamped_abilities,
)
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.application.runner.review_md import render_review_md
from promptpotter.application.scoring.cells import closed_rounds
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
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
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
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.domain.cells import HardSamples
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = [
    "write_hard_samples_artifacts",
    "write_log_md",
    "write_review_md",
]


def write_hard_samples_artifacts(session: Session, cycle: Cycle) -> None:
    """The ONE ``hard_samples.json``: campaign scope reads one cycle's, dataset scope folds per request."""
    if not session.state.cycle_id:
        return

    ruler = cycle.difficulty.ruler
    frontier, arm_theta = stamped_abilities(cycle.rounds, ruler)
    view = build_hard_samples(
        build_observations(cycle.rounds),
        ruler,
        frontier=frontier,
        arm_theta=arm_theta,
        parent_grades=parent_grades(cycle.tracking.current_results),
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


def _sample_queries(rounds: list[RoundResult]) -> dict[int, str]:
    out: dict[int, str] = {}
    for rr in rounds:
        for cell in rr.results:
            out.setdefault(cell.sample_id, cell.facts.query)
    return out


def from_disk_log(
    index: CycleIndex,
    rounds: list[RoundResult],
    *,
    hard_samples: HardSamples | None = None,
    streams_dir: Path | None = None,
    fork_indices: Sequence[CycleIndex] = (),
    hard_sample_order: HardSampleOrder = "info_gain",
    spend_by_round: dict[int, SpendRollup] | None = None,
) -> LogMdView:
    final = index.final
    status = DigestStatusView(
        campaign_id=index.cycle_id,
        optimizer=next((t.optimizer_state.manifest for t in rounds), None),
        stop_reason=index.stop_reason,
        standing=index.standing,
        rounds_completed=len(index.rounds),
        started_at=final.started_at if final else None,
        finished_at=index.finished_at,
        gen_only_rounds=sum(1 for t in rounds if t.generation_only),
    )

    round_views: list[RoundDigestView] = []
    for t in rounds:
        traj, _ = _load_p_best_trajectory(streams_dir, t.round)
        lineage = t.opt_sp.lineage if t.opt_sp else None
        selected = next(iter(t.selected_scores), None)
        round_views.append(
            RoundDigestView(
                round=t.round,
                label=t.label,
                accuracy=t.accuracy,
                improved=t.improved,
                total=t.total,
                composite_fitness=t.composite_fitness,
                changes_description=(lineage.changes_description if lineage else "").strip(),
                facts=tuple(t.optimizer_facts),
                evaluators=dict(t.evaluators),
                composite_floor=(
                    selected.vs_reference.reference_level("objective")
                    if selected and selected.vs_reference
                    else None
                ),
                ability=t.ability,
                verdict_reason=t.verdict_reason,
                overlap=t.overlap,
                p_best_trajectory=traj,
                candidate_labels={c.candidate_id: c.label for c in t.candidate_scores},
                winner_id=selected.candidate_id if selected else "",
                spend=(spend_by_round or {}).get(t.round),
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
            hard_samples, measured, measured=set(measured), order=hard_sample_order
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
    return scan_ledger_spend_by_round(ledger_chain(CycleDir(layout.cycle_dir)))


def _render_cycle_log_md(session: Session, config: CampaignConfig) -> None:
    store, hop = session.store.campaigns, session.hop
    index = store.load(hop)
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
        )
    )
    write_text(layout.log_md, content)


def _render_campaign_log_md(session: Session) -> None:
    store, campaign_id = session.store.campaigns, session.campaign_id
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return
    root = campaign.root_hop
    index = store.load(root)
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
    return [index for hop in hops if (index := store.load(hop)) is not None]


def _banked_rounds(session: Session, hop: CycleHop) -> list[RoundResult]:
    scoring = session.scoring
    # A run stopped before its origin closed has no round and may have no scorer either.
    if not session.store.campaigns.standing_rounds(hop).rounds:
        return []
    return closed_rounds(session.store, hop, scoring.require_scorer())


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
        index = store.load(session.hop)
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
            spend=scan_ledger_spend(ledger_chain(CycleDir(cycle_dir))).spend,
        )
        write_text(CycleLayout(cycle_dir).review_md, content)
