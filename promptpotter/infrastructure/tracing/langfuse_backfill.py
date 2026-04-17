"""Replay historical ``dataset_runs/`` entries into the Langfuse sink.

Reads JSON from disk and emits :class:`QueryEvalStart` / :class:`QueryNodeSpan`
/ :class:`QueryEvalEnd` events into an :class:`ObservabilityBridge`. The sink
owns every Langfuse SDK call — this file is just a disk walker.

Idempotency tracking (``backfilled_run_ids`` and legacy ``dataset_items``
seed) lives in ``obs/langfuse/backfill_state.json``; the sink's own
persisted id maps in ``sessions/{session_id}/langfuse_state.json`` are
the source of truth after the first replay.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.infrastructure.store import Stores
from promptpotter.infrastructure.store.base import read_json_optional, write_json
from promptpotter.infrastructure.tracing.bridge import ObservabilityBridge
from promptpotter.infrastructure.tracing.events import (
    QueryEvalEnd,
    QueryEvalStart,
    QueryNodeSpan,
)
from promptpotter.shared.constants import DATASET_NAME as _DEFAULT_DATASET_NAME

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema


logger = logging.getLogger(__name__)

ORIGIN_ORDER = [
    "baseline",
    "run_recon",
    "feedback_cycle",
    "optimization_loop",
    "adaptive_recon_winner",
    "other",
]


def classify_run_origin(source: str = "") -> str:
    """Classify a dataset run's origin from its ``source`` field."""
    if source in ORIGIN_ORDER:
        return source
    return "other"


@dataclass(frozen=True)
class LangfuseObservation:
    """A pipeline node extracted from ``dataset_runs/`` ready to emit."""

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
    schema: PipelineSchema,
) -> dict[str, Any]:
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
    schema: PipelineSchema,
) -> list[LangfuseObservation]:
    """Parse pipeline_data into an ordered list of typed nodes."""
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


def _state_path(store: Stores, backend_id: str) -> Path:
    return store.base_dir / backend_id / "obs" / "langfuse" / "backfill_state.json"


def _fresh_state() -> dict[str, Any]:
    return {
        "backfilled_run_ids": [],
        "last_backfill_at": None,
        "langfuse_trace_ids": {},
        "dataset_items": {},
    }


def _load_state(store: Stores, backend_id: str) -> dict[str, Any]:
    return read_json_optional(_state_path(store, backend_id)) or _fresh_state()


def _save_state(store: Stores, backend_id: str, state: dict[str, Any]) -> None:
    write_json(_state_path(store, backend_id), state)


def _collect_ground_truth(
    store: Stores,
    backend_id: str,
    summaries: list[dict],
) -> dict[str, str]:
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


def _replay_run(
    bridge: ObservabilityBridge,
    store: Stores,
    backend_id: str,
    run_id: str,
    schema: PipelineSchema | None,
    session_id: str,
    dataset_name: str,
) -> bool:
    """Replay one dataset_run's items as events. Returns ``True`` on success."""
    detail = store.dataset_runs.load_by_id(backend_id, run_id)
    if not detail:
        logger.warning("replay: could not load detail for run %s", run_id)
        return False

    items = detail.get("dataset_run_items", [])
    origin = classify_run_origin(detail.get("source", ""))
    any_pushed = False

    for it in items:
        query = it.get("query", "")
        if not query:
            continue
        pipeline = it.get("pipeline_data") or {}
        hit = bool(it.get("hit", False))

        bridge.emit(
            QueryEvalStart(
                run_id=run_id,
                query=query,
                ground_truth=it.get("ground_truth", ""),
                origin=origin,
                llm_provider=pipeline.get("llm_provider", detail.get("model", "")),
                prompt_fields_id=detail.get("prompt_fields_id", ""),
                pipeline_params=pipeline.get("pipeline_params") or None,
                schema_name=schema.name if schema and schema.name else "",
                session_id=session_id,
                dataset_name=dataset_name,
            )
        )

        if pipeline and schema:
            for node in extract_pipeline_nodes(pipeline, query, schema=schema):
                bridge.emit(
                    QueryNodeSpan(
                        run_id=run_id,
                        query=query,
                        node_name=node.name,
                        as_type=node.as_type,
                        input_data=node.input,
                        output_data=node.output,
                        metadata=node.metadata,
                        model=node.model,
                        usage_details=node.usage_details,
                    )
                )

        node_outputs: dict[str, Any] = {}
        if schema:
            for schema_node in schema.nodes:
                for k in schema_node.output_keys:
                    val = pipeline.get(k)
                    if val is not None:
                        node_outputs[k] = val

        bridge.emit(
            QueryEvalEnd(
                run_id=run_id,
                query=query,
                predicted=it.get("predicted", ""),
                ground_truth=it.get("ground_truth", ""),
                hit=hit,
                total_time=pipeline.get("total_time"),
                node_outputs=node_outputs,
            )
        )
        any_pushed = True

    return any_pushed


def sync_langfuse_runs(
    store: Stores,
    backend_id: str,
    *,
    dataset_name: str = "ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally replay all runs."""
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
    store: Stores,
    backend_id: str,
    *,
    dataset_name: str = _DEFAULT_DATASET_NAME,
    flush_every: int = 20,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Replay all historical dataset_runs through a Langfuse bridge."""
    bridge = ObservabilityBridge.from_settings(store.base_dir, backend_id)
    lf_sink = bridge.langfuse_sink
    if lf_sink is None:
        return {"error": "Langfuse is disabled (missing credentials or LANGFUSE_ENABLED=false)"}

    state = _load_state(store, backend_id)
    already_done = set(state["backfilled_run_ids"])

    summaries = store.dataset_runs.list_all(backend_id)
    total_on_disk = len(summaries)

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    _emit("Collecting ground truth from dataset runs...")
    gt_map = _collect_ground_truth(store, backend_id, summaries)
    try:
        query_to_item_id = lf_sink.reconcile_dataset(
            dataset_name,
            gt_map,
            seed_items=state.get("dataset_items") or None,
        )
    except RuntimeError as exc:
        _emit(f"  SKIP: {exc}")
        return {"skipped": True, "reason": str(exc)}
    state["dataset_items"] = query_to_item_id

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
            ok = _replay_run(
                bridge,
                store,
                backend_id,
                rid,
                schema=None,
                session_id=session_id,
                dataset_name=dataset_name,
            )
            if ok:
                detail = store.dataset_runs.load_by_id(backend_id, rid)
                if detail:
                    scores = detail.get("scores", {})
                    accuracies.append(scores.get("accuracy", 0.0))
                    total_items += len(detail.get("dataset_run_items", []))
                state["backfilled_run_ids"].append(rid)

            run_counter += 1
            if run_counter % flush_every == 0:
                bridge.flush()
                _emit(f"    [{run_counter}/{new_runs}] flushed")

        state["last_backfill_at"] = datetime.now(UTC).isoformat()
        _save_state(store, backend_id, state)

        best_acc = max(accuracies) if accuracies else 0.0
        avg_acc = sum(accuracies) / len(accuracies) if accuracies else 0.0
        origin_stats[origin] = {
            "n_runs": len(runs),
            "total_items": total_items,
            "best_accuracy": best_acc,
            "avg_accuracy": avg_acc,
        }

    bridge.flush()
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
        "rate_limit_hit": False,
    }
