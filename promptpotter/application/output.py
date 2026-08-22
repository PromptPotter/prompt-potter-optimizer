"""Operator-facing artifact WRITERS — ``log.md`` / ``review.md`` / ``hard_samples.json``, called from
runner milestones and NOT from RunCallbacks, which stay display-only. Every function here is
session-scoped and touches disk; the pure ``review.md`` renderer it calls is ``review_md.py``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign_config import load_campaign_config
from promptpotter.application.intelligence.exploration import build_observations
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact,
    build_hard_samples_artifact_from_observations,
)
from promptpotter.application.review_md import render_review_md
from promptpotter.application.views.render import to_markdown
from promptpotter.application.views.view_models import (
    DigestStatusView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import HardSampleOrder, RoundResult
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json, write_text
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    campaign_cycles_dir,
    sibling_kind,
)
from promptpotter.infrastructure.store.read_model import iter_jsonl
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.cycle import Cycle
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


def write_hard_samples_artifacts(session: Session, cycle: Cycle) -> dict[str, Any] | None:
    """Build + persist the heatmap artifacts at cycle and campaign scope. There is no DATASET-scope
    file: that scope is cross-campaign, so no campaign owns it and the route folds it per request."""
    if not session.state.cycle_id or session.store is None:
        return None

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
    campaign_obs = list(cycle.archive_observations) + live_obs
    campaign_artifact = build_hard_samples_artifact_from_observations(
        campaign_obs,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
    )

    opt_cfg = cycle.config.optimization
    # Archive candidates contribute to the joint Rasch fit but stay off the
    # heatmap Y-axis — it's filtered to this cycle's own cand_NNN.
    live_cids = {cid for rr in cycle.rounds for cid in rr.all_candidate_results}
    if live_cids:
        campaign_artifact = _filter_artifact_to_live_candidates(campaign_artifact, live_cids)

    with graceful("cycle hard_samples.json write failed"):
        write_json(CycleLayout(cycle_dir).hard_samples, cycle_artifact)
    with graceful("campaign hard_samples.json write failed"):
        write_json(campaign_dir / "hard_samples.json", campaign_artifact)

    return campaign_artifact if opt_cfg.seed_heatmap_from_archive else cycle_artifact


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """``{candidate_id: [P(best) per query]}``. One stream line is one candidate's reading, so fanning
    every key of its cid→prob map into a trajectory files the winner under its own defeat."""
    if streams_dir is None:
        return {}, {}
    trajectory: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    for rec in iter_jsonl(streams_dir / f"round_{round_num:04d}_p_best.jsonl"):
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
    fork_indices: list[dict[str, Any]] | None = None,
    hard_sample_order: HardSampleOrder = "info_gain",
) -> LogMdView:
    """``fork_indices`` is the sibling-cycle ``index.json`` blobs, rendered as ``## Cycles`` on the
    campaign digest; the per-cycle log.md passes ``None``."""
    final = index.get("final") or {}
    status = DigestStatusView(
        campaign_id=str(index.get("cycle_id") or ""),
        parent_session_id=index.get("parent_session_id"),
        status=str(index.get("status", "active")),
        stop_reason=str(final.get("stop_reason") or index.get("stop_reason") or "(running)"),
        origin_accuracy=origin_accuracy_of(index) or 0.0,
        best_accuracy=float(index.get("best_accuracy", 0.0)),
        best_round=index.get("best_round"),
        rounds_completed=int(index.get("n_rounds", 0)),
        started_at=final.get("started_at"),
        finished_at=final.get("finished_at"),
        gen_only_rounds=sum(1 for t in rounds if t.status == "generation_only"),
    )

    round_views: list[RoundDigestView] = []
    for t in rounds:
        traj, _ = _load_p_best_trajectory(streams_dir, t.round)
        lineage = t.opt_sp.lineage if t.opt_sp else None
        round_views.append(
            RoundDigestView(
                round=t.round,
                label=t.label.strip() or t.round_id,
                accuracy=t.accuracy,
                improved=t.improved,
                total=t.total,
                composite_fitness=t.composite_fitness,
                changes_description=(lineage.changes_description if lineage else "").strip(),
                l1_critique_text=format_l1_critique_for_prompt(t.critique),
                l1_yield=t.l1_yield,
                l1_n_no_op=t.l1_n_no_op,
                l1_n_duplicate=t.l1_n_duplicate,
                l1_n_repeat=t.l1_n_repeat,
                candidates_scored=t.candidates_scored,
                evaluators=dict(t.evaluators),
                matched_parent_composite=t.matched_parent_composite,
                ability=t.ability,
                verdict_reason=t.verdict_reason,
                overlap=t.overlap,
                p_best_trajectory=traj,
            )
        )

    final_view = (
        FinalWinnerView(
            winner_prompt_fields=dict(final.get("winner_prompt_fields") or {}),
            winner_pipeline_params=dict(final.get("winner_pipeline_params") or {}),
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
    fork_views: tuple[ForkSummaryView, ...] = ()
    family_best: tuple[float, str] | None = None
    if fork_indices:
        live = [fi for fi in fork_indices if int(fi.get("n_rounds") or 0) > 0]
        fork_views = tuple(
            sorted(
                (_fork_summary_from_index(fi) for fi in live),
                key=lambda v: v.best_accuracy,
                reverse=True,
            )
        )
        candidates: list[tuple[float, str]] = [
            (float(index.get("best_accuracy", 0.0)), str(index.get("cycle_id") or ""))
        ]
        for fv in fork_views:
            candidates.append((fv.best_accuracy, fv.cycle_id))
        family_best = max(candidates, key=lambda c: c[0])

    return LogMdView(
        status=status,
        rounds=tuple(round_views),
        # Top-level key is the running cycle's copy, stamped at init; `final` only exists at stop.
        formula=final.get("scorer_round_formula") or index.get("scorer_round_formula"),
        hard_samples=hard,
        final=final_view,
        forks=fork_views,
        family_best=family_best,
    )


def _fork_summary_from_index(fork_index: dict[str, Any]) -> ForkSummaryView:
    final = fork_index.get("final") or {}
    cycle_id = str(fork_index.get("cycle_id") or "")
    return ForkSummaryView(
        cycle_id=cycle_id,
        mode=str(final.get("mode") or (sibling_kind(cycle_id) if cycle_id else "")),
        status=str(fork_index.get("status", "active")),
        best_accuracy=float(fork_index.get("best_accuracy", 0.0)),
        origin_accuracy=origin_accuracy_of(fork_index) or 0.0,
        n_rounds=int(fork_index.get("n_rounds", 0) or 0),
        stop_reason=str(final.get("stop_reason") or fork_index.get("stop_reason") or ""),
        finished_at=final.get("finished_at") or fork_index.get("finished_at"),
    )


def write_log_md(session: Session, *, hard_samples_artifact: dict[str, Any] | None = None) -> None:
    """Render the per-cycle log.md and refresh the campaign digest."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("log.md render failed"):
        store = session.store.campaigns
        _render_cycle_log_md(store, session.hop, hard_samples_artifact)
        _render_campaign_log_md(store, session.campaign_id)


def _render_cycle_log_md(
    store: CampaignStore,
    hop: CycleHop,
    hard_samples_artifact: dict[str, Any] | None,
) -> None:
    index = store.load(hop)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(hop, 0, n_rounds - 1) if n_rounds else []
    layout = CycleLayout(store.cycle_dir(hop))
    campaign = store.load_campaign(hop.campaign_id)
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hard_samples_artifact=hard_samples_artifact,
            streams_dir=layout.streams,
            fork_indices=None,
            # The typed knob, never a raw-dict key read: the manifest persists only the delta
            # from defaults, so an unset `hard_sample_order` is absent rather than spelled out.
            hard_sample_order=(
                load_campaign_config(campaign.config).hard_sample_order
                if campaign is not None
                else "info_gain"
            ),
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
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(root, 0, n_rounds - 1) if n_rounds else []
    streams_dir = CycleLayout(store.cycle_dir(root)).streams
    fork_indices = _load_sibling_indices(store, campaign_id, exclude=root.cycle_id)
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            streams_dir=streams_dir,
            fork_indices=fork_indices,
        )
    )
    write_text(store.campaign_root_dir(campaign_id) / "log.md", content)


def _load_sibling_indices(
    store: CampaignStore, campaign_id: str, *, exclude: str
) -> list[dict[str, Any]] | None:
    cycles_dir = campaign_cycles_dir(store.campaign_root_dir(campaign_id))
    if not cycles_dir.is_dir():
        return None
    out: list[dict[str, Any]] = []
    for cycle_dir in sorted(cycles_dir.iterdir()):
        if not cycle_dir.is_dir() or cycle_dir.name == exclude:
            continue
        blob = read_json_tolerant(CycleLayout(cycle_dir).manifest)
        if not isinstance(blob, dict):
            continue
        blob["cycle_id"] = cycle_dir.name
        out.append(blob)
    return out or None


def write_review_md(session: Session, cycle: Cycle) -> None:
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        index = store.load(session.hop)
        if not index:
            return
        n_rounds = int(index.get("n_rounds", 0) or 0)
        rounds = store.load_rounds_range(session.hop, 0, n_rounds - 1) if n_rounds else []
        cycle_dir = store.cycle_dir(session.hop)
        round_audits = load_round_audits(cycle_dir, [r.round for r in rounds])
        td = cycle.opt_sp.memory.task_context
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
            l1_patience=cycle.config.optimization.l1_patience,
        )
        write_text(CycleLayout(cycle_dir).review_md, content)
