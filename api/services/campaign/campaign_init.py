"""Campaign initialization service.

Sets up project store, backend client, auto-syncs experiment data,
and returns a services dict ready for use.
"""

import logging
from pathlib import Path

from api.models.backend import BackendConnection
from api.services.backend_client import BackendClient
from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


async def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
    project_root: Path | None = None,
) -> dict:
    """Initialize store, client, and load experiment data.

    If experiment data is not in the project store, attempts an automatic
    sync from the backend.  Connection errors are caught and logged so
    callers can still proceed (the user gets a clear message instead of
    a crash).

    Args:
        backend_url: Backend base URL.
        backend_id: Backend identifier.
        experiment_id: Experiment to load.
        project_root: Project root directory.  Defaults to two levels up
            from the notebooks directory.

    Returns:
        Dict with keys: store, client, queries, terms, exp_data,
        backend_id, experiment_id, backend_client, session_terms,
        synced (bool indicating whether auto-sync was attempted).
    """
    if project_root is None:
        # Default: assume called from notebooks/ subdirectory
        project_root = Path(__file__).resolve().parent.parent

    store = ProjectStore(base_dir=project_root / ".promptpotter" / "projects")
    client = BackendClient(backend_url)

    if not store.backends.get(backend_id):
        store.backends.register(BackendConnection(
            id=backend_id, name="TermNorm Local",
            backend_type="termnorm", base_url=backend_url,
        ))

    exp_data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Detect stale sync data: data exists but has no traces
    _has_traces = bool(
        exp_data
        and exp_data.get("runs")
        and exp_data["runs"][0].get("traces")
    )

    synced = False
    if not exp_data or not _has_traces:
        reason = "No stored experiment data" if not exp_data else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        try:
            await client.sync_experiments(store, backend_id, include_traces=True)
            exp_data = store.backends.load_sync(
                backend_id, f"experiments/{experiment_id}.json",
            )
            synced = True
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)

    base = {
        "store": store,
        "backend_id": backend_id,
        "experiment_id": experiment_id,
        "backend_client": client,
        "synced": synced,
    }

    if not exp_data:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced."
        )
        return {
            **base,
            "queries": [],
            "exp_data": {},
            "session_terms": [],
        }

    queries = client.extract_replay_queries(exp_data)

    return {
        **base,
        "queries": queries,
        "exp_data": exp_data,
        "session_terms": client.extract_session_terms(exp_data),
    }
