"""
Dataset run (eval result caching) storage.
"""
import logging
import os
import time
from pathlib import Path
from typing import Any

from api.services.stores.base import (
    read_json,
    read_json_optional,
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
        """Acquire an exclusive lock file, retrying on contention.

        Uses short non-blocking retries so KeyboardInterrupt (Jupyter cell
        interrupt) is never swallowed by a blocking sleep.
        """
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return
            except FileExistsError:
                # Check for stale lock (file mtime older than timeout)
                try:
                    mtime = os.path.getmtime(lock_path)
                    age = time.time() - mtime
                    if age > _LOCK_TIMEOUT:
                        logger.warning(
                            "Removing stale lock file: %s (age=%.1fs)",
                            lock_path, age,
                        )
                        try:
                            os.unlink(lock_path)
                        except OSError:
                            pass
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    logger.warning(
                        "Lock timeout on %s — breaking stale lock", lock_path,
                    )
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
            "prompt_state_id": data["prompt_state_id"],
            "model": data["model"],
            "temperature": data["temperature"],
            "item_count": data["item_count"],
            "scores": data["scores"],
            "content_hash": data["content_hash"],
            "rendered_prompt_hash": data.get("rendered_prompt_hash", ""),
            "pipeline_params": data.get("pipeline_params"),
            "source": data.get("source", ""),
            "created_at": data["created_at"],
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

    def load_by_alias(
        self,
        backend_id: str,
        rp_hash: str,
        model: str,
        temperature: float,
        pipeline_params: dict | None,
        item_count: int,
    ) -> dict[str, Any] | None:
        """Find a cached run via prompt alias groups.

        Resolves ``rp_hash`` to its alias set, then scans the index for an
        entry whose ``rendered_prompt_hash``, ``model``, ``temperature``,
        ``pipeline_params``, and ``item_count`` all match.  Returns the
        detail file for the first match, or ``None``.
        """
        alias_set = self.resolve_aliases(backend_id, rp_hash)
        index = read_json_optional(self._index_path(backend_id))
        if index is None:
            return None

        def _strip_steps(pp):
            if not pp:
                return None
            stripped = {k: v for k, v in pp.items() if k != "steps"}
            return stripped or None

        norm_pp = _strip_steps(pipeline_params)

        for entry in index.get("dataset_runs", []):
            if entry.get("rendered_prompt_hash", "") not in alias_set:
                continue
            if entry.get("model") != model:
                continue
            if entry.get("temperature") != temperature:
                continue
            if _strip_steps(entry.get("pipeline_params")) != norm_pp:
                continue
            if entry.get("item_count") != item_count:
                continue
            return read_json_optional(
                self._runs_dir(backend_id) / f"{entry['run_id']}.json",
            )
        return None

    # -- prompt alias groups ---------------------------------------------------

    def _alias_path(self, backend_id: str) -> Path:
        validate_path_component(backend_id)
        return self._base_dir / backend_id / "prompt_aliases.json"

    def register_alias(self, backend_id: str, *hashes: str) -> None:
        """Link rendered_prompt_hashes as semantically equivalent.

        Merges into existing groups: if any hash is already grouped,
        the new hashes join that group.
        """
        hashes_set = {h for h in hashes if h}
        if len(hashes_set) < 2:
            return

        path = self._alias_path(backend_id)
        data = read_json_optional(path) or {"groups": []}
        groups: list[list[str]] = data["groups"]

        # Find all existing groups that overlap with the new hashes
        merged: set[str] = set(hashes_set)
        remaining: list[list[str]] = []
        for group in groups:
            if merged & set(group):
                merged |= set(group)
            else:
                remaining.append(group)

        remaining.append(sorted(merged))
        data["groups"] = remaining
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, data)

    def resolve_aliases(self, backend_id: str, rp_hash: str) -> set[str]:
        """Return all hashes equivalent to *rp_hash* (including itself)."""
        if not rp_hash:
            return set()
        data = read_json_optional(self._alias_path(backend_id))
        if not data:
            return {rp_hash}
        for group in data.get("groups", []):
            if rp_hash in group:
                return set(group)
        return {rp_hash}

