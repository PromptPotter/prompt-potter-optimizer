"""
Dataset run (eval result caching) storage.
"""
import logging
import os
import time
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    append_jsonl,
    read_json,
    read_json_optional,
    read_jsonl,
    validate_path_component,
    write_json,
)

logger = logging.getLogger(__name__)

# Lock acquisition parameters
_LOCK_RETRY_INTERVAL = 0.05  # seconds between retries
_LOCK_TIMEOUT = 5.0  # seconds before treating lock as stale


class DatasetRunStore:
    """File I/O for dataset evaluation runs and incremental eval writes."""

    def __init__(self, base_dir: Path):
        self._base_dir = base_dir

    def _runs_dir(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "dataset_runs"

    def _index_path(self, backend_id: str) -> Path:
        return self._base_dir / backend_id / "dataset_runs.json"

    # -- complete runs --------------------------------------------------------

    @staticmethod
    def _acquire_lock(lock_path: Path) -> None:
        """Acquire an exclusive lock file, retrying on contention."""
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                # Check for stale lock (older than timeout)
                try:
                    age = time.monotonic() - os.path.getmtime(lock_path)
                    if age > _LOCK_TIMEOUT:
                        logger.warning("Removing stale lock file: %s (age=%.1fs)", lock_path, age)
                        try:
                            os.unlink(lock_path)
                        except OSError:
                            pass
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    logger.warning("Lock timeout on %s — breaking stale lock", lock_path)
                    try:
                        os.unlink(lock_path)
                    except OSError:
                        pass
                    continue
                time.sleep(_LOCK_RETRY_INTERVAL)

    @staticmethod
    def _release_lock(lock_path: Path) -> None:
        """Release the lock file."""
        try:
            os.unlink(lock_path)
        except OSError:
            pass

    def save(
        self, backend_id: str, run_id: str, data: dict[str, Any],
    ) -> Path:
        """Write detail file and upsert the index.

        ``data`` must include at least ``run_id``, ``content_hash``, and
        ``scores``.

        The detail file is written atomically (via ``write_json``).  The
        index update is protected by an exclusive lock file to prevent
        concurrent writers from losing entries.
        """
        detail_path = self._runs_dir(backend_id) / f"{run_id}.json"
        write_json(detail_path, data)

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
            "source": data.get("source", ""),
            "created_at": data.get("created_at", ""),
        }

        index_path = self._index_path(backend_id)
        lock_path = index_path.with_suffix(".json.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        self._acquire_lock(lock_path)
        try:
            if index_path.exists():
                index = read_json(index_path)
            else:
                index = {"dataset_runs": [], "total": 0}

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
            write_json(index_path, index)
        finally:
            self._release_lock(lock_path)

        return detail_path

    def load_by_id(
        self, backend_id: str, run_id: str,
    ) -> dict[str, Any] | None:
        """Load a dataset run detail file directly by run_id (no index scan)."""
        return read_json_optional(self._runs_dir(backend_id) / f"{run_id}.json")

    def load_by_hash(
        self, backend_id: str, content_hash: str,
    ) -> dict[str, Any] | None:
        """Scan the index for a matching content_hash, load and return detail."""
        index = read_json_optional(self._index_path(backend_id))
        if index is None:
            return None

        for entry in index.get("dataset_runs", []):
            if entry.get("content_hash") == content_hash:
                return read_json_optional(
                    self._runs_dir(backend_id) / f"{entry['run_id']}.json",
                )
        return None

    def list_all(self, backend_id: str) -> list[dict[str, Any]]:
        """Return the index entries (summaries without full items)."""
        index = read_json_optional(self._index_path(backend_id))
        if index is None:
            return []
        return index.get("dataset_runs", [])

    # -- incremental eval writes ----------------------------------------------

    def append_eval_item(
        self, backend_id: str, run_id: str, item: dict,
    ) -> Path:
        """Append one eval result to an in-progress .partial.jsonl file."""
        return append_jsonl(
            self._runs_dir(backend_id) / f"{run_id}.partial.jsonl", item,
        )

    def load_partial_eval(
        self, backend_id: str, run_id: str,
    ) -> list[dict[str, Any]]:
        """Read all items from an in-progress .partial.jsonl file."""
        return read_jsonl(
            self._runs_dir(backend_id) / f"{run_id}.partial.jsonl",
        )

    def list_partial_evals(self, backend_id: str) -> list[dict]:
        """List in-progress .partial.jsonl files with line counts."""
        d = self._runs_dir(backend_id)
        if not d.exists():
            return []
        results = []
        for p in sorted(d.glob("*.partial.jsonl")):
            run_id = p.name.removesuffix(".partial.jsonl")
            count = 0
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
            results.append({"run_id": run_id, "items": count, "path": str(p)})
        return results

    def finalize_eval_run(
        self, backend_id: str, run_id: str, run_data: dict,
    ) -> Path:
        """Save the complete dataset run and remove the .partial.jsonl file."""
        detail_path = self.save(backend_id, run_id, run_data)
        partial_path = self._runs_dir(backend_id) / f"{run_id}.partial.jsonl"
        if partial_path.exists():
            partial_path.unlink()
        return detail_path
