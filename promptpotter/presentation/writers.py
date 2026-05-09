"""Operator-facing markdown writers — log.md / review.md / archive/*.md.

Called from runner orchestration milestones, NOT from RunCallbacks (which
must stay display-only per CLAUDE.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.review import render_review_md
from promptpotter.infrastructure.projections.audit_trail import load_round_audits
from promptpotter.infrastructure.store import root_cycle_id
from promptpotter.presentation.views.render_markdown import to_markdown
from promptpotter.presentation.views.view_factories import from_disk_log
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.optimization.cycle import Cycle

__all__ = ["write_log_md", "write_review_md"]


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
            # section picks up this fork's latest best/baseline/stop_reason.
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
