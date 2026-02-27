"""
Push dataset runs to cloud Langfuse.

Dataset-first structure: registers dataset items with ground truth, then
creates one trace per dataset_run linked to the dataset via Dataset Runs.

Two entry points:

- ``push_run()`` — push a single run (called automatically after each eval)
- ``push_all_runs()`` — batch push all historical runs (called from notebook)

State is tracked in ``{backend_id}/obs/langfuse/backfill_state.json`` so
re-running only pushes new runs.

Usage::

    from api.services.obs.langfuse_push import push_run, push_all_runs
    push_run(lf, store, backend_id, run_id)       # single run
    stats = push_all_runs(store, backend_id)       # batch
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

DATASET_NAME = "termnorm_ground_truth"


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
            state = json.load(f)
        # Detect old format (origin-grouped) and reset
        if state.get("format_version") != 2:
            logger.info("Resetting stale backfill state (old format)")
            return _fresh_state()
        return state
    return _fresh_state()


def _fresh_state() -> dict[str, Any]:
    return {
        "format_version": 2,
        "backfilled_run_ids": [],
        "last_backfill_at": None,
        "langfuse_trace_ids": {},
        "dataset_items": {},
    }


def _save_state(store: ProjectStore, backend_id: str, state: dict[str, Any]) -> None:
    path = _state_path(store, backend_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _collect_ground_truth(
    store: ProjectStore, backend_id: str, summaries: list[dict],
) -> dict[str, str]:
    """Extract unique (query → ground_truth) pairs from all dataset_runs."""
    gt_map: dict[str, str] = {}
    for s in summaries:
        rid = s.get("run_id", "")
        detail = store.dataset_runs.load_by_id(backend_id, rid)
        if not detail:
            continue
        for item in detail.get("dataset_run_items", []):
            query = item.get("query", "")
            ground_truth = item.get("ground_truth", "")
            if query and ground_truth:
                gt_map[query] = ground_truth
    return gt_map


def _register_dataset_items(
    lf: Any,
    gt_map: dict[str, str],
    state: dict[str, Any],
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Register/update dataset items in Langfuse. Returns {query: item_id}.

    Creates the dataset if needed, then creates or updates each item
    with the ground truth as expected_output.
    """
    query_to_item_id: dict[str, str] = dict(state.get("dataset_items", {}))

    # Create dataset (idempotent)
    lf.create_dataset(
        name=DATASET_NAME,
        description="TermNorm production ground truth queries for prompt evaluation",
        metadata={"n_queries": len(gt_map)},
    )

    # Fetch existing items to find those needing updates
    existing_items: dict[str, Any] = {}  # query_str → SDK item
    ds = lf.get_dataset(DATASET_NAME)
    if ds and hasattr(ds, "items"):
        for it in ds.items:
            input_data = getattr(it, "input", None) or {}
            q = input_data.get("query", "") if isinstance(input_data, dict) else ""
            if q:
                existing_items[q] = it

    created = 0
    updated = 0
    for query, ground_truth in gt_map.items():
        if query in query_to_item_id:
            # Already registered — check if expected_output needs update
            existing = existing_items.get(query)
            if existing and getattr(existing, "expected_output", None) is None:
                lf.update_dataset_item(
                    item_id=getattr(existing, "id"),
                    expected_output=ground_truth,
                )
                updated += 1
            continue

        # Check if item exists in Langfuse but not in our state
        existing = existing_items.get(query)
        if existing:
            item_id = getattr(existing, "id")
            if getattr(existing, "expected_output", None) is None:
                lf.update_dataset_item(
                    item_id=item_id,
                    expected_output=ground_truth,
                )
                updated += 1
        else:
            item_id = lf.create_dataset_item(
                dataset_name=DATASET_NAME,
                input={"query": query},
                expected_output=ground_truth,
                metadata={"source": "eval_data"},
            )
            created += 1

        if item_id:
            query_to_item_id[query] = item_id

    if on_progress:
        on_progress(
            f"  Dataset '{DATASET_NAME}': {len(query_to_item_id)} items "
            f"({created} created, {updated} updated)",
        )

    return query_to_item_id


# ---------------------------------------------------------------------------
# push_run — single-run cloud push (idempotent via shared registry)
# ---------------------------------------------------------------------------


def push_run(
    lf: Any,
    store: ProjectStore,
    backend_id: str,
    run_id: str,
    *,
    query_to_item_id: dict[str, str] | None = None,
    session_id: str | None = None,
    _state: dict[str, Any] | None = None,
    _save: bool = True,
) -> str | None:
    """Push one dataset_run to Langfuse cloud. Idempotent via shared registry.

    Args:
        lf: LangfuseLogger instance.
        store: ProjectStore instance.
        backend_id: Backend identifier.
        run_id: Dataset run ID to push.
        query_to_item_id: Optional ``{query: item_id}`` for dataset linking.
        session_id: Optional Langfuse session ID.
        _state: Pre-loaded state dict (for batch efficiency). When None,
            state is loaded from disk on each call.
        _save: Whether to persist state after this call. Set False in
            batch contexts where the caller manages saves.

    Returns:
        Langfuse trace ID, or None if already pushed or push failed.
    """
    state = _state if _state is not None else _load_state(store, backend_id)
    already_done = set(state["backfilled_run_ids"])

    if run_id in already_done:
        return None

    # Load full run detail
    detail = store.dataset_runs.load_by_id(backend_id, run_id)
    if not detail:
        logger.warning("push_run: could not load detail for run %s", run_id)
        return None

    scores = detail.get("scores", {})
    accuracy = scores.get("accuracy", 0.0)
    hits = scores.get("hits", 0)
    total = scores.get("total", 0)
    items = detail.get("dataset_run_items", [])
    origin = classify_run_origin(run_id)

    # Create trace
    trace_id = lf.create_trace(
        name=f"eval_{run_id}",
        input={
            "run_id": run_id,
            "content_hash": detail.get("content_hash", ""),
            "model": detail.get("model", ""),
            "prompt_state_id": detail.get("prompt_state_id", ""),
        },
        session_id=session_id or f"dataset_{backend_id}",
        tags=["eval", origin],
    )
    if not trace_id:
        logger.warning("push_run: failed to create trace for run %s", run_id)
        return None

    # Per-query spans
    for it in items:
        query = it.get("query", "")
        span_id = lf.create_span(
            trace_id=trace_id,
            name=query[:60] if query else "query",
            input={
                "query": query,
                "ground_truth": it.get("ground_truth", ""),
            },
            output={
                "predicted": it.get("predicted", ""),
                "hit": it.get("hit", False),
            },
        )

        # Link span to dataset item (skip if rate-limited)
        if query_to_item_id:
            item_id = query_to_item_id.get(query)
            if item_id and not lf.rate_limited:
                lf.link_item_to_run(
                    dataset_item_id=item_id,
                    trace_id=trace_id,
                    observation_id=span_id,
                    run_name=run_id,
                    run_metadata={
                        "origin": origin,
                        "accuracy": accuracy,
                    },
                )

    # Score + output + end
    lf.create_score(trace_id, "accuracy", accuracy)
    lf.update_trace(
        trace_id,
        output={
            "accuracy": accuracy,
            "hits": hits,
            "total": total,
        },
    )
    lf.end_trace(trace_id)

    # Update registry
    state["backfilled_run_ids"].append(run_id)
    state["langfuse_trace_ids"][run_id] = trace_id

    if _save:
        state["last_backfill_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(store, backend_id, state)

    return trace_id


# ---------------------------------------------------------------------------
# push_all_runs — batch push (renamed from backfill_to_langfuse)
# ---------------------------------------------------------------------------


def push_all_runs(
    store: ProjectStore,
    backend_id: str,
    *,
    flush_every: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Push all historical dataset_runs to cloud Langfuse.

    Dataset-first structure:
    1. Collect ground truth from all runs
    2. Register/update dataset items with expected_output
    3. Create one trace per run, link to dataset items via Dataset Runs
    4. Score each trace with accuracy

    Idempotent — tracks which run_ids have been pushed in a state file
    and skips them on subsequent calls.

    Args:
        store: ProjectStore instance.
        backend_id: Backend identifier.
        flush_every: Flush Langfuse client every N traces.
        on_progress: Optional callback for progress messages.

    Returns:
        Stats dict with keys: total_on_disk, new_runs, already_done,
        origins (per-origin breakdown), error (if Langfuse disabled).
    """
    from api.services.obs.langfuse_client import LangfuseLogger

    lf = LangfuseLogger.get_instance()
    if not lf.enabled:
        return {"error": "Langfuse is disabled (missing credentials or LANGFUSE_ENABLED=false)"}

    # Load state
    state = _load_state(store, backend_id)
    already_done = set(state["backfilled_run_ids"])

    # List all completed runs
    summaries = store.dataset_runs.list_all(backend_id)
    total_on_disk = len(summaries)

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # Step 1: Collect ground truth and register dataset items
    _emit("Collecting ground truth from dataset runs...")
    gt_map = _collect_ground_truth(store, backend_id, summaries)
    query_to_item_id = _register_dataset_items(lf, gt_map, state, on_progress)
    state["dataset_items"] = query_to_item_id

    # Step 2: Group new runs by origin for stats
    groups: dict[str, list[dict]] = {origin: [] for origin in ORIGIN_ORDER}
    for s in summaries:
        rid = s.get("run_id", "")
        if rid in already_done:
            continue
        origin = classify_run_origin(rid)
        groups[origin].append(s)

    new_runs = sum(len(v) for v in groups.values())
    run_counter = 0
    origin_stats: dict[str, dict[str, Any]] = {}
    rate_limit_warned = False

    # Step 3: Push each run via push_run() with shared state
    session_id = f"dataset_{backend_id}"

    for origin in ORIGIN_ORDER:
        runs = groups[origin]
        if not runs:
            continue

        _emit(f"  {origin}: {len(runs)} runs")

        accuracies: list[float] = []
        total_items = 0

        for run_summary in runs:
            rid = run_summary["run_id"]

            trace_id = push_run(
                lf, store, backend_id, rid,
                query_to_item_id=query_to_item_id,
                session_id=session_id,
                _state=state,
                _save=False,
            )

            if trace_id:
                # Collect stats from detail (already loaded inside push_run,
                # but lightweight to re-read for stats)
                detail = store.dataset_runs.load_by_id(backend_id, rid)
                if detail:
                    scores = detail.get("scores", {})
                    accuracies.append(scores.get("accuracy", 0.0))
                    total_items += len(detail.get("dataset_run_items", []))

            if lf.rate_limited and not rate_limit_warned:
                rate_limit_warned = True
                _emit(
                    "  ** Rate limit hit — traces/scores continue but "
                    "dataset linking paused until quota resets. "
                    "Re-run push later to complete linking."
                )

            run_counter += 1
            if run_counter % flush_every == 0:
                lf.flush()
                _emit(f"    [{run_counter}/{new_runs}] flushed")

        # Save state after each origin group (crash recovery)
        state["last_backfill_at"] = datetime.now(timezone.utc).isoformat()
        _save_state(store, backend_id, state)

        if accuracies:
            best_acc = max(accuracies)
            avg_acc = sum(accuracies) / len(accuracies)
        else:
            best_acc = 0.0
            avg_acc = 0.0

        origin_stats[origin] = {
            "n_runs": len(runs),
            "total_items": total_items,
            "best_accuracy": best_acc,
            "avg_accuracy": avg_acc,
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
        "session_id": session_id,
        "dataset_name": DATASET_NAME,
        "dataset_items": len(query_to_item_id),
        "rate_limit_hit": rate_limit_warned,
    }


# Backward-compatible alias
backfill_to_langfuse = push_all_runs
