"""
Execution (pipeline replay) storage.
"""
from pathlib import Path
from typing import Any

from api.models.backend import Execution, ExecutionResultItem
from api.services.stores.base import (
    append_jsonl,
    read_json,
    read_json_optional,
    read_jsonl,
    validate_path_component,
    write_json,
)


class ExecutionStore:
    """File I/O for pipeline execution replays."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _executions_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "executions"

    # -- read / write ---------------------------------------------------------

    def save(self, execution: Execution) -> Path:
        """Save an execution result."""
        path = self._executions_dir(execution.backend_id) / (
            f"{execution.execution_id}.json"
        )
        write_json(path, execution.model_dump())
        return path

    def load(self, backend_id: str, execution_id: str) -> Execution | None:
        """Load an execution by ID. Returns None if not found."""
        data = read_json_optional(
            self._executions_dir(backend_id) / f"{execution_id}.json",
        )
        return Execution(**data) if data is not None else None

    def append_result(
        self, backend_id: str, execution_id: str, result: dict[str, Any],
    ) -> Path:
        """Append a single result as one JSON line to an in-progress .jsonl file."""
        return append_jsonl(
            self._executions_dir(backend_id) / f"{execution_id}.jsonl", result,
        )

    def load_partial_results(
        self, backend_id: str, execution_id: str,
    ) -> list[dict[str, Any]]:
        """Read all result lines from an in-progress .jsonl file."""
        return read_jsonl(
            self._executions_dir(backend_id) / f"{execution_id}.jsonl",
        )

    def finalize(self, execution: Execution) -> Path:
        """Merge .jsonl partial results into Execution, save .json, delete .jsonl."""
        jsonl_path = self._executions_dir(execution.backend_id) / (
            f"{execution.execution_id}.jsonl"
        )

        if not execution.results and jsonl_path.exists():
            partial = self.load_partial_results(
                execution.backend_id, execution.execution_id,
            )
            execution = execution.model_copy(
                update={
                    "results": [ExecutionResultItem(**r) for r in partial],
                    "query_count": len(partial),
                    "successful_count": sum(
                        1 for r in partial if r.get("status") == "success"
                    ),
                    "error_count": sum(
                        1 for r in partial if r.get("status") == "error"
                    ),
                }
            )

        json_path = self.save(execution)

        if jsonl_path.exists():
            jsonl_path.unlink()

        return json_path

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """List execution summaries (without full results array)."""
        d = self._executions_dir(backend_id)
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.json")):
            data = read_json(p)
            items.append({
                "execution_id": data["execution_id"],
                "backend_id": data["backend_id"],
                "experiment_id": data["experiment_id"],
                "variant_label": data.get("variant_label", ""),
                "pipeline_notation": data.get("pipeline_notation", ""),
                "query_count": data.get("query_count", 0),
                "successful_count": data.get("successful_count", 0),
                "created_at": data.get("created_at", ""),
            })
        return items
