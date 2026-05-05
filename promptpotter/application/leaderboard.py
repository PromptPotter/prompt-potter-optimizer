"""Cross-cycle leaderboard — read-only pivot over a tenant's campaigns.

Walks every cycle under a ``Stores.campaigns`` tree, computes per-cycle
``L1Stats`` + behaviour pass rates, and renders the operator-facing
``archive/runs.md`` and ``archive/individuals.md``. Cycles group by
``l1_generate_hash`` (one heading per canonical L1-generate template).
Sweep-mode cycles get their own ``## Sweep view`` section in the same
file; absent when no sweep cycles exist.

Computes ``proxy_lift_corr`` — Spearman rank correlation between
sweep-mode round-1 top_lift and full-mode rounds_to_95-or-final-acc,
across ``l1_generate_hash``es that have at least one cycle in each mode.
Reported in the table footer when ≥4 paired hashes exist (per spec).

No new persistence; pure derivation from disk artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.application.l1_behavior_checks import CheckContext, run_all_checks
from promptpotter.application.optimization.l1_stats import (
    compute_l1_stats,
)

__all__ = [
    "JSPRow",
    "LeaderboardRow",
    "build_individuals_rows",
    "build_leaderboard_rows",
    "compute_proxy_lift_corr",
    "format_individuals_md",
    "format_runs_md",
]


PROXY_CORR_MIN_PAIRS = 4


@dataclass(frozen=True)
class LeaderboardRow:
    cycle_id: str
    dataset: str
    pipeline: str
    l1_generate_hash: str
    l1_critique_hash: str
    mode: str  # "sweep" | "full" | "?"
    rounds_to_95: int | None
    round_1_verdict: str
    round_1_top_lift: float
    round_1_yield: float
    best_acc: float
    baseline_acc: float
    delta_acc: float
    behavior_pass_rate: float
    rounds_completed: int
    l2_fires: int
    stop_reason: str


# --- public API ----------------------------------------------------------


def build_leaderboard_rows(stores: Any) -> list[LeaderboardRow]:
    """Walk every cycle in ``stores.campaigns`` and build a row for each.

    ``stores`` is a ``Stores`` bundle. Cycles whose ``index.json`` is
    unreadable are silently skipped — the leaderboard is best-effort.
    """
    rows: list[LeaderboardRow] = []
    cs = stores.campaigns
    cycle_summaries = cs.list_all("")  # tenant-wide
    for summary in cycle_summaries:
        cycle_id = summary.get("campaign_id")
        if not cycle_id:
            continue
        backend_id = summary.get("backend_id") or ""
        cycle_summary = cs.load(backend_id, cycle_id) or summary
        row = _row_from_cycle(stores, backend_id, cycle_id, cycle_summary)
        if row is not None:
            rows.append(row)
    return rows


def compute_proxy_lift_corr(rows: list[LeaderboardRow]) -> tuple[float | None, int]:
    """Spearman correlation of sweep round-1 top_lift vs full-mode outcome.

    Pairs sweep & full cycles by ``l1_generate_hash``. For each hash with
    both, takes the mean sweep round_1_top_lift and the best full outcome
    (``rounds_to_95`` if present, else final ``best_acc``). Returns
    ``(corr, n_pairs)``; corr is ``None`` when fewer than
    ``PROXY_CORR_MIN_PAIRS`` pairs exist.
    """
    by_hash_sweep: dict[str, list[float]] = {}
    by_hash_full: dict[str, list[tuple[int | None, float]]] = {}
    for r in rows:
        if not r.l1_generate_hash:
            continue
        if r.mode == "sweep":
            by_hash_sweep.setdefault(r.l1_generate_hash, []).append(r.round_1_top_lift)
        elif r.mode == "full":
            by_hash_full.setdefault(r.l1_generate_hash, []).append((r.rounds_to_95, r.best_acc))

    paired_x: list[float] = []
    paired_y: list[float] = []
    for h, sweep_lifts in by_hash_sweep.items():
        full_outcomes = by_hash_full.get(h)
        if not full_outcomes:
            continue
        x = sum(sweep_lifts) / len(sweep_lifts)
        # Lower rounds_to_95 is better; absent → use 1 - best_acc as a tiebreaker.
        y_candidates = [
            (out[0] if out[0] is not None else (10 + (1 - out[1]))) for out in full_outcomes
        ]
        y = min(y_candidates)
        paired_x.append(x)
        paired_y.append(float(y))

    n_pairs = len(paired_x)
    if n_pairs < PROXY_CORR_MIN_PAIRS:
        return None, n_pairs

    from scipy.stats import spearmanr  # type: ignore[import-untyped]

    # Negate y so higher correlation means "more sweep lift → better full
    # outcome (fewer rounds-to-95)". Spec asks for the proxy validity sign.
    corr, _p = spearmanr(paired_x, [-y for y in paired_y])
    return float(corr), n_pairs


def format_runs_md(rows: list[LeaderboardRow]) -> str:
    """Markdown body for ``archive/runs.md`` — every cycle, grouped by template.

    One H2 per ``l1_generate_hash`` carries the constants (template hash +
    dataset + pipeline) so the table inside each group can stay narrow
    (cycle, mode, baseline → best, rounds, L2 fires, behavior, round 1).

    Sweep-mode cycles surface as a separate ``## Sweep view`` H2 sorted by
    round-1 top_lift desc. The section is omitted entirely when zero sweep
    cycles exist — no "(no rows)" placeholder.
    """
    if not rows:
        return "_No cycles yet — run `python -m promptpotter optimize`._\n"

    parts: list[str] = [
        "Every cycle this tenant has produced. Grouped by L1-generate template —",
        "cycles sharing one group used the same canonical L1-generate prompt. Within",
        "a group, **mode** (`full` / `diag` / `sweep`) tells you what the cycle was for.",
        "",
    ]

    grouped: dict[tuple[str, str, str], list[LeaderboardRow]] = {}
    for r in rows:
        grouped.setdefault((r.l1_generate_hash, r.dataset, r.pipeline), []).append(r)

    for (l1g, dataset, pipeline), group_rows in sorted(grouped.items()):
        group_rows.sort(
            key=lambda r: (
                (r.rounds_to_95 if r.rounds_to_95 is not None else 999),
                -r.behavior_pass_rate,
                r.cycle_id,
            )
        )
        parts.append(f"## L1-generate `{l1g or '?'}` · {dataset} · {pipeline}")
        parts.append("")
        parts += _runs_table([_run_row_cells(r) for r in group_rows])
        parts.append("")

    sweep_rows = sorted(
        (r for r in rows if r.mode == "sweep"),
        key=lambda r: (-r.round_1_top_lift, -r.behavior_pass_rate),
    )
    if sweep_rows:
        parts.append("## Sweep view")
        parts.append("")
        parts.append("Sweep-mode cycles ranked by round-1 top-lift — used to narrow down")
        parts.append("candidate L1 prompts before promoting to full runs.")
        parts.append("")
        parts += _sweep_table([_sweep_row_cells(r) for r in sweep_rows])
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


_RUN_HEADERS = ("cycle", "mode", "baseline → best", "rounds", "L2 fires", "behavior", "round 1")
_SWEEP_HEADERS = ("cycle", "round 1", "top lift", "yield", "behavior", "baseline")


def _run_row_cells(r: LeaderboardRow) -> tuple[str, ...]:
    return (
        r.cycle_id,
        r.mode or "?",
        f"{r.baseline_acc:.2f} → {r.best_acc:.2f}",
        str(r.rounds_completed),
        str(r.l2_fires),
        f"{r.behavior_pass_rate * 100:.0f}%",
        r.round_1_verdict or "—",
    )


def _sweep_row_cells(r: LeaderboardRow) -> tuple[str, ...]:
    return (
        r.cycle_id,
        r.round_1_verdict or "—",
        f"{r.round_1_top_lift:+.3f}",
        f"{r.round_1_yield * 100:.0f}%",
        f"{r.behavior_pass_rate * 100:.0f}%",
        f"{r.baseline_acc:.2f}",
    )


def _runs_table(rows: list[tuple[str, ...]]) -> list[str]:
    return _md_table(_RUN_HEADERS, rows)


def _sweep_table(rows: list[tuple[str, ...]]) -> list[str]:
    return _md_table(_SWEEP_HEADERS, rows)


def _md_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    header = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("-" * (len(h) + 2) for h in headers) + "|"
    body = ["| " + " | ".join(cells) + " |" for cells in rows]
    return [header, sep, *body]


# --- per-cycle row construction ------------------------------------------


def _row_from_cycle(
    stores: Any, backend_id: str, cycle_id: str, cycle_summary: dict[str, Any]
) -> LeaderboardRow | None:
    final = cycle_summary.get("final") or {}
    n_rounds = int(cycle_summary.get("n_rounds") or 0)
    cs = stores.campaigns
    rounds = cs.load_rounds_range(backend_id, cycle_id, 0, n_rounds - 1) if n_rounds else []
    cycle_dir = cs.campaign_dir(cycle_id)
    audits = _load_round_audits(cycle_dir, rounds)

    parent_session_id = cycle_summary.get("parent_session_id") or ""
    dataset = _lookup_dataset(stores, parent_session_id)
    pipeline = (final.get("scorer_round_formula_short") or "").strip() or "?"

    hashes = final.get("prompt_hashes") or {}
    l1_gen_hash = (hashes.get("l1_generate") or "")[:8]
    l1_crit_hash = (hashes.get("l1_critique") or "")[:8]
    mode = (final.get("mode") or "").strip() or "?"

    baseline_composite_fitness = float(final.get("baseline_composite_fitness") or 0.0)
    behavior_results = _compute_behavior_results(rounds, audits)
    stats = compute_l1_stats(
        list(rounds),
        baseline_composite_fitness=baseline_composite_fitness,
        behavior_results=behavior_results,
    )

    baseline_acc = float(cycle_summary.get("baseline_accuracy") or 0.0)
    best_acc = float(cycle_summary.get("best_accuracy") or 0.0)
    round_1_top_lift = (
        (float(rounds[0].get("composite_fitness") or 0.0) - baseline_composite_fitness)
        if rounds
        else 0.0
    )
    round_1_yield = float(rounds[0].get("l1_yield") or 0.0) if rounds else 0.0

    return LeaderboardRow(
        cycle_id=cycle_id,
        dataset=dataset,
        pipeline=pipeline,
        l1_generate_hash=l1_gen_hash,
        l1_critique_hash=l1_crit_hash,
        mode=mode,
        rounds_to_95=stats.rounds_to_95,
        round_1_verdict=stats.round_1_verdict,
        round_1_top_lift=round_1_top_lift,
        round_1_yield=round_1_yield,
        best_acc=best_acc,
        baseline_acc=baseline_acc,
        delta_acc=best_acc - baseline_acc,
        behavior_pass_rate=stats.behavior_pass_rate,
        rounds_completed=n_rounds,
        l2_fires=stats.l2_fires,
        stop_reason=str(final.get("stop_reason") or cycle_summary.get("stop_reason") or ""),
    )


def _load_round_audits(
    cycle_dir: Path, rounds: list[dict[str, Any]]
) -> list[dict[str, Any] | None]:
    import json

    rounds_dir = cycle_dir / ".runtime" / "cache" / "rounds"
    out: list[dict[str, Any] | None] = []
    for round_data in rounds:
        round_num = int(round_data.get("round") or 0)
        path = rounds_dir / f"round_{round_num:04d}.json"
        if path.is_file():
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
                continue
            except (OSError, json.JSONDecodeError):
                pass
        out.append(None)
    return out


def _compute_behavior_results(
    rounds: list[dict[str, Any]], audits: list[dict[str, Any] | None]
) -> list[list]:
    out: list[list] = []
    prior: list[dict[str, Any]] = []
    for i, round_data in enumerate(rounds):
        audit = audits[i] if i < len(audits) else None
        if audit is None:
            out.append([])
            continue
        ctx = CheckContext(
            round_num=int(round_data.get("round") or i),
            prior_rounds=list(prior),
            opt_search_point=dict(round_data.get("opt_search_point") or {}),
            context_object=[],  # leaderboard skips context_object — caller-private
        )
        out.append(run_all_checks(audit, ctx))
        prior.append(audit)
    return out


def _lookup_dataset(stores: Any, session_id: str) -> str:
    if not session_id:
        return "?"
    record = stores.sessions.read(session_id)
    if not record:
        return "?"
    return str(
        record.get("dataset_name") or record.get("init_params", {}).get("dataset_name") or "?"
    )


# ===========================================================================
# Individuals view — ranks JobSearchPoints across the tenant's measurement
# archive. Each archive index entry is one individual (content_hash unique
# in index — see MeasurementArchive.save).
# ===========================================================================


@dataclass(frozen=True)
class JSPRow:
    """One individual's aggregate performance across the tenant's archive."""

    jsp_hash: str  # content_hash[:8]
    n_samples: int  # samples measured under this individual
    mean_score: float
    measured_at: str  # short timestamp from the archive entry's created_at
    origin_cycle: str  # experiment_id (cycle prefix) of the originating cycle


def build_individuals_rows(stores: Any, backend_id: str) -> list[JSPRow]:
    """Walk the archive, build one row per measured individual.

    Each archive index entry maps 1:1 to an individual (the archive
    replaces by ``content_hash`` on save). For each entry, load the
    detail file to aggregate per-sample score, then build the row.
    Skips entries with unreadable detail.
    """
    archive = stores.archive
    rows: list[JSPRow] = []
    for entry in archive.list_all(backend_id):
        content_hash = (entry.get("content_hash") or "").strip()
        if not content_hash:
            continue
        run_id = entry.get("run_id")
        if not run_id:
            continue
        detail = archive.load_by_id(backend_id, run_id)
        if detail is None:
            continue
        items = detail.get("measurements") or []
        n_samples = 0
        score_sum = 0.0
        score_n = 0
        for item in items:
            if item.get("error") or item.get("predicted") == "ERROR":
                continue
            n_samples += 1
            s = item.get("fitness")
            if s is not None:
                score_sum += float(s)
                score_n += 1
        if n_samples == 0:
            continue
        mean_score = (score_sum / score_n) if score_n else 0.0
        rows.append(
            JSPRow(
                jsp_hash=content_hash[:8],
                n_samples=n_samples,
                mean_score=mean_score,
                measured_at=_short_ts(str(entry.get("created_at") or "")),
                origin_cycle=str(entry.get("experiment_id") or "—"),
            )
        )
    return rows


def format_individuals_md(rows: list[JSPRow]) -> str:
    """Markdown body for ``archive/individuals.md`` — every measured individual."""
    if not rows:
        return "_No measurements yet — run a cycle first._\n"

    sorted_rows = sorted(rows, key=lambda r: (-r.mean_score, -r.n_samples, r.jsp_hash))
    parts: list[str] = [
        "Every individual (a target-layer JobSearchPoint — `pipeline_params` plus",
        "the canonical prompt) the system has scored across all cycles. Ranked by",
        "mean score.",
        "",
    ]
    headers = ("individual", "mean score", "samples", "measured", "origin cycle")
    body: list[tuple[str, ...]] = []
    for r in sorted_rows:
        body.append(
            (
                r.jsp_hash,
                f"{r.mean_score:.2f}",
                str(r.n_samples),
                r.measured_at or "—",
                r.origin_cycle,
            )
        )
    parts += _md_table(headers, body)
    parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _short_ts(ts: str) -> str:
    """ISO timestamp → ``YYYY-MM-DD HH:MM`` (drops seconds + tz). Empty → ''."""
    if not ts or len(ts) < 16:
        return ts
    return ts[:10] + " " + ts[11:16]
