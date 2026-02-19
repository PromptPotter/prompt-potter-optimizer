"""
Dataset loader for TermNorm experiments.

Fetches traces from TermNorm API and loads ground truth from Langfuse-format files.
"""
import json
import httpx
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class DatasetItem(BaseModel):
    """A single dataset item with input and expected output."""

    id: str = Field(..., description="Unique item identifier")
    input: Dict[str, Any] = Field(..., description="Input data (e.g., {'query': 'term'})")
    expected_output: Optional[Dict[str, Any]] = Field(
        None, description="Expected output (e.g., {'target': 'normalized'})"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_trace_id: Optional[str] = None


class TraceItem(BaseModel):
    """A trace from TermNorm pipeline execution."""

    trace_id: str
    input: Dict[str, Any] = Field(default_factory=dict)
    output: Optional[Dict[str, Any]] = None
    request_time: Optional[str] = None
    execution_duration_ms: Optional[int] = None
    state: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)


class Dataset(BaseModel):
    """A collection of dataset items."""

    name: str
    items: List[DatasetItem] = Field(default_factory=list)
    source: str = Field(..., description="Where data came from: 'api' or 'file'")

    def __len__(self) -> int:
        return len(self.items)


class DatasetLoader:
    """Load datasets from TermNorm API or local Langfuse files."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    async def fetch_experiments(self) -> List[Dict[str, Any]]:
        """List all experiments from TermNorm API."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/experiments")
            resp.raise_for_status()
            data = resp.json()
            return data.get("experiments", [])

    async def fetch_traces(
        self,
        experiment_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[TraceItem]:
        """
        Fetch traces from TermNorm experiments API.

        These are pipeline executions with input/output, useful for seeing
        what was processed but may not have ground truth expected_output.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/experiments/{experiment_id}/traces",
                params={"limit": limit, "offset": offset}
            )
            resp.raise_for_status()
            data = resp.json()

            traces = []
            for t in data.get("traces", []):
                traces.append(TraceItem(
                    trace_id=t.get("trace_id", ""),
                    input=t.get("input", {}),
                    output=t.get("output"),
                    request_time=t.get("request_time"),
                    execution_duration_ms=t.get("execution_duration_ms"),
                    state=t.get("state"),
                    tags=t.get("tags", {})
                ))
            return traces

    async def fetch_trace_detail(
        self,
        experiment_id: str,
        trace_id: str
    ) -> Dict[str, Any]:
        """Fetch full trace details including spans."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/experiments/{experiment_id}/traces/{trace_id}"
            )
            resp.raise_for_status()
            return resp.json().get("trace", {})

    def load_from_traces_json(self, path: str) -> Dataset:
        """
        Load dataset from saved traces JSON file (from API export).

        Handles MLflow trace format where input/output are in tags.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Traces file not found: {path}")

        data = json.loads(file_path.read_text(encoding="utf-8"))
        traces = data.get("traces", [])

        items = []
        for t in traces:
            trace_id = t.get("trace_id", "")
            tags = t.get("tags", {})

            # Parse input from MLflow tags
            input_str = tags.get("mlflow.traceInputs", "{}")
            try:
                input_data = json.loads(input_str)
            except json.JSONDecodeError:
                input_data = {"raw": input_str}

            # Parse output from MLflow tags (this is the "ground truth" for historical data)
            output_str = tags.get("mlflow.traceOutputs", "{}")
            try:
                output_data = json.loads(output_str)
            except json.JSONDecodeError:
                output_data = {"raw": output_str}

            if input_data:
                items.append(DatasetItem(
                    id=trace_id,
                    input=input_data,
                    expected_output=output_data if output_data else None,
                    metadata={
                        "request_time": t.get("request_time", "").strip("'"),
                        "execution_duration_ms": t.get("execution_duration_ms"),
                        "state": t.get("state"),
                        "confidence": tags.get("score.confidence"),
                        "latency_ms": tags.get("score.latency_ms"),
                    },
                    source_trace_id=trace_id
                ))

        return Dataset(
            name=file_path.stem,
            items=items,
            source=f"file:{path}"
        )

    def load_from_langfuse_dir(
        self,
        path: str,
        dataset_name: str = "termnorm_ground_truth"
    ) -> Dataset:
        """
        Load ground truth dataset items from Langfuse-format directory.

        Expects structure: {path}/datasets/{dataset_name}/item-*.json
        """
        base_path = Path(path)
        dataset_path = base_path / "datasets" / dataset_name

        if not dataset_path.exists():
            # Try without datasets subdirectory
            dataset_path = base_path / dataset_name

        if not dataset_path.exists():
            return Dataset(name=dataset_name, items=[], source=f"file:{path}")

        items = []
        for item_file in dataset_path.glob("item-*.json"):
            try:
                data = json.loads(item_file.read_text(encoding="utf-8"))
                items.append(DatasetItem(
                    id=data.get("id", item_file.stem),
                    input=data.get("input", {}),
                    expected_output=data.get("expected_output"),
                    metadata=data.get("metadata", {}),
                    source_trace_id=data.get("source_trace_id")
                ))
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Failed to load {item_file}: {e}")

        return Dataset(
            name=dataset_name,
            items=items,
            source=f"file:{dataset_path}"
        )

    async def traces_to_dataset(
        self,
        traces: List[TraceItem],
        name: str = "traces"
    ) -> Dataset:
        """
        Convert traces to a dataset.

        Uses trace output as expected_output (assumes traces are ground truth).
        Useful when traces have been user-corrected.
        """
        items = []
        for t in traces:
            if t.input:
                items.append(DatasetItem(
                    id=t.trace_id,
                    input=t.input,
                    expected_output=t.output,
                    metadata={
                        "request_time": t.request_time,
                        "execution_duration_ms": t.execution_duration_ms,
                        "state": t.state,
                    },
                    source_trace_id=t.trace_id
                ))

        return Dataset(name=name, items=items, source="api:traces")


# Convenience function for notebook use
async def load_dataset(
    source: str = "api",
    base_url: str = "http://127.0.0.1:8000",
    experiment_id: Optional[str] = None,
    file_path: Optional[str] = None,
    dataset_name: str = "termnorm_ground_truth",
    limit: int = 100
) -> Dataset:
    """
    Load a dataset from API or file.

    Args:
        source: "api" to fetch from TermNorm, "file" to load from disk
        base_url: TermNorm API URL (for source="api")
        experiment_id: Experiment to fetch traces from (for source="api")
        file_path: Path to langfuse logs directory (for source="file")
        dataset_name: Name of dataset subdirectory
        limit: Max traces to fetch from API

    Returns:
        Dataset with items
    """
    loader = DatasetLoader(base_url=base_url)

    if source == "api":
        if not experiment_id:
            # Get first experiment
            experiments = await loader.fetch_experiments()
            if not experiments:
                raise ValueError("No experiments found")
            experiment_id = experiments[0].get("experiment_id", experiments[0].get("name", ""))

        traces = await loader.fetch_traces(experiment_id, limit=limit)
        return await loader.traces_to_dataset(traces, name=f"{experiment_id}_traces")

    elif source == "file":
        if not file_path:
            raise ValueError("file_path required for source='file'")
        return loader.load_from_langfuse_dir(file_path, dataset_name)

    else:
        raise ValueError(f"Unknown source: {source}")
