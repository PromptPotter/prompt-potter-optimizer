"""Operator-facing markdown writers — log.md / review.md / archive/*.md.

Called from runner orchestration milestones, NOT from RunCallbacks (which
must stay display-only per CLAUDE.md).

Owns the disk-side view reconstruction (``from_disk_round`` /
``from_disk_log``): persisted ``trial_NNNN.json`` + ``index.json`` rebuild
into the same ``RoundCompleteView`` / ``LogMdView`` shapes the live ingress
emits, so ``to_markdown`` has one schema to render against. Shared
score-entry helpers come from ``views.view_ingress``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimization.dispatch.hub import (
    format_l1_critique_for_prompt,
)
from promptpotter.application.review import render_review_md
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store import root_cycle_id
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

__all__ = ["from_disk_log", "from_disk_round", "write_log_md", "write_review_md"]


def from_disk_round(
    round_data: dict[str, Any],
    *,
    composite_fitness_formula: str | None = None,
    composite_fitness_formula_short: str | None = None,
    origin_composite_fitness: float | None = None,
) -> RoundCompleteView:
    """Reconstruct a ``RoundCompleteView`` from a persisted ``trial_NNNN.json``."""
    score_entries = [score_entry_from_dict(s) for s in round_data.get("candidate_scores") or []]

    winner = pick_round_winner(score_entries)
    winner_label = winner.label if winner is not None else ""

    winner_acc = float(round_data.get("accuracy", 0.0))
    origin_acc = float(round_data.get("origin_accuracy", 0.0))
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
        delta=winner_acc - origin_acc,
        p_value=round_data.get("p_value"),
        improved_reason=round_data.get("improved_reason"),
        next_action=str(round_data.get("next_action", "")),
        l1_critique_text=format_l1_critique_for_prompt(round_data.get("critique") or {}),
        composite_fitness_formula=composite_fitness_formula,
        composite_fitness_formula_short=composite_fitness_formula_short,
        origin_composite_fitness=origin_composite_fitness,
    )


def _load_p_best_trajectory(
    streams_dir: Path | None, round_num: int
) -> tuple[dict[str, list[float]], dict[str, int]]:
    """Load per-sample P(best) snapshots from the JSONL stream for a single round.

    Returns ``(trajectory, stopped_at)`` — trajectory is candidate_id →
    list of P(best) values across queries; stopped_at is candidate_id →
    last-query-idx-before-the-current-candidate-stopped, populated when a
    candidate's value drops to / below ``ε`` (best-effort: the stream
    doesn't carry ε, so we infer "stopped" as the query at which the
    current candidate's snapshot disappears).
    """
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
    """Build the ``log.md`` view from ``index.json`` + a list of round_data dicts.

    ``fork_indices`` is the list of fork ``index.json`` blobs (one per fork
    of the family); rendered as the ``## Forks`` section on the family-root
    log.md. Forks themselves pass ``None`` and get an empty tuple.
    """
    final = index.get("final") or {}
    gen_only = sum(1 for t in rounds if str(t.get("status") or "") == "generation_only")
    status = DigestStatusView(
        campaign_id=str(index.get("campaign_id") or ""),
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
        # Drop uninitialized forks (created but never ran a round) — their
        # all-zeros row is noise. Sort survivors by best desc so the top
        # contender surfaces first.
        live = [fi for fi in fork_indices if int(fi.get("n_rounds") or 0) > 0]
        fork_views = tuple(
            sorted(
                (_fork_summary_from_index(fi) for fi in live),
                key=lambda v: v.best_accuracy,
                reverse=True,
            )
        )
        # Family-best across the root + all forks. Cycle id of the holder
        # disambiguates which sibling carries the headline.
        candidates: list[tuple[float, str]] = [
            (float(index.get("best_accuracy", 0.0)), str(index.get("campaign_id") or ""))
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
        cycle_id=str(fork_index.get("campaign_id") or ""),
        mode=str(final.get("mode") or fork_index.get("fork_kind") or ""),
        status=str(fork_index.get("status", "active")),
        best_accuracy=float(fork_index.get("best_accuracy", 0.0)),
        origin_accuracy=float(fork_index.get("origin_accuracy", 0.0)),
        n_rounds=int(fork_index.get("n_rounds") or fork_index.get("n_rounds", 0) or 0),
        stop_reason=str(final.get("stop_reason") or fork_index.get("stop_reason") or ""),
        finished_at=final.get("finished_at") or fork_index.get("finished_at"),
    )


def write_log_md(session: Session, *, hard_samples_artifact: dict | None = None) -> None:
    """Render log.md for the active cycle; on a fork, also refresh the family root's."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("log.md render failed"):
        store = session.store.campaigns
        cycle_id = session.state.cycle_id
        _render_log_md_for(store, session.backend_id, cycle_id, hard_samples_artifact)
        root_id = root_cycle_id(cycle_id)
        if root_id != cycle_id:
            # Fork finished — refresh the family root's log.md so its Forks
            # section picks up this fork's latest best/origin/stop_reason.
            _render_log_md_for(store, session.backend_id, root_id, None)


def _render_log_md_for(
    store: Any,
    backend_id: str,
    cycle_id: str,
    hard_samples_artifact: dict | None,
) -> None:
    index = store.load(backend_id, cycle_id)
    if not index:
        return
    n_rounds = int(index.get("n_rounds", 0) or 0)
    rounds = store.load_rounds_range(backend_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
    cycle_dir = store.campaign_dir(cycle_id)
    streams_dir = cycle_dir / ".runtime" / "streams"
    fork_indices = _load_fork_indices(cycle_dir)
    content = to_markdown(
        from_disk_log(
            index,
            rounds,
            hard_samples_artifact=hard_samples_artifact,
            streams_dir=streams_dir,
            fork_indices=fork_indices,
        )
    )
    (cycle_dir / "log.md").write_text(content, encoding="utf-8")


def _load_fork_indices(cycle_dir: Path) -> list[dict] | None:
    """Sibling index.json under forks/, diag/, sweeps/*/forks/. None when no siblings."""
    sibling_dirs: list[Path] = []
    for sibling_kind in ("forks", "diag"):
        parent = cycle_dir / sibling_kind
        if parent.is_dir():
            sibling_dirs.extend(sorted(parent.iterdir()))
    sweeps_dir = cycle_dir / "sweeps"
    if sweeps_dir.is_dir():
        for batch_dir in sorted(sweeps_dir.iterdir()):
            batch_forks = batch_dir / "forks"
            if batch_forks.is_dir():
                sibling_dirs.extend(sorted(batch_forks.iterdir()))
    if not sibling_dirs:
        return None
    out: list[dict] = []
    for fork_dir in sibling_dirs:
        if not fork_dir.is_dir():
            continue
        idx = fork_dir / "index.json"
        if not idx.is_file():
            continue
        try:
            out.append(json.loads(idx.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def write_review_md(session: Session, cycle: Cycle) -> None:
    """Render review.md from index + rounds + per-round audit JSONs (M10 surface)."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        index = store.load(session.backend_id, session.state.cycle_id)
        if not index:
            return
        n_rounds = int(index.get("n_rounds", 0) or 0)
        rounds = (
            store.load_rounds_range(session.backend_id, session.state.cycle_id, 0, n_rounds - 1)
            if n_rounds
            else []
        )
        cycle_dir = store.campaign_dir(session.state.cycle_id)
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
