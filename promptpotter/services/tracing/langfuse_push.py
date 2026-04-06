"""
Push dataset runs to cloud Langfuse.

Dataset-first structure: registers dataset items with ground truth, then
creates one trace per *query* linked to the dataset via Dataset Runs.
Each trace shows the backend pipeline graph as child spans — standard
Langfuse pattern: 1 trace = 1 pipeline invocation. Node discovery is
schema-driven via ``PipelineNode.output_keys``.

Single entry point: ``push_all_runs()`` — batch push all historical runs
(called from the notebook's ``push_langfuse()`` cell). Internally delegates
to ``push_run()`` per run, always with dataset-item linking.

State is tracked in ``{backend_id}/obs/langfuse/backfill_state.json`` so
re-running only pushes new runs.

Usage::

    from promptpotter.services.tracing.langfuse_push import push_all_runs
    stats = push_all_runs(store, backend_id)
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.services.project_store import ProjectStore
from promptpotter.services.stores.base import read_json_optional, write_json
from promptpotter.shared.constants import DATASET_NAME as _DEFAULT_DATASET_NAME

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema


# ---------------------------------------------------------------------------
# Pipeline node extraction (merged from pipeline_nodes.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LangfuseObservation:
    """A single pipeline node ready to be pushed as a Langfuse observation."""

    name: str
    as_type: str
    input: dict[str, Any]
    output: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, int] | None = None


def _node_meta(
    timings: dict,
    node_params: dict,
    node_name: str,
    schema: "PipelineSchema",
) -> dict[str, Any]:
    """Build metadata dict for a pipeline node: timing + per-node params."""
    meta: dict[str, Any] = {}
    if node_name in timings:
        meta["duration_s"] = timings[node_name]
    param_keys = schema.node_param_keys().get(node_name, set())
    matched = {k: node_params[k] for k in param_keys if k in node_params}
    if matched:
        meta["pipeline_params"] = matched
    return meta


def extract_pipeline_nodes(
    pipeline_data: dict,
    query: str,
    schema: "PipelineSchema",
) -> list[LangfuseObservation]:
    """Parse pipeline_data into an ordered list of typed nodes.

    Schema-driven: walks ``schema.nodes`` in pipeline order and checks each
    node's ``output_keys`` (derived from ``observation_mappings``) against
    ``pipeline_data``.  Nodes without matching keys are omitted.
    """
    nodes: list[LangfuseObservation] = []
    timings = pipeline_data.get("step_timings") or {}
    llm_provider = pipeline_data.get("llm_provider", "")
    node_params = pipeline_data.get("pipeline_params") or {}

    for node in schema.nodes:
        output = {k: pipeline_data[k] for k in node.output_keys if pipeline_data.get(k) is not None}
        if not output:
            continue
        nodes.append(
            LangfuseObservation(
                name=node.name,
                as_type=node.langfuse_type,
                input={"query": query},
                output=output,
                metadata=_node_meta(timings, node_params, node.name, schema),
                model=llm_provider or None if node.is_llm else None,
            )
        )

    return nodes


# ---------------------------------------------------------------------------
# Langfuse push
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Fixed ordering for origin groups
ORIGIN_ORDER = [
    "baseline",
    "sensitivity_scan",
    "feedback_cycle",
    "optimization_loop",
    "smart_search_winner",
    "other",
]


def classify_run_origin(source: str = "") -> str:
    """Classify a dataset run's origin from its ``source`` field.

    Returns one of the ORIGIN_ORDER values, defaulting to ``"other"``.
    """
    if source in ORIGIN_ORDER:
        return source
    return "other"


def _state_path(store: ProjectStore, backend_id: str) -> Path:
    return store.base_dir / backend_id / "obs" / "langfuse" / "backfill_state.json"


def _load_state(store: ProjectStore, backend_id: str) -> dict[str, Any]:
    return read_json_optional(_state_path(store, backend_id)) or _fresh_state()


def _fresh_state() -> dict[str, Any]:
    return {
        "backfilled_run_ids": [],
        "last_backfill_at": None,
        "langfuse_trace_ids": {},  # {run_id: [trace_id, ...]}
        "dataset_items": {},
    }


def _save_state(store: ProjectStore, backend_id: str, state: dict[str, Any]) -> None:
    write_json(_state_path(store, backend_id), state)


def _collect_ground_truth(
    store: ProjectStore,
    backend_id: str,
    summaries: list[dict],
) -> dict[str, str]:
    """Extract unique (query -> ground_truth) pairs from all dataset_runs."""
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
    dataset_name: str = _DEFAULT_DATASET_NAME,
) -> dict[str, str]:
    """Register/update dataset items in Langfuse. Returns {query: item_id}.

    Creates the dataset if needed, then creates or updates each item
    with the ground truth as expected_output.
    """
    query_to_item_id: dict[str, str] = dict(state.get("dataset_items", {}))

    # Create dataset (idempotent) — fail fast on auth errors
    ok = lf.create_dataset(
        name=dataset_name,
        description="Production ground truth queries for prompt evaluation",
        metadata={"n_queries": len(gt_map)},
    )
    if not ok:
        raise RuntimeError(
            f"Langfuse: failed to create/access dataset '{dataset_name}'. "
            "Check LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env."
        )

    # Fetch existing items to find those needing updates
    existing_items: dict[str, Any] = {}  # query_str -> SDK item
    ds = lf.get_dataset(dataset_name)
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
                    item_id=existing.id,
                    expected_output=ground_truth,
                )
                updated += 1
            continue

        # Check if item exists in Langfuse but not in our state
        existing = existing_items.get(query)
        if existing:
            item_id = existing.id
            if getattr(existing, "expected_output", None) is None:
                lf.update_dataset_item(
                    item_id=item_id,
                    expected_output=ground_truth,
                )
                updated += 1
        else:
            item_id = lf.create_dataset_item(
                dataset_name=dataset_name,
                input={"query": query},
                expected_output=ground_truth,
                metadata={"source": "dataset"},
            )
            created += 1

        if item_id:
            query_to_item_id[query] = item_id

    if on_progress:
        on_progress(
            f"  Dataset '{dataset_name}': {len(query_to_item_id)} items "
            f"({created} created, {updated} updated)",
        )

    return query_to_item_id


# ---------------------------------------------------------------------------
# push_run — single-run cloud push (one trace per query)
# ---------------------------------------------------------------------------


def push_run(
    lf: Any,
    store: ProjectStore,
    backend_id: str,
    run_id: str,
    *,
    schema: Any | None = None,
    query_to_item_id: dict[str, str] | None = None,
    session_id: str | None = None,
    _state: dict[str, Any] | None = None,
    _save: bool = True,
) -> list[str] | None:
    """Push one dataset_run to Langfuse cloud as per-query pipeline traces.

    Creates one rooted trace per query item (via ``create_trace()``), then
    adds pipeline steps as child spans (via ``create_span()``). This produces
    a proper Langfuse graph: ``__start__ → target_pipeline → {steps} → __end__``.

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
        List of per-query Langfuse trace IDs, or None if already pushed
        or push failed.
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

    items = detail.get("dataset_run_items", [])
    origin = classify_run_origin(detail.get("source", ""))
    sid = session_id or f"dataset_{backend_id}"
    trace_ids: list[str] = []

    for it in items:
        query = it.get("query", "")
        pipeline = it.get("pipeline_data") or {}
        hit = it.get("hit", False)

        # Root chain anchor — visible in Langfuse graph as the trace root.
        # Child spans nest underneath, producing a proper pipeline graph.
        trace_metadata: dict[str, Any] = {
            "run_id": run_id,
            "llm_provider": pipeline.get("llm_provider", detail.get("model", "")),
            "prompt_fields_id": detail.get("prompt_fields_id", ""),
        }
        pp = pipeline.get("pipeline_params")
        if pp:
            trace_metadata["pipeline_params"] = pp

        trace_name = f"{schema.name}_pipeline" if schema and schema.name else "pipeline"
        trace_id = lf.create_trace(
            name=trace_name,
            input={"query": query, "ground_truth": it.get("ground_truth", "")},
            session_id=sid,
            tags=["eval", origin, "pipeline"],
            metadata=trace_metadata,
        )
        if not trace_id:
            continue

        if pipeline and schema:
            nodes = extract_pipeline_nodes(pipeline, query, schema=schema)
            for node in nodes:
                lf.create_span(
                    trace_id,
                    node.name,
                    node.input,
                    node.output,
                    node.metadata,
                    as_type=node.as_type,
                    model=node.model,
                    usage_details=node.usage_details,
                )

        lf.create_score(trace_id, "hit", 1.0 if hit else 0.0)
        trace_output: dict[str, Any] = {
            "predicted": it.get("predicted", ""),
            "ground_truth": it.get("ground_truth", ""),
            "hit": hit,
            "total_time": pipeline.get("total_time"),
        }
        if schema:
            for node in schema.nodes:
                for k in node.output_keys:
                    val = pipeline.get(k)
                    if val is not None:
                        trace_output[k] = val
        lf.update_trace(trace_id, output=trace_output)
        lf.end_trace(trace_id)

        # Link trace to dataset item
        if query_to_item_id:
            item_id = query_to_item_id.get(query)
            if item_id and not lf.rate_limited:
                lf.link_item_to_run(
                    dataset_item_id=item_id,
                    trace_id=trace_id,
                    run_name=run_id,
                    run_metadata={"origin": origin},
                )

        trace_ids.append(trace_id)

    if not trace_ids:
        return None

    # Update registry
    state["backfilled_run_ids"].append(run_id)
    state["langfuse_trace_ids"][run_id] = trace_ids

    if _save:
        state["last_backfill_at"] = datetime.now(UTC).isoformat()
        _save_state(store, backend_id, state)

    return trace_ids


# ---------------------------------------------------------------------------
# push_all_runs — batch push (renamed from backfill_to_langfuse)
# ---------------------------------------------------------------------------


def sync_langfuse_runs(
    store: ProjectStore,
    backend_id: str,
    *,
    dataset_name: str = "ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally push all runs.

    Returns:
        Push stats dict, or None if backfill was skipped.
    """
    if not backfill:
        logger.info("Langfuse dataset: %s (backfill disabled)", dataset_name)
        return None

    if reset:
        _save_state(store, backend_id, _fresh_state())
        logger.info("Langfuse push state reset — will re-push all runs.")

    n_runs = len(store.dataset_runs.list_all(backend_id))
    if n_runs == 0:
        logger.info("No completed dataset runs — skipping Langfuse backfill.")
        return None

    return push_all_runs(store, backend_id, dataset_name=dataset_name)


def push_all_runs(
    store: ProjectStore,
    backend_id: str,
    *,
    dataset_name: str = _DEFAULT_DATASET_NAME,
    flush_every: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Push all historical dataset_runs to cloud Langfuse.

    Dataset-first structure:
    1. Collect ground truth from all runs
    2. Register/update dataset items with expected_output
    3. Create one trace per query per run, link to dataset items
    4. Score each trace with hit (1/0)

    Idempotent — tracks which run_ids have been pushed in a state file
    and skips them on subsequent calls.

    Args:
        store: ProjectStore instance.
        backend_id: Backend identifier.
        flush_every: Flush Langfuse client every N runs.
        on_progress: Optional callback for progress messages.

    Returns:
        Stats dict with keys: total_on_disk, new_runs, already_done,
        origins (per-origin breakdown), error (if Langfuse disabled).
    """
    from promptpotter.services.tracing.langfuse_client import LangfuseLogger

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
    try:
        query_to_item_id = _register_dataset_items(
            lf, gt_map, state, on_progress, dataset_name=dataset_name
        )
    except RuntimeError as exc:
        _emit(f"  SKIP: {exc}")
        return {"skipped": True, "reason": str(exc)}
    state["dataset_items"] = query_to_item_id

    # Step 2: Group new runs by origin for stats
    groups: dict[str, list[dict]] = {origin: [] for origin in ORIGIN_ORDER}
    for s in summaries:
        rid = s.get("run_id", "")
        if rid in already_done:
            continue
        origin = classify_run_origin(s.get("source", ""))
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

            trace_ids = push_run(
                lf,
                store,
                backend_id,
                rid,
                query_to_item_id=query_to_item_id,
                session_id=session_id,
                _state=state,
                _save=False,
            )

            if trace_ids:
                # Collect stats from detail
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
        state["last_backfill_at"] = datetime.now(UTC).isoformat()
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
    state["last_backfill_at"] = datetime.now(UTC).isoformat()
    _save_state(store, backend_id, state)

    return {
        "total_on_disk": total_on_disk,
        "new_runs": new_runs,
        "already_done": total_on_disk - new_runs,
        "origins": origin_stats,
        "session_id": session_id,
        "dataset_name": dataset_name,
        "dataset_items": len(query_to_item_id),
        "rate_limit_hit": rate_limit_warned,
    }
