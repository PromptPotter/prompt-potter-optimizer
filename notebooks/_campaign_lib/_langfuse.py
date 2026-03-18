"""Langfuse configuration, sync, and push utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.project_store import ProjectStore

__all__ = [
    "configure_langfuse",
    "sync_langfuse",
    "push_langfuse",
]


def configure_langfuse(
    *,
    enabled: bool | None = None,
    host: str | None = None,
    public_key: str | None = None,
    secret_key: str | None = None,
) -> None:
    """Configure Langfuse settings at runtime from a notebook cell.

    Mutates the global settings singleton and resets the LangfuseLogger
    singleton so the next call picks up new credentials.
    """
    from api.config.settings import settings
    from api.services.obs.langfuse_client import LangfuseLogger

    changed = False
    if enabled is not None:
        settings.LANGFUSE_ENABLED = enabled
        changed = True
    if host is not None:
        settings.LANGFUSE_HOST = host
        changed = True
    if public_key is not None:
        settings.LANGFUSE_PUBLIC_KEY = public_key
        changed = True
    if secret_key is not None:
        settings.LANGFUSE_SECRET_KEY = secret_key
        changed = True

    if changed:
        LangfuseLogger.reset_instance()
        lf = LangfuseLogger.get_instance()
        status = "enabled" if lf.enabled else "disabled"
        print(f"Langfuse reconfigured: {status}")


def sync_langfuse(
    store: "ProjectStore",
    backend_id: str,
    *,
    dataset_name: str = "termnorm_ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally push all runs.

    Returns:
        Push stats dict, or None if backfill was skipped.
    """
    import api.services.obs.langfuse_push as _lfp
    _lfp.DATASET_NAME = dataset_name

    if not backfill:
        print(f"Langfuse dataset: {dataset_name} (backfill disabled)")
        return None

    if reset:
        from api.services.obs.langfuse_push import _fresh_state, _save_state
        _save_state(store, backend_id, _fresh_state())
        print("Langfuse push state reset -- will re-push all runs.")

    n_runs = len(store.dataset_runs.list_all(backend_id))
    if n_runs == 0:
        print("No completed dataset runs yet -- skipping Langfuse backfill (run after eval).")
        return None

    return push_langfuse(store, backend_id)


def push_langfuse(store: "ProjectStore", backend_id: str) -> dict:
    """Push all historical dataset_runs to cloud Langfuse (dataset-first).

    Creates dataset items with ground truth, then one trace per run linked
    to dataset items. Re-running is safe -- already-pushed runs are skipped.

    Returns:
        Stats dict from ``push_all_runs()``.
    """
    from api.services.obs.langfuse_push import push_all_runs

    summaries = store.dataset_runs.list_all(backend_id)

    print("=" * 70)
    print("  LANGFUSE PUSH (dataset-first)")
    print("=" * 70)
    print(f"Found {len(summaries)} completed dataset runs for '{backend_id}'")

    stats = push_all_runs(store, backend_id, on_progress=print)

    if "error" in stats:
        print(f"\nPush aborted: {stats['error']}")
        return stats

    new_runs = stats["new_runs"]
    already = stats["already_done"]

    if new_runs == 0:
        print(f"\nAll {already} runs already pushed. Nothing to do.")
    else:
        print(f"\nPush complete: {new_runs} runs pushed to Langfuse")
        print(f"Session: {stats['session_id']}")

    print("=" * 70)
    print("  PUSH SUMMARY")
    print("=" * 70)
    print(f"  Total runs on disk:  {stats['total_on_disk']}")
    print(f"  Newly pushed:        {new_runs}")
    print(f"  Already done:        {already}")
    print(f"  Dataset:             {stats.get('dataset_name', 'N/A')}")
    print(f"  Dataset items:       {stats.get('dataset_items', 0)}")

    for origin, info in stats.get("origins", {}).items():
        n = info["n_runs"]
        items = info["total_items"]
        best = info["best_accuracy"]
        avg = info["avg_accuracy"]
        print(f"\n  {origin}:")
        print(f"    Runs: {n}, Items: {items}")
        print(f"    Best accuracy: {best:.1%}, Avg: {avg:.1%}")

    print("=" * 70)

    return stats
