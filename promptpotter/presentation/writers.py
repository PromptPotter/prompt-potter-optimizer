"""Operator-facing markdown writers — log.md / review.md / hard_samples.json.

Called from runner orchestration milestones, NOT from RunCallbacks (which
must stay display-only per CLAUDE.md).

Two log.md tiers: ``cycles/{cycle_id}/log.md`` (per-cycle detail) and
``campaigns/{campaign_id}/log.md`` (the campaign digest — every cycle of
the lineage + a campaign-scoped heatmap, the folder-UI headline).

Owns the disk-side view reconstruction (``from_disk_round`` /
``from_disk_log``): persisted ``round_NNNN.json`` + ``index.json`` rebuild
into the same ``RoundCompleteView`` / ``LogMdView`` shapes the live ingress
emits, so ``to_markdown`` has one schema to render against.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.intelligence.exploration import build_observations
from promptpotter.application.intelligence.hard_sample_archive import (
    build_archive_hard_samples_artifact,
)
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact,
    build_hard_samples_artifact_from_observations,
)
from promptpotter.application.optimization.dispatch.hub import (
    format_l1_critique_for_prompt,
)
from promptpotter.application.review import render_review_md
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.base import write_json
from promptpotter.presentation.views.render import to_markdown
from promptpotter.presentation.views.view_ingress import (
    pick_round_winner,
    score_entry_from_dict,
)
from promptpotter.presentation.views.view_models import (
    DigestStatusView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundCompleteView,
    RoundDigestView,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.store import CampaignStore

__all__ = [
    "from_disk_log",
    "from_disk_round",
    "write_hard_samples_artifacts",
    "write_log_md",
    "write_review_md",
]


def _filter_artifact_to_live_candidates(artifact: dict, live_cids: set[str]) -> dict:
    """Restrict candidate_order + cells to ``live_cids`` (Y-axis hygiene).

    The Rasch fit stays joint (archive observations still contribute to
    ``δ_s``); only the displayed candidate axis is filtered.
    """
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


def write_hard_samples_artifacts(session: Session, cycle: Cycle) -> dict | None:
    """Build + persist the hard-sample heatmap artifacts at each data scope.

    Three files, named by data scope:

    - ``cycles/{cycle_id}/hard_samples.json`` — **cycle** scope: fit over
      ``cycle.rounds`` only.
    - ``campaigns/{campaign_id}/hard_samples.json`` — **campaign** scope:
      the cycle's rounds folded with the campaign's archive observations.
    - ``archive/measurements/hard_samples_{backend}_{dataset}.json`` —
      **dataset** scope: the archive snapshot for this backend + dataset.

    Returns the artifact selected for inline log.md rendering: the
    campaign-scope one when ``exploration.seed_heatmap_from_archive`` is on,
    otherwise the cycle-scope one. ``None`` when no observations exist yet.
    """
    if not session.state.cycle_id or session.store is None:
        return None

    store = session.store.campaigns
    campaign_id = session.campaign_id
    cycle_id = session.state.cycle_id
    cycle_dir = store.cycle_dir(campaign_id, cycle_id)
    campaign_dir = store.campaign_root_dir(campaign_id)

    cycle_artifact = build_hard_samples_artifact(
        cycle.rounds,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
        posterior=cycle.last_rasch_posterior,
    )

    live_obs = build_observations(cycle.rounds)
    campaign_obs = list(cycle.archive_observations) + live_obs
    campaign_artifact = build_hard_samples_artifact_from_observations(
        campaign_obs,
        cycle_id=cycle_id,
        top_k_candidates=None,
        top_k_samples=None,
    )

    exp_cfg = cycle.config.optimization.exploration
    if not exp_cfg.heatmap_show_archive_candidates:
        live_cids = {cid for rr in cycle.rounds for cid in rr.all_candidate_results}
        if live_cids:
            campaign_artifact = _filter_artifact_to_live_candidates(campaign_artifact, live_cids)

    with graceful("cycle hard_samples.json write failed"):
        write_json(cycle_dir / "hard_samples.json", cycle_artifact)
    with graceful("campaign hard_samples.json write failed"):
        write_json(campaign_dir / "hard_samples.json", campaign_artifact)

    # Dataset-scope snapshot is per-(backend, dataset) — cross-dataset
    # pooling would corrupt Rasch + PoBB picker (sample_id collides).
    dataset_tag = session.dataset_name or "unknown"
    tenant_path = (
        session.store.base_dir
        / "archive"
        / "measurements"
        / f"hard_samples_{session.backend_id}_{dataset_tag}.json"
    )
    with graceful("dataset hard_samples snapshot write failed"):
        archive_artifact = build_archive_hard_samples_artifact(
            session.store,
            session.backend_id,
            dataset_name=session.dataset_name,
            top_k_candidates=None,
            top_k_samples=None,
        )
        write_json(tenant_path, archive_artifact)

    return campaign_artifact if exp_cfg.seed_heatmap_from_archive else cycle_artifact


def from_disk_round(
    round_data: dict[str, Any],
    *,
    composite_fitness_formula: str | None = None,
    composite_fitness_formula_short: str | None = None,
    origin_composite_fitness: float | None = None,
) -> RoundCompleteView:
    """Reconstruct a ``RoundCompleteView`` from a persisted ``round_NNNN.json``."""
    score_entries = [score_entry_from_dict(s) for s in round_data.get("candidate_scores") or []]

    winner = pick_round_winner(score_entries)
    winner_label = winner.label if winner is not None else ""

    winner_acc = float(round_data.get("accuracy", 0.0))
    origin_acc = float(round_data.get("origin_accuracy", 0.0))
    matched_origin_acc = float(round_data.get("matched_origin_accuracy", origin_acc))
    matched_origin_hits = int(round_data.get("matched_origin_hits", 0))
    matched_origin_composite = round_data.get("matched_origin_composite")
    improved = bool(round_data.get("improved", False))
    return RoundCompleteView(
        round=int(round_data.get("round", 0)),
        origin_acc=origin_acc,
        scores=tuple(score_entries),
        winner_label=winner_label,
        winner_accuracy=winner_acc,
        winner_composite_fitness=round_data.get("composite_fitness"),
        winner_evaluators=dict(round_data.get("evaluators") or {}),
        winner_hits=int(round_data.get("hits", 0)),
        winner_total=int(round_data.get("total", 0)),
        improved=improved,
        delta=winner_acc - matched_origin_acc,
        p_value=round_data.get("p_value"),
        improved_reason=round_data.get("improved_reason"),
        next_action=str(round_data.get("next_action", "")),
        l1_critique_text=format_l1_critique_for_prompt(round_data.get("critique") or {}),
        composite_fitness_formula=composite_fitness_formula,
        composite_fitness_formula_short=composite_fitness_formula_short,
        origin_composite_fitness=origin_composite_fitness,
        matched_origin_accuracy=matched_origin_acc,
        matched_origin_hits=matched_origin_hits,
        matched_origin_composite=matched_origin_composite,
    )


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """Load per-sample P(best) snapshots from the JSONL stream for a single round."""
    if streams_dir is None or not streams_dir.is_dir():
        return {}, {}
    path = streams_dir / f"round_{round_num:04d}_p_best.jsonl"
    if not path.is_file():
        return {}, {}
    trajectory: dict[str, list[float]] = {}
    last_seen: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            qi = int(rec.get("sample_idx", -1))
            for cid, prob in (rec.get("p_best") or {}).items():
                trajectory.setdefault(str(cid), []).append(float(prob))
                last_seen[str(cid)] = qi
    except OSError:
        return {}, {}
    return trajectory, last_seen


def from_disk_log(
    index: dict[str, Any],
    rounds: list[dict[str, Any]],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
    sample_query_lookup: dict[int, str] | None = None,
    streams_dir: Path | None = None,
    fork_indices: list[dict[str, Any]] | None = None,
) -> LogMdView:
    """Build a ``log.md`` view from ``index.json`` + a list of round_data dicts.

    ``fork_indices`` is the list of sibling-cycle ``index.json`` blobs;
    rendered as the ``## Cycles`` section on the campaign digest. The
    per-cycle log.md passes ``None``.
    """
    final = index.get("final") or {}
    gen_only = sum(1 for t in rounds if str(t.get("status") or "") == "generation_only")
    status = DigestStatusView(
        campaign_id=str(index.get("cycle_id") or ""),
        parent_session_id=index.get("parent_session_id"),
        status=str(index.get("status", "active")),
        stop_reason=str(final.get("stop_reason") or index.get("stop_reason") or "(running)"),
        origin_accuracy=float(index.get("origin_accuracy", 0.0)),
        best_accuracy=float(index.get("best_accuracy", 0.0)),
        best_round=index.get("best_round"),
        rounds_completed=int(index.get("n_rounds", 0)),
        started_at=final.get("started_at"),
        finished_at=final.get("finished_at"),
        gen_only_rounds=gen_only,
    )

    round_views: list[RoundDigestView] = []
    for t in rounds:
        osp = t.get("opt_search_point") or {}
        lineage = osp.get("lineage") or {}
        rnd_raw = t.get("round")
        rnd = rnd_raw if isinstance(rnd_raw, int) else 0
        traj, stopped = _load_p_best_trajectory(streams_dir, rnd)
        round_views.append(
            RoundDigestView(
                round=rnd,
                label=str(t.get("label") or "").strip() or f"round_{rnd}",
                accuracy=float(t.get("accuracy", 0.0)),
                improved=bool(t.get("improved", False)),
                hits=int(t.get("hits", 0)),
                total=int(t.get("total", 0)),
                composite_fitness=float(t.get("composite_fitness", 0.0)),
                changes_description=(lineage.get("changes_description") or "").strip(),
                l1_critique_text=format_l1_critique_for_prompt(t.get("critique") or {}),
                l1_yield=float(t.get("l1_yield", 1.0)),
                l1_n_no_op=int(t.get("l1_n_no_op", 0)),
                l1_n_duplicate=int(t.get("l1_n_duplicate", 0)),
                candidates_scored=int(t.get("candidates_scored", 0)),
                evaluators=dict(t.get("evaluators") or {}),
                p_best_trajectory=traj,
                p_best_stopped=stopped,
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
            sample_query_lookup=dict(sample_query_lookup or {}),
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
        formula=final.get("scorer_round_formula"),
        origin_composite_fitness=final.get("origin_composite_fitness"),
        hard_samples=hard,
        final=final_view,
        forks=fork_views,
        family_best=family_best,
    )


def _fork_summary_from_index(fork_index: dict[str, Any]) -> ForkSummaryView:
    final = fork_index.get("final") or {}
    return ForkSummaryView(
        cycle_id=str(fork_index.get("cycle_id") or ""),
        mode=str(final.get("mode") or fork_index.get("sibling_kind") or ""),
        status=str(fork_index.get("status", "active")),
        best_accuracy=float(fork_index.get("best_accuracy", 0.0)),
        origin_accuracy=float(fork_index.get("origin_accuracy", 0.0)),
        n_rounds=int(fork_index.get("n_rounds", 0) or 0),
        stop_reason=str(final.get("stop_reason") or fork_index.get("stop_reason") or ""),
        finished_at=final.get("finished_at") or fork_index.get("finished_at"),
    )


def write_log_md(session: Session, *, hard_samples_artifact: dict | None = None) -> None:
    """Render the per-cycle log.md and refresh the campaign digest."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("log.md render failed"):
        store = session.store.campaigns
        campaign_id = session.campaign_id
        cycle_id = session.state.cycle_id
        _render_cycle_log_md(store, campaign_id, cycle_id, hard_samples_artifact)
        _render_campaign_log_md(store, campaign_id)


def _render_cycle_log_md(
    store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    hard_samples_artifact: dict | None,
) -> None:
    """Per-cycle ``cycles/{cycle_id}/log.md`` — this cycle's rounds only."""
    index = store.load(campaign_id, cycle_id)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(campaign_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
    cycle_dir = store.cycle_dir(campaign_id, cycle_id)
    streams_dir = cycle_dir / ".runtime" / "streams"
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hard_samples_artifact=hard_samples_artifact,
            streams_dir=streams_dir,
            fork_indices=None,
        )
    )
    (cycle_dir / "log.md").write_text(content, encoding="utf-8")


def _render_campaign_log_md(store: CampaignStore, campaign_id: str) -> None:
    """Campaign digest ``campaigns/{campaign_id}/log.md`` — the folder-UI headline.

    Anchored on the root cycle, with every other cycle of the lineage
    folded into the ``## Cycles`` section.
    """
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        return
    root_id = campaign.root_cycle_id
    index = store.load(campaign_id, root_id)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(campaign_id, root_id, 0, n_rounds - 1) if n_rounds else []
    cycle_dir = store.cycle_dir(campaign_id, root_id)
    streams_dir = cycle_dir / ".runtime" / "streams"
    fork_indices = _load_sibling_indices(store, campaign_id, exclude=root_id)
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            streams_dir=streams_dir,
            fork_indices=fork_indices,
        )
    )
    (store.campaign_root_dir(campaign_id) / "log.md").write_text(content, encoding="utf-8")


def _load_sibling_indices(
    store: CampaignStore, campaign_id: str, *, exclude: str
) -> list[dict] | None:
    """Every cycle ``index.json`` in the campaign except ``exclude``. ``None`` when empty."""
    cycles_dir = store.campaign_root_dir(campaign_id) / "cycles"
    if not cycles_dir.is_dir():
        return None
    out: list[dict] = []
    for cycle_dir in sorted(cycles_dir.iterdir()):
        if not cycle_dir.is_dir() or cycle_dir.name == exclude:
            continue
        idx = cycle_dir / "index.json"
        if not idx.is_file():
            continue
        try:
            blob = json.loads(idx.read_text(encoding="utf-8"))
            blob["cycle_id"] = cycle_dir.name
            out.append(blob)
        except (OSError, json.JSONDecodeError):
            continue
    return out or None


def write_review_md(session: Session, cycle: Cycle) -> None:
    """Render review.md from index + rounds + per-round audit JSONs."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        campaign_id = session.campaign_id
        cycle_id = session.state.cycle_id
        index = store.load(campaign_id, cycle_id)
        if not index:
            return
        n_rounds = int(index.get("n_rounds", 0) or 0)
        rounds = store.load_rounds_range(campaign_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
        cycle_dir = store.cycle_dir(campaign_id, cycle_id)
        round_audits = load_round_audits(cycle_dir, rounds)
        td = cycle.opt_sp.task_context
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
        )
        (cycle_dir / "review.md").write_text(content, encoding="utf-8")
