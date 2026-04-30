"""Cross-cycle leaderboard — read-only pivot over a tenant's campaigns.

Walks every cycle under a ``Stores.campaigns`` tree, computes per-cycle
``L1Stats`` + behaviour pass rates, and renders two views:

- **Default** — every cycle, sorted by (l1_generate_hash, rounds_to_95
  asc with None last, behavior_pass_rate desc). The "are we converging"
  view.
- **Sweep** — sweep-mode cycles only, sorted by round-1 top_lift desc.
  The "which candidate L1 prompt is most promising" view.

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
    "LeaderboardRow",
    "build_leaderboard_rows",
    "compute_proxy_lift_corr",
    "format_leaderboard",
    "format_sweep_leaderboard",
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
        index = cs.load(backend_id, cycle_id) or summary
        row = _row_from_cycle(stores, backend_id, cycle_id, index)
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


def format_leaderboard(rows: list[LeaderboardRow]) -> str:
    """Default view: all cycles, grouped by l1_generate_hash."""
    sorted_rows = sorted(
        rows,
        key=lambda r: (
            r.l1_generate_hash,
            (r.rounds_to_95 if r.rounds_to_95 is not None else 999),
            -r.behavior_pass_rate,
        ),
    )
    return _format_table(sorted_rows, _DEFAULT_COLS)


def format_sweep_leaderboard(rows: list[LeaderboardRow]) -> str:
    """Sweep view: sweep cycles only, sorted by round-1 top_lift desc."""
    sweep_rows = [r for r in rows if r.mode == "sweep"]
    sorted_rows = sorted(
        sweep_rows,
        key=lambda r: (-r.round_1_top_lift, -r.behavior_pass_rate),
    )
    return _format_table(sorted_rows, _SWEEP_COLS)


# --- per-cycle row construction ------------------------------------------


def _row_from_cycle(
    stores: Any, backend_id: str, cycle_id: str, index: dict[str, Any]
) -> LeaderboardRow | None:
    final = index.get("final") or {}
    n_trials = int(index.get("n_trials") or 0)
    cs = stores.campaigns
    trials = cs.load_trials_range(backend_id, cycle_id, 0, n_trials - 1) if n_trials else []
    cycle_dir = cs.campaign_dir(cycle_id)
    audits = _load_round_audits(cycle_dir, trials)

    parent_session_id = index.get("parent_session_id") or ""
    dataset = _lookup_dataset(stores, parent_session_id)
    pipeline = (final.get("scorer_round_formula_short") or "").strip() or "?"

    hashes = final.get("prompt_hashes") or {}
    l1_gen_hash = (hashes.get("l1_generate") or "")[:8]
    l1_crit_hash = (hashes.get("l1_critique") or "")[:8]
    mode = (final.get("mode") or "").strip() or "?"

    baseline_composite = float(final.get("baseline_composite") or 0.0)
    behavior_results = _compute_behavior_results(trials, audits)
    stats = compute_l1_stats(
        list(trials), baseline_composite=baseline_composite, behavior_results=behavior_results
    )

    baseline_acc = float(index.get("baseline_accuracy") or 0.0)
    best_acc = float(index.get("best_accuracy") or 0.0)
    round_1_top_lift = (
        (float(trials[0].get("composite") or 0.0) - baseline_composite) if trials else 0.0
    )
    round_1_yield = float(trials[0].get("l1_yield") or 0.0) if trials else 0.0

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
        rounds_completed=n_trials,
        l2_fires=stats.l2_fires,
        stop_reason=str(final.get("stop_reason") or index.get("stop_reason") or ""),
    )


def _load_round_audits(
    cycle_dir: Path, trials: list[dict[str, Any]]
) -> list[dict[str, Any] | None]:
    import json

    rounds_dir = cycle_dir / ".cache" / "rounds"
    out: list[dict[str, Any] | None] = []
    for trial in trials:
        round_num = int(trial.get("round") or 0)
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
    trials: list[dict[str, Any]], audits: list[dict[str, Any] | None]
) -> list[list]:
    out: list[list] = []
    prior: list[dict[str, Any]] = []
    for i, trial in enumerate(trials):
        audit = audits[i] if i < len(audits) else None
        if audit is None:
            out.append([])
            continue
        ctx = CheckContext(
            round_num=int(trial.get("round") or i),
            prior_rounds=list(prior),
            opt_search_point=dict(trial.get("opt_search_point") or {}),
            context_object=[],  # leaderboard skips context_object — caller-private
        )
        out.append(run_all_checks(audit, ctx))
        prior.append(audit)
    return out


def _lookup_dataset(stores: Any, session_id: str) -> str:
    if not session_id:
        return "?"
    record = stores.sessions.read(session_id) if hasattr(stores, "sessions") else None
    if not record:
        return "?"
    return str(
        record.get("dataset_name") or record.get("init_params", {}).get("dataset_name") or "?"
    )


# --- table formatting -----------------------------------------------------

_DEFAULT_COLS: tuple[tuple[str, str], ...] = (
    ("cycle_id", "cycle"),
    ("dataset", "dataset"),
    ("l1_generate_hash", "l1g"),
    ("l1_critique_hash", "l1c"),
    ("mode", "mode"),
    ("rounds_to_95", "→95"),
    ("round_1_verdict", "r1"),
    ("round_1_top_lift", "r1Δ"),
    ("best_acc", "best"),
    ("baseline_acc", "base"),
    ("delta_acc", "Δacc"),
    ("behavior_pass_rate", "beh"),
    ("rounds_completed", "n"),
    ("l2_fires", "l2"),
    ("stop_reason", "stop"),
)

_SWEEP_COLS: tuple[tuple[str, str], ...] = (
    ("cycle_id", "cycle"),
    ("dataset", "dataset"),
    ("l1_generate_hash", "l1g"),
    ("round_1_verdict", "r1"),
    ("round_1_top_lift", "r1Δ"),
    ("round_1_yield", "yield"),
    ("behavior_pass_rate", "beh"),
    ("baseline_acc", "base"),
    ("stop_reason", "stop"),
)


def _format_table(rows: list[LeaderboardRow], cols: tuple[tuple[str, str], ...]) -> str:
    if not rows:
        return "(no cycles)\n"
    cells: list[list[str]] = [[label for _, label in cols]]
    for r in rows:
        cells.append([_fmt(getattr(r, attr)) for attr, _ in cols])
    widths = [max(len(c) for c in col) for col in zip(*cells, strict=True)]
    lines = []
    for i, row in enumerate(cells):
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)))
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "y" if value else "n"
    if isinstance(value, float):
        return f"{value:+.3f}" if abs(value) < 10 else f"{value:.2f}"
    return str(value)
