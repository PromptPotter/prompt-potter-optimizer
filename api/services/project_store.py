"""
File-based project store — facade over focused store modules.

All callers import ``ProjectStore`` from here. Internally it delegates to
:mod:`api.services.stores` sub-modules for each domain.

Layout on disk::

    .promptpotter/projects/
      {backend_id}/
        backend.json
        sync/
          experiments.json
          experiments/{experiment_id}.json
        executions/{execution_id}.json
        dataset_runs/{run_id}.json
        dataset_runs/{run_id}.partial.jsonl
        dataset_runs.json
        grid_plans/{plan_id}.json
        smart_search_plans/{plan_id}.json
"""

from pathlib import Path
from typing import Any

from api.models.backend import BackendConnection, Execution
from api.services.stores.backend_store import BackendStore
from api.services.stores.dataset_run_store import DatasetRunStore
from api.services.stores.execution_store import ExecutionStore
from api.services.stores.grid_plan_store import GridPlanStore
from api.services.stores.smart_search_store import SmartSearchStore

BASE_DIR = Path(".promptpotter") / "projects"


class ProjectStore:
    """Facade composing focused store modules.

    Preserves the original public API so existing callers
    (routers, services, notebooks, tests) work without changes.
    """

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or BASE_DIR
        self._backends = BackendStore(self.base_dir)
        self._executions = ExecutionStore(self.base_dir)
        self._dataset_runs = DatasetRunStore(self.base_dir)
        self._grid_plans = GridPlanStore(self.base_dir)
        self._smart_search = SmartSearchStore(self.base_dir)

    # -- backend CRUD -----------------------------------------------------

    def register_backend(self, backend: BackendConnection) -> Path:
        """Write backend.json for a new backend."""
        return self._backends.register(backend)

    def get_backend(self, backend_id: str) -> BackendConnection | None:
        """Read backend.json, return None if not found."""
        return self._backends.get(backend_id)

    def list_backends(self) -> list[BackendConnection]:
        """List all registered backends."""
        return self._backends.list_all()

    def delete_backend(self, backend_id: str) -> bool:
        """Delete a backend and all its data. Returns True if it existed."""
        return self._backends.delete(backend_id)

    def update_backend(self, backend: BackendConnection) -> None:
        """Overwrite backend.json with updated data."""
        self._backends.update(backend)

    # -- sync (verbatim API responses) ------------------------------------

    def save_sync(self, backend_id: str, key: str, data: Any) -> Path:
        """Store a verbatim API response under sync/."""
        return self._backends.save_sync(backend_id, key, data)

    def load_sync(self, backend_id: str, key: str) -> Any | None:
        """Read a synced API response. Returns None if not found."""
        return self._backends.load_sync(backend_id, key)

    def list_synced_experiments(
        self, backend_id: str,
    ) -> list[dict[str, Any]]:
        """List individual synced experiment files."""
        return self._backends.list_synced_experiments(backend_id)

    # -- executions -------------------------------------------------------

    def save_execution(self, execution: Execution) -> Path:
        """Save an execution result."""
        return self._executions.save(execution)

    def load_execution(
        self, backend_id: str, execution_id: str,
    ) -> Execution | None:
        """Load an execution by ID. Returns None if not found."""
        return self._executions.load(backend_id, execution_id)

    def append_result(
        self, backend_id: str, execution_id: str, result: dict[str, Any],
    ) -> Path:
        """Append a single result as one JSON line to an in-progress .jsonl."""
        return self._executions.append_result(
            backend_id, execution_id, result,
        )

    def load_partial_results(
        self, backend_id: str, execution_id: str,
    ) -> list[dict[str, Any]]:
        """Read all result lines from an in-progress .jsonl file."""
        return self._executions.load_partial_results(
            backend_id, execution_id,
        )

    def finalize_execution(self, execution: Execution) -> Path:
        """Merge .jsonl partial results into Execution, save, delete .jsonl."""
        return self._executions.finalize(execution)

    def list_executions(self, backend_id: str) -> list[dict[str, Any]]:
        """List execution summaries (without full results array)."""
        return self._executions.list_all(backend_id)

    # -- dataset runs (eval result caching) --------------------------------

    def save_dataset_run(
        self, backend_id: str, run_id: str, data: dict[str, Any],
    ) -> Path:
        """Write detail file and upsert the index."""
        return self._dataset_runs.save(backend_id, run_id, data)

    def load_dataset_run_by_hash(
        self, backend_id: str, content_hash: str,
    ) -> dict[str, Any] | None:
        """Scan the index for a matching content_hash, load detail."""
        return self._dataset_runs.load_by_hash(backend_id, content_hash)

    def list_dataset_runs(self, backend_id: str) -> list[dict[str, Any]]:
        """Return the index entries (summaries without full items)."""
        return self._dataset_runs.list_all(backend_id)

    # -- grid plans -------------------------------------------------------

    def save_grid_plan(
        self, backend_id: str, plan_id: str, plan_data: dict[str, Any],
    ) -> Path:
        """Write a grid search plan to disk."""
        return self._grid_plans.save(backend_id, plan_id, plan_data)

    def load_grid_plan(
        self, backend_id: str, plan_id: str,
    ) -> dict[str, Any] | None:
        """Read a grid search plan. Returns None if not found."""
        return self._grid_plans.load(backend_id, plan_id)

    def update_grid_plan_status(
        self, backend_id: str, plan_id: str, status: str,
    ) -> None:
        """Update the status field of an existing grid plan in-place."""
        self._grid_plans.update_status(backend_id, plan_id, status)

    def list_grid_plans(self, backend_id: str) -> list[dict[str, Any]]:
        """Return summary metadata for all grid plans on disk."""
        return self._grid_plans.list_all(backend_id)

    # -- incremental eval writes -------------------------------------------

    def append_eval_item(
        self, backend_id: str, run_id: str, item: dict,
    ) -> Path:
        """Append one eval result to an in-progress .partial.jsonl file."""
        return self._dataset_runs.append_eval_item(
            backend_id, run_id, item,
        )

    def load_partial_eval(
        self, backend_id: str, run_id: str,
    ) -> list[dict[str, Any]]:
        """Read all items from an in-progress .partial.jsonl file."""
        return self._dataset_runs.load_partial_eval(backend_id, run_id)

    def list_partial_evals(self, backend_id: str) -> list[dict]:
        """List in-progress .partial.jsonl files with line counts."""
        return self._dataset_runs.list_partial_evals(backend_id)

    def finalize_eval_run(
        self, backend_id: str, run_id: str, run_data: dict,
    ) -> Path:
        """Save the complete dataset run and remove the .partial.jsonl file."""
        return self._dataset_runs.finalize_eval_run(
            backend_id, run_id, run_data,
        )

    # -- smart search plans ------------------------------------------------

    def save_smart_search_plan(
        self, backend_id: str, plan_id: str, plan_data: dict[str, Any],
    ) -> Path:
        """Write a smart search plan to disk."""
        return self._smart_search.save(backend_id, plan_id, plan_data)

    def load_smart_search_plan(
        self, backend_id: str, plan_id: str,
    ) -> dict[str, Any] | None:
        """Read a smart search plan. Returns None if not found."""
        return self._smart_search.load(backend_id, plan_id)

    def update_smart_search_plan(
        self, backend_id: str, plan_id: str, updates: dict[str, Any],
    ) -> None:
        """Merge updates into an existing smart search plan."""
        self._smart_search.update(backend_id, plan_id, updates)

    def list_smart_search_plans(
        self, backend_id: str,
    ) -> list[dict[str, Any]]:
        """Return summary metadata for all smart search plans on disk."""
        return self._smart_search.list_all(backend_id)
