"""Operator-facing markdown writers — log.md / review.md / hard_samples.json.

Called from runner milestones (not RunCallbacks — those stay display-only).
Two log.md tiers: per-cycle (``cycles/{cycle_id}/log.md``) + campaign
digest (``campaigns/{campaign_id}/log.md``).

Owns disk-side view reconstruction (``from_disk_log``): a persisted ``index.json``
→ the same ``LogMdView`` shape the live ingress emits, so ``to_markdown`` has one
schema. (The round twin, ``from_disk_round``, had no callers and is deleted.)

This is an **orchestrator** (computes artifacts + writes disk), so it lives in
``application/`` next to the runner that calls it — not in ``presentation/``,
whose entry-point shells must stay read-only over orchestration. It renders
through the presentation view layer (``to_markdown`` + the typed view models),
which is the pure data → text surface shared with the live ingress."""

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
from promptpotter.application.output.review import render_review_md
from promptpotter.application.views import (
    DigestStatusView,
    FinalWinnerView,
    ForkSummaryView,
    HardSamplesView,
    LogMdView,
    RoundDigestView,
    to_markdown,
)
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import RoundResult
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store.campaign_store.store import origin_accuracy_of
from promptpotter.infrastructure.store.io import read_json_tolerant, write_json, write_text
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.infrastructure.store import CampaignStore

__all__ = [
    "from_disk_log",
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
    """Build + persist the hard-sample heatmap artifacts at each data scope.

    Three files, named by data scope:

    - ``cycles/{cycle_id}/hard_samples.json`` — **cycle** scope: fit over
      ``cycle.rounds`` only.
    - ``campaigns/{campaign_id}/hard_samples.json`` — **campaign** scope:
      the cycle's rounds folded with the campaign's archive observations.
    - ``measurements/hard_samples_{backend}_{dataset}.json`` —
      **dataset** scope: the archive snapshot for this backend + dataset.

    Returns the artifact selected for inline log.md rendering: the
    campaign-scope one when ``optimization.seed_heatmap_from_archive`` is on,
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

    # Dataset-scope snapshot is per-(backend, dataset) — cross-dataset
    # pooling would corrupt Rasch + PoBB queue mechanism (sample_id collides).
    dataset_tag = session.dataset_name or "unknown"
    tenant_path = session.store.archive.dataset_snapshot_path(session.backend_id, dataset_tag)
    with graceful("dataset hard_samples snapshot write failed"):
        archive_artifact = build_archive_hard_samples_artifact(
            session.store,
            dataset_name=session.dataset_name,
            top_k_candidates=None,
            top_k_samples=None,
        )
        write_json(tenant_path, archive_artifact)

    return campaign_artifact if opt_cfg.seed_heatmap_from_archive else cycle_artifact


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
    rounds: list[RoundResult],
    *,
    hard_samples_artifact: dict[str, Any] | None = None,
    sample_query_lookup: dict[int, str] | None = None,
    streams_dir: Path | None = None,
    fork_indices: list[dict[str, Any]] | None = None,
) -> LogMdView:
    """Build a ``log.md`` view from ``index.json`` + the cycle's rounds.

    ``fork_indices`` is the list of sibling-cycle ``index.json`` blobs;
    rendered as the ``## Cycles`` section on the campaign digest. The
    per-cycle log.md passes ``None``.
    """
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
        lineage = t.opt_search_point.lineage if t.opt_search_point else None
        round_views.append(
            RoundDigestView(
                round=t.round,
                label=t.label.strip() or t.round_id,
                accuracy=t.accuracy,
                improved=t.improved,
                hits=t.hits,
                total=t.total,
                composite_fitness=t.composite_fitness,
                changes_description=(lineage.changes_description if lineage else "").strip(),
                l1_critique_text=format_l1_critique_for_prompt(t.critique),
                l1_yield=t.l1_yield,
                l1_n_no_op=t.l1_n_no_op,
                l1_n_duplicate=t.l1_n_duplicate,
                candidates_scored=t.candidates_scored,
                evaluators=dict(t.evaluators),
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
        campaign_id = session.campaign_id
        cycle_id = session.state.cycle_id
        _render_cycle_log_md(store, campaign_id, cycle_id, hard_samples_artifact)
        _render_campaign_log_md(store, campaign_id)


def _render_cycle_log_md(
    store: CampaignStore,
    campaign_id: str,
    cycle_id: str,
    hard_samples_artifact: dict[str, Any] | None,
) -> None:
    """Per-cycle ``cycles/{cycle_id}/log.md`` — this cycle's rounds only."""
    index = store.load(campaign_id, cycle_id)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(campaign_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
    layout = CycleLayout(store.cycle_dir(campaign_id, cycle_id))
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hard_samples_artifact=hard_samples_artifact,
            streams_dir=layout.streams,
            fork_indices=None,
        )
    )
    write_text(layout.log_md, content)


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
    streams_dir = CycleLayout(store.cycle_dir(campaign_id, root_id)).streams
    fork_indices = _load_sibling_indices(store, campaign_id, exclude=root_id)
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
    """Every cycle ``index.json`` in the campaign except ``exclude``. ``None`` when empty."""
    cycles_dir = store.campaign_root_dir(campaign_id) / "cycles"
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
