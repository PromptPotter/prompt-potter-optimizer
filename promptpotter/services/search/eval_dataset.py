"""Eval dataset loading from synced experiments or stored replays."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from promptpotter.services.project_store import ProjectStore

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extract_eval_from_traces(
    exp_data: dict,
    schema: PipelineSchema,
) -> list:
    """Build eval data from Langfuse-style traces in synced experiment data.

    Each trace in ``runs[0].traces[]`` carries named observations with
    pipeline step outputs.  Ground truth is joined from experiment
    mappings via the connector config's query parsing.

    Observation extraction is driven by the schema's ``obs_extraction_map()``.

    Returns:
        List of eval-data dicts (may be empty).
    """
    from promptpotter.config.connectors.termnorm import extract_ground_truth_map

    bom_to_gt = extract_ground_truth_map(exp_data)

    runs = exp_data.get("runs", [])
    if not runs:
        return []

    traces = runs[0].get("traces", [])
    if not traces:
        return []

    dataset = []
    for trace in traces:
        query = (trace.get("input") or {}).get("query", "")
        if not query:
            continue

        from promptpotter.config.connectors.termnorm import split_query

        bom_material, _ = split_query(query)
        ground_truth = bom_to_gt.get(bom_material)
        if not ground_truth:
            continue

        # Build pipeline_data from observations using declarative mapping
        pipeline_data: dict = {}
        llm_provider: str | None = None

        _obs_map = schema.obs_extraction_map()

        for obs in trace.get("observations", []):
            name = obs.get("name", "")
            output = obs.get("output")
            if not name or not output:
                continue

            for field in _obs_map.get(name, []):
                if field.output_field is None:
                    pipeline_data[field.pipeline_key] = output
                else:
                    val = output.get(field.output_field)
                    if val is not None:
                        pipeline_data[field.pipeline_key] = val

                if field.is_llm and not llm_provider:
                    llm_provider = (obs.get("metadata") or {}).get("model")

        # Gate: required pipeline key must be present
        if not pipeline_data.get(schema.required_pipeline_key()):
            continue

        if llm_provider:
            pipeline_data["llm_provider"] = llm_provider

        # Total time from trace scores (latency_ms → seconds)
        for score in trace.get("scores", []):
            if score.get("name") == "latency_ms" and score.get("value"):
                pipeline_data["total_time"] = score["value"] / 1000.0
                break

        dataset.append({
            "query": query,
            "ground_truth": ground_truth,
            "pipeline_data": pipeline_data,
            "status": "success",
        })

    return dataset


def load_eval_dataset(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
    sample_size: int = 0,
    schema: PipelineSchema | None = None,
) -> list:
    """Load per-query evaluation data from synced experiments or stored replays.

    Priority:
        1. Langfuse-style traces from ``runs[0].traces[]``
        2. Stored replay executions
        3. Empty list if neither found
    """
    exp_data = store.backends.load_sync(
        backend_id, f"experiments/{experiment_id}.json",
    )

    if exp_data:
        if schema is None:
            from promptpotter.models.pipeline_schema import PipelineSchema
            schema = PipelineSchema()
        dataset = _extract_eval_from_traces(exp_data, schema=schema)
        if dataset:
            if sample_size > 0 and len(dataset) > sample_size:
                rng = random.Random(42)
                dataset = rng.sample(dataset, sample_size)
            return dataset

    executions = store.backends.list_executions(backend_id)
    for ex_summary in executions:
        if ex_summary.get("experiment_id") == experiment_id:
            execution = store.backends.load_execution(
                backend_id, ex_summary["execution_id"],
            )
            if execution:
                req_key = schema.required_pipeline_key()
                dataset = [
                    r.model_dump() for r in execution.results
                    if r.status == "success"
                    and r.pipeline_data
                    and r.pipeline_data.get(req_key)
                ]
                if dataset:
                    if sample_size > 0 and len(dataset) > sample_size:
                        rng = random.Random(42)
                        dataset = rng.sample(dataset, sample_size)
                    return dataset

    return []
