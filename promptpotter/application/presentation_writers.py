"""Operator-facing markdown writers — log.md / review.md / library/*.md.

Called from runner orchestration milestones, NOT from RunListener (which
must stay display-only per CLAUDE.md).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.review import render_review_md
from promptpotter.infrastructure.store import root_cycle_id
from promptpotter.presentation.views.render_markdown import to_markdown
from promptpotter.presentation.views.view_factories import from_disk_log
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.optimization.cycle import Cycle

__all__ = [
    "refresh_tenant_leaderboards",
    "write_log_md",
    "write_review_md",
]


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
    n_trials = int(index.get("n_trials", 0) or 0)
    trials = store.load_trials_range(backend_id, cycle_id, 0, n_trials - 1) if n_trials else []
    cycle_dir = store.campaign_dir(cycle_id)
    streams_dir = cycle_dir / ".runtime" / "streams"
    fork_indices = _load_fork_indices(cycle_dir)
    content = to_markdown(
        from_disk_log(
            index,
            trials,
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


def refresh_tenant_leaderboards(session: Session) -> None:
    """Refresh tenant library/ dashboards: runs.md, individuals.md, hard_samples.md, README.md."""
    from promptpotter.application.intelligence.hard_sample_archive import (
        build_archive_hard_samples_artifact,
    )
    from promptpotter.application.leaderboard import (
        build_individuals_rows,
        build_leaderboard_rows,
        format_individuals_md,
        format_runs_md,
    )
    from promptpotter.presentation.views.render_markdown import render_hard_sample_heatmap

    if session.store is None:
        return
    out_dir = session.store.base_dir / "library"
    out_dir.mkdir(parents=True, exist_ok=True)

    backend_id = session.backend_id
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    with graceful("library/ refresh failed"):
        rows = build_leaderboard_rows(session.store)
        dataset_label = _pick_dataset_label(rows)
        runs_body = f"# Runs\n\n_Generated {generated_at}._\n\n" + format_runs_md(rows)
        (out_dir / "runs.md").write_text(runs_body, encoding="utf-8")

        ind_rows = build_individuals_rows(session.store, backend_id)
        ind_body = f"# Individuals\n\n_Generated {generated_at}._\n\n" + format_individuals_md(
            ind_rows
        )
        (out_dir / "individuals.md").write_text(ind_body, encoding="utf-8")

        artifact = build_archive_hard_samples_artifact(session.store.archive, backend_id)
        sample_lookup = _build_sample_query_lookup(session.store.archive, backend_id)
        heatmap = render_hard_sample_heatmap(artifact, sample_query_lookup=sample_lookup)
        title = f"# Hard Samples — {dataset_label}" if dataset_label else "# Hard Samples"
        if heatmap:
            hs_body = f"{title}\n\n_Generated {generated_at}._\n\n```\n{heatmap}\n```\n"
        else:
            hs_body = (
                f"{title}\n\n_Generated {generated_at}._\n\n"
                "_No measurements yet — run a cycle first._\n"
            )
        (out_dir / "hard_samples.md").write_text(hs_body, encoding="utf-8")

        (out_dir / "README.md").write_text(_render_library_readme(), encoding="utf-8")


def _pick_dataset_label(rows: list[Any]) -> str:
    """Pick a single dataset label for headers when one dominates."""
    seen: dict[str, int] = {}
    for r in rows:
        d = getattr(r, "dataset", "") or ""
        if d and d != "?":
            seen[d] = seen.get(d, 0) + 1
    if not seen:
        return ""
    if len(seen) == 1:
        return next(iter(seen))
    return ", ".join(sorted(seen))


def _build_sample_query_lookup(archive: Any, backend_id: str) -> dict[int, str]:
    """First-seen ``sample_id → query`` map across the archive."""
    out: dict[int, str] = {}
    for entry in archive.list_all(backend_id):
        run_id = entry.get("run_id")
        if not run_id:
            continue
        detail = archive.load_by_id(backend_id, run_id)
        if detail is None:
            continue
        for item in detail.get("measurements", []):
            sid = item.get("sample_id")
            if sid is None:
                continue
            sid_int = int(sid)
            if sid_int in out:
                continue
            q = (item.get("query") or "").strip()
            if q:
                out[sid_int] = q
    return out


def _render_library_readme() -> str:
    return (
        "# library — cross-cycle dashboards for this tenant\n"
        "\n"
        "Open the .md files at the top of this folder to see what's been measured.\n"
        "\n"
        "| File              | Question it answers                                            |\n"
        "|-------------------|----------------------------------------------------------------|\n"
        "| `runs.md`         | What cycles has this tenant produced and how did they go?      |\n"
        "| `individuals.md`  | Which target configs have been measured, ranked by mean score? |\n"
        "| `hard_samples.md` | Which dataset samples are hardest? Heatmap + Rasch leaderboard.|\n"
        "\n"
        "`.archive/` holds machine state the optimizer needs at runtime — measurement\n"
        "facts, prompt-alias dedup, backend registrations. Don't read these by hand;\n"
        "each .md above derives its view from this archive on every round-end.\n"
    )


def write_review_md(session: Session, cycle: Cycle) -> None:
    """Render review.md from index + trials + per-round audit JSONs (M10 surface)."""
    if not session.state.cycle_id or session.store is None:
        return
    with graceful("review.md render failed"):
        store = session.store.campaigns
        index = store.load(session.backend_id, session.state.cycle_id)
        if not index:
            return
        n_trials = int(index.get("n_trials", 0) or 0)
        trials = (
            store.load_trials_range(session.backend_id, session.state.cycle_id, 0, n_trials - 1)
            if n_trials
            else []
        )
        cycle_dir = store.campaign_dir(session.state.cycle_id)
        rounds_dir = cycle_dir / ".runtime" / "cache" / "rounds"
        round_audits: list[dict | None] = []
        for trial in trials:
            round_num = int(trial.get("round") or 0)
            audit_path = rounds_dir / f"round_{round_num:04d}.json"
            audit: dict | None = None
            if audit_path.is_file():
                try:
                    audit = json.loads(audit_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    audit = None
            round_audits.append(audit)
        td = cycle.opt_sp.task_context
        context_object = [
            td.pipeline_purpose,
            td.optimization_goals,
            td.key_challenges,
        ]
        content = render_review_md(
            index,
            trials,
            round_audits=round_audits,
            context_object=context_object,
        )
        (cycle_dir / "review.md").write_text(content, encoding="utf-8")
