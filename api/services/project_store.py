"""
File-based project store for backend data.

Layout:
    .promptpotter/projects/
      {backend_id}/
        backend.json                  # BackendConnection config
        sync/
          experiments.json            # GET /experiments (verbatim)
          experiments/
            {experiment_id}.json      # GET /experiments/{id} (verbatim)
        executions/
          {execution_id}.json         # Replay results
        dataset_runs.json             # INDEX — eval run summaries
        dataset_runs/
          {name}_{hash}.json          # DETAIL — full DatasetRun with items
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from api.models.backend import BackendConnection, Execution, ExecutionResultItem

BASE_DIR = Path(".promptpotter") / "projects"


class ProjectStore:
    """File I/O for .promptpotter/projects/."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or BASE_DIR

    # -- helpers ----------------------------------------------------------

    def _backend_dir(self, backend_id: str) -> Path:
        return self.base_dir / backend_id

    def _sync_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "sync"

    def _executions_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "executions"

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _read_json(path: Path) -> Any:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # -- backend CRUD -----------------------------------------------------

    def register_backend(self, backend: BackendConnection) -> Path:
        """Write backend.json for a new backend."""
        path = self._backend_dir(backend.id) / "backend.json"
        self._write_json(path, backend.model_dump())
        return path

    def get_backend(self, backend_id: str) -> Optional[BackendConnection]:
        """Read backend.json, return None if not found."""
        path = self._backend_dir(backend_id) / "backend.json"
        if not path.exists():
            return None
        return BackendConnection(**self._read_json(path))

    def list_backends(self) -> List[BackendConnection]:
        """List all registered backends."""
        if not self.base_dir.exists():
            return []
        backends = []
        for d in sorted(self.base_dir.iterdir()):
            cfg = d / "backend.json"
            if cfg.exists():
                backends.append(BackendConnection(**self._read_json(cfg)))
        return backends

    def delete_backend(self, backend_id: str) -> bool:
        """Delete a backend and all its data. Returns True if it existed."""
        import shutil

        d = self._backend_dir(backend_id)
        if not d.exists():
            return False
        shutil.rmtree(d)
        return True

    def update_backend(self, backend: BackendConnection) -> None:
        """Overwrite backend.json with updated data."""
        path = self._backend_dir(backend.id) / "backend.json"
        self._write_json(path, backend.model_dump())

    # -- sync (verbatim API responses) ------------------------------------

    def save_sync(self, backend_id: str, key: str, data: Any) -> Path:
        """Store a verbatim API response under sync/.

        ``key`` is a relative path like ``experiments.json`` or
        ``experiments/{id}.json``.
        """
        path = self._sync_dir(backend_id) / key
        self._write_json(path, data)
        return path

    def load_sync(self, backend_id: str, key: str) -> Optional[Any]:
        """Read a synced API response. Returns None if not found."""
        path = self._sync_dir(backend_id) / key
        if not path.exists():
            return None
        return self._read_json(path)

    def list_synced_experiments(self, backend_id: str) -> List[Dict[str, Any]]:
        """List individual synced experiment files."""
        exp_dir = self._sync_dir(backend_id) / "experiments"
        if not exp_dir.exists():
            return []
        results = []
        for p in sorted(exp_dir.glob("*.json")):
            results.append(self._read_json(p))
        return results

    # -- executions -------------------------------------------------------

    def save_execution(self, execution: Execution) -> Path:
        """Save an execution result."""
        path = (
            self._executions_dir(execution.backend_id)
            / f"{execution.execution_id}.json"
        )
        self._write_json(path, execution.model_dump())
        return path

    def load_execution(
        self, backend_id: str, execution_id: str
    ) -> Optional[Execution]:
        """Load an execution by ID. Returns None if not found."""
        path = self._executions_dir(backend_id) / f"{execution_id}.json"
        if not path.exists():
            return None
        return Execution(**self._read_json(path))

    def append_result(
        self, backend_id: str, execution_id: str, result: Dict[str, Any]
    ) -> Path:
        """Append a single result as one JSON line to an in-progress .jsonl file."""
        path = self._executions_dir(backend_id) / f"{execution_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
            f.flush()
        return path

    def load_partial_results(
        self, backend_id: str, execution_id: str
    ) -> List[Dict[str, Any]]:
        """Read all result lines from an in-progress .jsonl file.

        Returns an empty list if no .jsonl exists.
        """
        path = self._executions_dir(backend_id) / f"{execution_id}.jsonl"
        if not path.exists():
            return []
        results = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        return results

    def finalize_execution(self, execution: Execution) -> Path:
        """Merge .jsonl partial results into Execution, save .json, delete .jsonl."""
        jsonl_path = (
            self._executions_dir(execution.backend_id)
            / f"{execution.execution_id}.jsonl"
        )

        if not execution.results and jsonl_path.exists():
            partial = self.load_partial_results(
                execution.backend_id, execution.execution_id
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

        json_path = self.save_execution(execution)

        if jsonl_path.exists():
            jsonl_path.unlink()

        return json_path

    def list_executions(self, backend_id: str) -> List[Dict[str, Any]]:
        """List execution summaries (without full results array)."""
        d = self._executions_dir(backend_id)
        if not d.exists():
            return []
        items = []
        for p in sorted(d.glob("*.json")):
            data = self._read_json(p)
            # Return summary without the large results list
            items.append(
                {
                    "execution_id": data["execution_id"],
                    "backend_id": data["backend_id"],
                    "experiment_id": data["experiment_id"],
                    "variant_label": data.get("variant_label", ""),
                    "pipeline_notation": data.get("pipeline_notation", ""),
                    "query_count": data.get("query_count", 0),
                    "successful_count": data.get("successful_count", 0),
                    "created_at": data.get("created_at", ""),
                }
            )
        return items

    # -- dataset runs (eval result caching) --------------------------------

    def _dataset_runs_dir(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "dataset_runs"

    def _dataset_runs_index_path(self, backend_id: str) -> Path:
        return self._backend_dir(backend_id) / "dataset_runs.json"

    def save_dataset_run(
        self, backend_id: str, run_id: str, data: Dict[str, Any]
    ) -> Path:
        """Write detail file and append/update the index.

        ``data`` must include at least ``run_id``, ``content_hash``, and
        ``scores``.  The detail file is written to
        ``dataset_runs/{run_id}.json`` and a summary entry is upserted into
        ``dataset_runs.json``.
        """
        # Write detail file
        detail_path = self._dataset_runs_dir(backend_id) / f"{run_id}.json"
        self._write_json(detail_path, data)

        # Build summary entry for the index
        summary = {
            "run_id": data["run_id"],
            "name": data.get("name", run_id),
            "experiment_id": data.get("experiment_id", ""),
            "prompt_state_id": data.get("prompt_state_id", ""),
            "model": data.get("model", ""),
            "temperature": data.get("temperature", 0),
            "item_count": data.get("item_count", 0),
            "scores": data.get("scores", {}),
            "content_hash": data.get("content_hash", ""),
            "created_at": data.get("created_at", ""),
        }

        # Load or create index
        index_path = self._dataset_runs_index_path(backend_id)
        if index_path.exists():
            index = self._read_json(index_path)
        else:
            index = {"dataset_runs": [], "total": 0}

        # Upsert: replace existing entry with same content_hash, else append
        content_hash = data.get("content_hash", "")
        entries = index["dataset_runs"]
        replaced = False
        for i, entry in enumerate(entries):
            if entry.get("content_hash") == content_hash:
                entries[i] = summary
                replaced = True
                break
        if not replaced:
            entries.append(summary)

        index["total"] = len(entries)
        self._write_json(index_path, index)

        return detail_path

    def load_dataset_run_by_hash(
        self, backend_id: str, content_hash: str
    ) -> Optional[Dict[str, Any]]:
        """Scan the index for a matching content_hash, load and return detail.

        Returns None on miss.
        """
        index_path = self._dataset_runs_index_path(backend_id)
        if not index_path.exists():
            return None

        index = self._read_json(index_path)
        for entry in index.get("dataset_runs", []):
            if entry.get("content_hash") == content_hash:
                detail_path = (
                    self._dataset_runs_dir(backend_id)
                    / f"{entry['run_id']}.json"
                )
                if detail_path.exists():
                    return self._read_json(detail_path)
        return None

    def list_dataset_runs(self, backend_id: str) -> List[Dict[str, Any]]:
        """Return the index entries (summaries without full items)."""
        index_path = self._dataset_runs_index_path(backend_id)
        if not index_path.exists():
            return []
        index = self._read_json(index_path)
        return index.get("dataset_runs", [])
