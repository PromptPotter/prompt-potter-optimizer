"""Operator-facing artifact WRITERS — ``log.md`` / ``review.md`` / ``hard_samples.json``, called from
runner milestones and NOT from RunCallbacks, which stay display-only. Every function here is
session-scoped and touches disk; the pure ``review.md`` renderer it calls is ``review_md.py``."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import build_observations
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact,
    build_hard_samples_artifact_from_observations,
)
from promptpotter.application.runner.campaign_result import read_cycle_bench
from promptpotter.application.runner.review_md import render_review_md
from promptpotter.application.views.render.markdown import to_markdown
from promptpotter.application.views.view_models import (
    DigestStatusView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.results import HardSampleOrder, RoundResult, order_floor
from promptpotter.domain.spend import SpendRollup
from promptpotter.infrastructure.ledger import ledger_chain
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.projections.live_dashboard.state import served_spend
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_spend
from promptpotter.infrastructure.store.campaign_store.store import (
    cycle_ending,
    cycle_final,
    origin_accuracy_of,
)
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json, write_text
from promptpotter.infrastructure.store.layout import (
    CampaignLayout,
    CycleLayout,
    campaign_cycles_dir,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

__all__ = [
    "write_hard_samples_artifacts",
    "write_log_md",
    "write_review_md",
]


def _filter_artifact_to_live_candidates(
    artifact: dict[str, Any], live_cids: set[str]
) -> dict[str, Any]:
    """Restrict candidate_order + cells to ``live_cids`` (Y-axis hygiene).
    Rasch fit stays joint (archive observations still contribute to δ_s); only the displayed axis is filtered."""
    filtered_order = [cid for cid in artifact["candidate_order"] if cid in live_cids]
    filtered_cells = [c for c in artifact["cells"] if c["c"] in live_cids]
    rasch = dict(artifact["rasch"])
    rasch["theta"] = {cid: rasch["theta"][cid] for cid in filtered_order if cid in rasch["theta"]}
    rasch["theta_se"] = {
        cid: rasch["theta_se"][cid] for cid in filtered_order if cid in rasch["theta_se"]
    }
    rasch["n_obs_per_candidate"] = {
        cid: rasch["n_obs_per_candidate"][cid]
        for cid in filtered_order
        if cid in rasch["n_obs_per_candidate"]
    }
    out = dict(artifact)
    out["candidate_order"] = filtered_order
    out["cells"] = filtered_cells
    out["rasch"] = rasch
    out["n_candidates"] = len(filtered_order)
    out["n_observations"] = len(filtered_cells)
    return out


def write_hard_samples_artifacts(session: Session, cycle: Cycle) -> None:
    """Build + persist the heatmap artifacts at cycle and campaign scope. There is no DATASET-scope
    file: that scope is cross-campaign, so no campaign owns it and the route folds it per request."""
    if not session.state.cycle_id:
        return

    store = session.store.campaigns
    cycle_id = session.state.cycle_id
    cycle_dir = store.cycle_dir(session.hop)
    campaign_dir = store.campaign_root_dir(session.campaign_id)

    cycle_artifact = build_hard_samples_artifact(
        cycle.rounds,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
    )

    live_obs = build_observations(cycle.rounds)
    campaign_obs = list(cycle.difficulty.observations) + live_obs
    campaign_artifact = build_hard_samples_artifact_from_observations(
        campaign_obs,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
    )

    # Archive candidates contribute to the joint Rasch fit but stay off the
    # heatmap Y-axis — it's filtered to this cycle's own cand_NNN.
    live_cids = {cid for rr in cycle.rounds for cid in rr.all_candidate_results}
    if live_cids:
        campaign_artifact = _filter_artifact_to_live_candidates(campaign_artifact, live_cids)

    with graceful("cycle hard_samples.json write failed"):
        write_json(CycleLayout(cycle_dir).hard_samples, cycle_artifact)
    with graceful("campaign hard_samples.json write failed"):
        write_json(CampaignLayout(campaign_dir).hard_samples, campaign_artifact)


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """``{candidate_id: [P(best) per query]}`` off the round's racing stream — one file, since a
    manifest walks at most one eliminator. One stream line is one candidate's reading, so fanning
    every key of its cid→prob map into a trajectory files the winner under its own defeat."""
    if streams_dir is None:
        return {}, {}
    trajectory: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    streams = sorted(streams_dir.glob(f"round_{round_num:04d}_*.jsonl"))
    for rec in (rec for stream in streams for rec in iter_jsonl(stream)):
        cid = str(rec.get("current_id") or "")
        if not cid:
            continue
        trajectory.setdefault(cid, []).append(float(rec.get("p_best") or 0.0))
        last_seen[cid] = int(rec.get("sample_idx", -1))
    return trajectory, last_seen


def _sample_queries(rounds: list[RoundResult]) -> dict[int, str]:
    """``{sample_id: query}`` harvested from the rounds already in hand — the rounds carry
    every sample's ``query``, so derive it rather than asking the caller."""
    out: dict[int, str] = {}
    for rr in rounds:
        for row in rr.results:
            sid, query = row.get("sample_id"), row.get("query")
            if isinstance(sid, int) and isinstance(query, str) and sid not in out:
                out[sid] = query
    return out


def from_disk_log(
    index: dict[str, Any],
    rounds: list[RoundResult],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
    streams_dir: Path | None = None,
    fork_indices: Sequence[dict[str, Any]] = (),
    hard_sample_order: HardSampleOrder = "info_gain",
    spend_by_round: dict[str, SpendRollup] | None = None,
) -> LogMdView:
    """``fork_indices`` is the sibling-cycle ``index.json`` blobs, rendered as ``## Forks`` on the
    campaign digest; the per-cycle log.md passes none.

    **Looks dead, is not** — no live caller reaches it during a run, because it exists for the
    cycles that HAVE no live ledger: a foreign fork sibling and a historical cycle, where
    ``index.json`` is the only source there is."""
    final = cycle_final(index)
    status = DigestStatusView(
        campaign_id=index["cycle_id"],
        parent_session_id=index.get("parent_session_id"),
        optimizer=next((t.optimizer_state.manifest for t in rounds), None),
        stop_reason=cycle_ending(index),
        origin_accuracy=origin_accuracy_of(index),
        best_accuracy=index["best_accuracy"],
        best_round=index.get("best_round"),
        rounds_completed=index["n_rounds"],
        started_at=final.started_at if final else None,
        finished_at=index.get("finished_at"),
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
                stamps_theta=t.stamps_theta,
                evaluators=dict(t.evaluators),
                reference_composite=selected.reference_composite if selected else None,
                ability=t.ability,
                verdict_reason=t.verdict_reason,
                overlap=t.overlap,
                p_best_trajectory=traj,
                candidate_labels={c.candidate_id: c.label for c in t.candidate_scores},
                winner_id=selected.candidate_id if selected else "",
                spend=(spend_by_round or {}).get(str(t.round)),
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
    hard = (
        HardSamplesView(
            artifact=dict(hard_samples_artifact),
            sample_query_lookup=_sample_queries(rounds),
            order=hard_sample_order,
        )
        if hard_samples_artifact
        else None
    )
    # Best first; a fork that measured nothing sorts under every one that did.
    fork_views = tuple(
        sorted(
            (_fork_summary_from_index(fi) for fi in fork_indices if fi["n_rounds"] > 0),
            key=lambda v: order_floor(v.best_accuracy),
            reverse=True,
        )
    )
    return LogMdView(
        status=status,
        rounds=tuple(round_views),
        # Stamped at run init, so a RUNNING cycle's digest names the formula its numbers carry.
        formula=index.get("scorer_cell_formula"),
        hard_samples=hard,
        final=final_view,
        forks=fork_views,
    )


def _fork_summary_from_index(fork_index: dict[str, Any]) -> ForkSummaryView:
    cycle_id: str = fork_index["cycle_id"]
    return ForkSummaryView(
        cycle_id=cycle_id,
        kind=sibling_kind(cycle_id),
        best_accuracy=fork_index["best_accuracy"],
        origin_accuracy=origin_accuracy_of(fork_index),
        n_rounds=fork_index["n_rounds"],
        stop_reason=cycle_ending(fork_index),
    )


def write_log_md(session: Session, config: CampaignConfig) -> None:
    """Render the per-cycle log.md and refresh the campaign digest — at every round's close, and
    once more when the run is stamped finished: `mark_finished` writes the stop, the finish time
    and the winner into `index.json` AFTER the last round rendered, so a digest left there reads
    `active` for good."""
    if not session.state.cycle_id:
        return
    with graceful("log.md render failed"):
        store = session.store.campaigns
        _render_cycle_log_md(store, session.hop, config)
        _render_campaign_log_md(store, session.campaign_id)


def _spend_by_round(layout: CycleLayout) -> dict[str, SpendRollup]:
    """The per-round spend split the browser's cost strip reads, so the ``log.md`` round line is
    the same number. Empty for a cycle whose dashboard serves none."""
    served = served_spend(read_json_tolerant(layout.dashboard, {}))
    return served[1] if served else {}


def _render_cycle_log_md(store: CampaignStore, hop: CycleHop, config: CampaignConfig) -> None:
    index = store.load(hop)
    if not index:
        return
    rounds = _banked_rounds(store, hop, index)
    layout = CycleLayout(store.cycle_dir(hop))
    # The heat map `write_hard_samples_artifacts` put on disk, at the scope the campaign reads it.
    hard_samples = (
        CampaignLayout(store.campaign_root_dir(hop.campaign_id)).hard_samples
        if config.optimization.seed_heatmap_from_archive
        else layout.hard_samples
    )
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hard_samples_artifact=read_json_tolerant(hard_samples),
            streams_dir=layout.streams,
            hard_sample_order=config.hard_sample_order,
            spend_by_round=_spend_by_round(layout),
        )
    )
    write_text(layout.log_md, content)


def _render_campaign_log_md(store: CampaignStore, campaign_id: str) -> None:
    """Campaign digest ``campaigns/{campaign_id}/log.md`` — the folder-UI headline, anchored on the
    root cycle with every other cycle of the lineage folded into ``## Cycles``."""
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
            _banked_rounds(store, root, index),
            streams_dir=root_layout.streams,
            fork_indices=_load_sibling_indices(store, campaign_id, exclude=root.cycle_id),
            spend_by_round=_spend_by_round(root_layout),
        )
    )
    write_text(CampaignLayout(store.campaign_root_dir(campaign_id)).log_md, content)


def _load_sibling_indices(
    store: CampaignStore, campaign_id: str, *, exclude: str
) -> list[dict[str, Any]]:
    """Every other cycle of the campaign, as ``CampaignStore.load`` answers for each — a survey, so
    one unreadable sibling is skipped rather than failing the digest."""
    cycles_dir = campaign_cycles_dir(store.campaign_root_dir(campaign_id))
    out: list[dict[str, Any]] = []
    for cycle_dir in sorted(p for p in cycles_dir.iterdir() if p.is_dir() and p.name != exclude):
        blob = read_json_tolerant(CycleLayout(cycle_dir).manifest)
        if isinstance(blob, dict):
            out.append({**blob, "cycle_id": cycle_dir.name})
    return out


def _banked_rounds(store: CampaignStore, hop: CycleHop, index: dict[str, Any]) -> list[RoundResult]:
    n_rounds: int = index["n_rounds"]
    return store.load_rounds_range(hop, 0, n_rounds - 1) if n_rounds else []


def write_review_md(session: Session, cycle: Cycle) -> None:
    if not session.state.cycle_id:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        index = store.load(session.hop)
        if not index:
            return
        rounds = _banked_rounds(store, session.hop, index)
        cycle_dir = store.cycle_dir(session.hop)
        round_audits = load_round_audits(cycle_dir, [r.round for r in rounds])
        td = cycle.framing
        context_object = [
            td.pipeline_purpose,
            td.optimization_goals,
            td.key_challenges,
        ]
        content = render_review_md(
            index,
            rounds,
            round_audits=round_audits,
            context_object=context_object,
            accuracy_ceiling=cycle.config.accuracy_ceiling,
            optimizer=cycle.optimizer,
            # Read only once the cycle has ended: nothing renders the headline before then.
            bench=read_cycle_bench(session.store, session.hop) if "final" in index else None,
            spend=scan_ledger_spend(ledger_chain(CycleDir(cycle_dir)))[0],
        )
        write_text(CycleLayout(cycle_dir).review_md, content)
