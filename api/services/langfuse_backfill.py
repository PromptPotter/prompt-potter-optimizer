"""
Backfill historical dataset runs to cloud Langfuse.

One-shot, idempotent push of all completed dataset_runs to Langfuse as
properly structured traces. Grouped by run origin (baseline, grid_search,
sensitivity_scan, feedback_cycle, smart_search_winner).

State is tracked in ``{backend_id}/obs/langfuse/backfill_state.json`` so
re-running only pushes new runs.

Usage::

    from api.services.langfuse_backfill import backfill_to_langfuse
    stats = backfill_to_langfuse(store, backend_id)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)

# Fixed ordering for origin groups
ORIGIN_ORDER = [
    "baseline",
    "grid_search",
    "sensitivity_scan",
    "feedback_cycle",
    "smart_search_winner",
    "other",
]

_PREFIX_MAP = [
    ("baseline_", "baseline"),
    ("grid_", "grid_search"),
    ("scan_", "sensitivity_scan"),
    ("candidate_", "feedback_cycle"),
    ("smart_search_winner_", "smart_search_winner"),
]


def classify_run_origin(run_id: str) -> str:
    """Classify a dataset run by its ID prefix.

    Returns one of: baseline, grid_search, sensitivity_scan,
    feedback_cycle, smart_search_winner, other.
    """
    for prefix, origin in _PREFIX_MAP:
        if run_id.startswith(prefix):
            return origin
    return "other"


def _state_path(store: ProjectStore, backend_id: str) -> Path:
    return store.base_dir / backend_id / "obs" / "langfuse" / "backfill_state.json"


def _load_state(store: ProjectStore, backend_id: str) -> dict[str, Any]:
    path = _state_path(store, backend_id)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "backfilled_run_ids": [],
        "last_backfill_at": None,
        "langfuse_trace_ids": {},
    }


def _save_state(store: ProjectStore, backend_id: str, state: dict[str, Any]) -> None:
    path = _state_path(store, backend_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def backfill_to_langfuse(
    store: ProjectStore,
    backend_id: str,
    *,
    flush_every: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Push all historical dataset_runs to cloud Langfuse.

    Idempotent — tracks which run_ids have been pushed in a state file
    and skips them on subsequent calls.

    Args:
        store: ProjectStore instance.
        backend_id: Backend identifier.
        flush_every: Flush Langfuse client every N spans.
        on_progress: Optional callback for progress messages.

    Returns:
        Stats dict with keys: total_on_disk, new_runs, already_done,
        origins (per-origin breakdown), error (if Langfuse disabled).
    """
    from api.services.langfuse_client import LangfuseLogger

    lf = LangfuseLogger.get_instance()
    if not lf.enabled:
        return {"error": "Langfuse is disabled (missing credentials or LANGFUSE_ENABLED=false)"}

    # Load state
    state = _load_state(store, backend_id)
    already_done = set(state["backfilled_run_ids"])

    # List all completed runs
    summaries = store.dataset_runs.list_all(backend_id)
    total_on_disk = len(summaries)

    # Group by origin, skip already-done
    groups: dict[str, list[dict]] = {origin: [] for origin in ORIGIN_ORDER}
    for s in summaries:
        rid = s.get("run_id", "")
        if rid in already_done:
            continue
        origin = classify_run_origin(rid)
        groups[origin].append(s)

    new_runs = sum(len(v) for v in groups.values())
    span_counter = 0
    origin_stats: dict[str, dict[str, Any]] = {}

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    for origin in ORIGIN_ORDER:
        runs = groups[origin]
        if not runs:
            continue

        _emit(f"  {origin}: {len(runs)} runs")

        # Create one trace per origin group
        trace_id = lf.create_trace(
            name=f"backfill_{origin}",
            input={"origin": origin, "n_runs": len(runs)},
            session_id=f"backfill_{backend_id}",
            tags=["backfill", origin],
        )
        if not trace_id:
            logger.warning("Failed to create backfill trace for %s", origin)
            continue

        accuracies: list[float] = []
        total_items = 0

        for run_summary in runs:
            rid = run_summary["run_id"]

            # Load full detail
            detail = store.dataset_runs.load_by_id(backend_id, rid)
            if not detail:
                logger.warning("Could not load detail for run %s", rid)
                continue

            scores = detail.get("scores", {})
            accuracy = scores.get("accuracy", 0.0)
            hits = scores.get("hits", 0)
            total = scores.get("total", 0)
            items = detail.get("items", [])
            item_count = len(items)

            # Build span output with per-query items
            span_output: dict[str, Any] = {
                "accuracy": accuracy,
                "hits": hits,
                "total": total,
                "items": [
                    {
                        "query": it.get("query", ""),
                        "predicted": it.get("predicted", ""),
                        "ground_truth": it.get("ground_truth", ""),
                        "hit": it.get("hit", False),
                    }
                    for it in items
                ],
            }

            lf.create_span(
                trace_id=trace_id,
                name=rid,
                input={
                    "run_id": rid,
                    "content_hash": detail.get("content_hash", ""),
                    "model": detail.get("model", ""),
                    "prompt_state_id": detail.get("prompt_state_id", ""),
                },
                output=span_output,
            )

            accuracies.append(accuracy)
            total_items += item_count

            # Mark as done
            already_done.add(rid)
            state["backfilled_run_ids"].append(rid)

            span_counter += 1
            if span_counter % flush_every == 0:
                lf.flush()
                _emit(f"    [{span_counter}/{new_runs}] flushed")

        # Scores on the trace
        if accuracies:
            best_acc = max(accuracies)
            avg_acc = sum(accuracies) / len(accuracies)
            lf.create_score(trace_id, "best_accuracy", best_acc)
            lf.create_score(trace_id, "avg_accuracy", avg_acc)
            lf.update_trace(
                trace_id,
                output={
                    "best_accuracy": best_acc,
                    "avg_accuracy": avg_acc,
                    "n_runs": len(runs),
                    "total_items": total_items,
                },
            )
        else:
            best_acc = 0.0
            avg_acc = 0.0

        lf.end_trace(trace_id)

        # Save state after each origin group (crash recovery)
        state["langfuse_trace_ids"][origin] = trace_id
        state["last_backfill_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(store, backend_id, state)

        origin_stats[origin] = {
            "n_runs": len(runs),
            "total_items": total_items,
            "best_accuracy": best_acc,
            "avg_accuracy": avg_acc,
            "trace_id": trace_id,
        }

    # Final flush
    lf.flush()
    state["last_backfill_at"] = datetime.now(timezone.utc).isoformat()
    _save_state(store, backend_id, state)

    return {
        "total_on_disk": total_on_disk,
        "new_runs": new_runs,
        "already_done": total_on_disk - new_runs,
        "origins": origin_stats,
        "session_id": f"backfill_{backend_id}",
    }
