"""Service init, LLM setup, backend status, dataset loading."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from api.models.opt_search_point import OptSearchPoint
from api.services.backend_client import extract_pipeline_config
from api.services.project_store import ProjectStore

if TYPE_CHECKING:
    from api.services.llm_client import LLMClientBase

from api.services.campaign.campaign_init import (
    init_services as _init_services,
    build_all_session_terms,
    create_llm_client as setup_llm,
    save_campaign_winner,
)
from api.services.dataset_builder import (
    load_excel_ground_truth as _load_excel_gt,
    train_test_split as _train_test_split,
    SHEET_COLUMN_MAP,
)
from api.config.settings import load_variant_library

__all__ = [
    # Service init
    "init_services", "setup_llm", "load_variant_library",
    # Backend status & datasets
    "show_backend_status", "show_dataset_summary", "build_all_session_terms",
    "load_or_create_datasets", "load_stored_dataset", "prepare_datasets",
    "prepare_eval_context",
    "SHEET_COLUMN_MAP",
    # Pipeline config
    "configure_pipeline",
    # Pipeline snapshot
    "show_pipeline_snapshot",
    # Task context
    "decompose_task_context",
    # Re-exports
    "save_campaign_winner",
    # Langfuse
    "configure_langfuse", "sync_langfuse", "push_langfuse",
    # Dev
    "dev_reload",
]


# ---------------------------------------------------------------------------
# Task context decomposition
# ---------------------------------------------------------------------------

TASK_CONTEXT_FIELDS = ("domain", "pipeline_purpose", "data_characteristics",
                       "optimization_goals", "key_challenges")


async def decompose_task_context(
    task_description: str,
    campaign_config: dict,
    svc: dict,
    *,
    llm_client: "LLMClientBase | None" = None,
    model: str | None = None,
) -> dict:
    """Decompose TASK_DESCRIPTION into structured domain context fields via LLM.

    Calls ``restructure_context_cached()`` and extracts the ``task_context``
    sub-dict. Prints the decomposed fields for visibility.

    Returns:
        Dict with keys: domain, pipeline_purpose, data_characteristics,
        optimization_goals, key_challenges.
    """
    import hashlib
    from api.services.search.context import restructure_context_cached

    if not task_description:
        print("  (no task description provided)")
        return {}

    if llm_client is None or model is None:
        _client, _model = setup_llm(campaign_config)
        llm_client = llm_client or _client
        model = model or _model
    llm_model = model
    store = svc.get("store")
    backend_id = svc.get("backend_id", "")
    improvement_areas = campaign_config.get("improvement_areas", "")

    # Content-hash for caching
    rp_hash = hashlib.sha256(
        f"task_ctx:{task_description}".encode(),
    ).hexdigest()[:16]

    result, was_cached = await restructure_context_cached(
        task_description, llm_client,
        model=llm_model,
        improvement_areas=improvement_areas,
        store_base_dir=store.base_dir if store else None,
        backend_id=backend_id,
        rp_hash=rp_hash,
    )

    task_context = result.get("task_context", {})
    task_context["raw_description"] = task_description
    cache_tag = " (cached)" if was_cached else ""

    print(f"TASK CONTEXT DECOMPOSITION{cache_tag}")
    print("-" * 50)
    for field in TASK_CONTEXT_FIELDS:
        val = task_context.get(field, "")
        if val:
            print(f"  {field}: {val}")
        else:
            print(f"  {field}: (empty)")

    if result.get("consultation"):
        print(f"  Consultation: {result['consultation']}")

    return task_context


# ---------------------------------------------------------------------------
# Dev helpers
# ---------------------------------------------------------------------------


def dev_reload() -> None:
    """Force-reload api modules so code edits take effect without kernel restart."""
    import importlib
    import sys

    for mod in [
        "api.services.campaign.escalation",
        "api.services.campaign.layer_transitions",
        "api.services.campaign.critique",
        "api.services.campaign.models",
        "api.services.campaign.feedback_cycle",
    ]:
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


async def show_pipeline_snapshot(svc: dict) -> dict:
    """Fetch and display full pipeline config from backend.

    Prints: pipeline name/version, node list, resolved schemas/prompts,
    full JSON config. Returns raw pipeline config dict.
    """
    import json

    pipeline_raw = await svc["backend_client"].fetch_pipeline()
    config = pipeline_raw.get("data", pipeline_raw)

    name = config.get("name", "?")
    version = config.get("version", "?")
    nodes = list(config.get("nodes", {}).keys())
    schemas = list(config.get("resolved_schemas", {}).keys())
    prompts = list(config.get("resolved_prompts", {}).keys())

    print(f"PIPELINE SNAPSHOT: {name} {version}")
    print("-" * 50)
    print(f"  Nodes:   {nodes}")
    print(f"  Schemas: {schemas}")
    print(f"  Prompts: {prompts}")
    print(json.dumps(config, indent=2))

    return config


def configure_pipeline(svc: dict, campaign_config: dict) -> dict:
    """Build pipeline_params from live pipeline schema and campaign_config.

    Uses ``svc["pipeline_schema"]`` (from ``GET /pipeline``) as the source of
    truth for step names, falling back to experiment data only when the schema
    is unavailable.  Reads ``exclude_steps`` and ``pipeline_overrides`` from
    *campaign_config*, stores the result back into
    ``campaign_config["pipeline_params"]``, and returns the params dict.
    """
    pipeline_schema = svc.get("pipeline_schema")
    exclude = campaign_config.get("exclude_steps", [])
    overrides = campaign_config.get("pipeline_overrides")

    if pipeline_schema:
        all_steps = [s.name for s in pipeline_schema.steps]
    else:
        pipeline_config = extract_pipeline_config(svc["exp_data"])
        all_steps = [s["name"] for s in pipeline_config["steps"]]

    active_steps = [s for s in all_steps if s not in (exclude or [])]
    pipeline_params: dict = {"steps": active_steps}

    # Apply overrides via schema if available
    if overrides and pipeline_schema:
        for flat_name, value in overrides.items():
            resolved = pipeline_schema.resolve_flat_param(flat_name)
            if resolved:
                node, wire_key = resolved
                pipeline_params.setdefault(node, {})[wire_key] = value
            else:
                pipeline_params[flat_name] = value
    elif overrides:
        pipeline_params.update(overrides)

    campaign_config["pipeline_params"] = pipeline_params

    steps_str = ", ".join(active_steps)
    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    print(f"Active steps: {steps_str}{excl_str}")

    return pipeline_params


# ---------------------------------------------------------------------------
# Setup (thin wrapper over campaign_init.init_services)
# ---------------------------------------------------------------------------


async def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "termnorm-local",
    experiment_id: str = "1_production_historical",
    dataset_name: str | None = None,
) -> dict:
    """Initialize store, client, and load experiment data.

    When *dataset_name* is provided, loads ground-truth data from the
    DatasetStore instead of requiring experiment traces.
    """
    project_root = Path(__file__).resolve().parent.parent.parent

    svc = await _init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=print,
    )

    exp_data = svc.get("exp_data", {})
    queries = svc.get("queries", [])

    if dataset_name and queries:
        print(f"Dataset    : {dataset_name} ({len(queries)} queries)")
        print(f"Session terms: {len(svc.get('session_terms', []))}")
        return svc

    if not exp_data:
        print(
            "WARNING: No experiment data available. "
            "Use load_or_create_datasets() to load from Excel, "
            "or sync from backend."
        )
        return svc

    mappings = exp_data.get("mappings", [])
    verified = sum(
        1 for m in mappings
        if m.get("dataset_entry", "").strip() not in ("", "--")
    )
    print(f"Experiment : {exp_data.get('experiment', {}).get('name', experiment_id)}")
    print(f"Mappings   : {len(mappings)} total, {verified} with verified ground truth")
    print(f"Queries    : {len(queries)}  |  "
          f"Session terms: {len(svc.get('session_terms', []))}")

    return svc


# ---------------------------------------------------------------------------
# Backend status & dataset loading
# ---------------------------------------------------------------------------


async def show_backend_status(client) -> dict:
    """Call backend /status and display formatted status table.

    Returns the raw status dict (or error dict).
    """
    status = await client.check_status()

    st = status.get("status", "")
    if st == "unreachable":
        print("BACKEND STATUS: unreachable")
        print(f"  Error: {status['error']}")
        return status
    if st == "not_implemented":
        print("BACKEND STATUS: connected (GET /status not available)")
        print("  Upgrade TermNorm backend to get detailed status info.")
        return status
    if "error" in status and st == "error":
        print("BACKEND STATUS: error")
        print(f"  {status['error']}")
        return status

    # Success -- TermNorm wraps data under "data" key
    data = status.get("data", status)

    print("BACKEND STATUS")
    print("-" * 40)
    for key, val in data.items():
        if key == "experiments":
            continue  # displayed separately below
        label = key.replace("_", " ").title()
        print(f"  {label:<30s} {val}")

    # Per-experiment breakdown (if backend provides it)
    experiments = data.get("experiments")
    if experiments:
        print(f"  {'Experiments':30s}")
        for exp in experiments:
            eid = exp.get("id", "?")
            count = exp.get("mappings", 0)
            print(f"    {eid:<28s} {count} mappings")

    return status


async def prepare_eval_context(
    svc: dict,
    train_data: list[dict] | None,
    campaign_config: dict | None = None,
    run_baseline: bool = False,
) -> tuple[OptSearchPoint, list[dict], list, list]:
    """Load baseline prompt, set eval_data, check backend, optionally run baseline.

    Returns:
        (baseline, eval_data, campaign_rounds, baseline_results)
    """
    from api.services.campaign.campaign_init import load_baseline_prompt
    baseline = load_baseline_prompt(svc["exp_data"])
    eval_data = train_data or []

    print(f"\nEvaluation data: {len(eval_data)} queries")

    campaign_rounds: list = []
    baseline_results: list = []
    if run_baseline and campaign_config is not None:
        from ._eval import run_baseline_eval
        campaign_rounds, baseline_results = await run_baseline_eval(
            baseline, eval_data, campaign_config, svc,
        )

    return baseline, eval_data, campaign_rounds, baseline_results



# build_all_session_terms is now imported from
# api.services.campaign.campaign_init (see imports above)


def show_dataset_summary(store: "ProjectStore", backend_id: str) -> dict:
    """Load all stored datasets and display combined summary.

    Returns dict with keys: train, test_processes, test_material,
    all_session_terms, total_queries.
    """
    result: dict[str, list[dict]] = {}
    for name in ("train", "test_processes", "test_material"):
        ds = store.datasets.load(backend_id, name)
        result[name] = ds["items"] if ds and ds.get("items") else []

    all_queries: set[str] = set()
    for items in result.values():
        for item in items:
            q = item.get("query", "").strip()
            if q:
                all_queries.add(q)

    session_terms = build_all_session_terms(store, backend_id)

    print("\nDATASET SUMMARY")
    print("=" * 50)
    print(f"  Train              : {len(result['train'])} queries")
    print(f"  Test (processes)   : {len(result['test_processes'])} queries")
    print(f"  Test (material)    : {len(result['test_material'])} queries")
    print(f"  {'-' * 48}")
    print(f"  Combined queries   : {len(all_queries)} (deduplicated)")
    print(f"  Session identifiers: {len(session_terms)} unique targets")
    print("=" * 50)

    return {
        **result,
        "all_session_terms": session_terms,
        "total_queries": len(all_queries),
    }


def prepare_datasets(
    store: "ProjectStore",
    backend_id: str,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> tuple[list[dict] | None, list[str]]:
    """Load/create datasets, build session terms, and display summary.

    Single entry point that replaces the separate show_dataset_summary +
    load_or_create_datasets + build_all_session_terms calls.

    Returns:
        (train_data, session_terms)
    """
    if excel_path:
        excel_path = Path(excel_path)
        col_map = SHEET_COLUMN_MAP
        existing = store.datasets.load(backend_id, "train")
        needs_create = force or not (existing and existing.get("items"))

        if needs_create:
            print(f"Loading ground truth from {excel_path.name} ...")
            all_rows = _load_excel_gt(excel_path, col_map)
            train, test_sets = _train_test_split(all_rows)
            store.datasets.save(backend_id, "train", train, source_file=excel_path.name)
            for name, items in test_sets.items():
                store.datasets.save(backend_id, name, items, source_file=excel_path.name)

    # Load all splits from store (whether just created or pre-existing)
    splits: dict[str, list[dict]] = {}
    for name in ("train", "test_processes", "test_material"):
        ds = store.datasets.load(backend_id, name)
        splits[name] = ds["items"] if ds and ds.get("items") else []

    train_data = splits["train"] or None
    session_terms = build_all_session_terms(store, backend_id)

    # Combined summary
    all_queries: set[str] = set()
    for items in splits.values():
        for item in items:
            q = item.get("query", "").strip()
            if q:
                all_queries.add(q)

    print(f"\n{'='*50}")
    print(f"  Train              : {len(splits['train'])} queries")
    print(f"  Test (processes)   : {len(splits['test_processes'])} queries")
    print(f"  Test (material)    : {len(splits['test_material'])} queries")
    print(f"  {'-' * 48}")
    print(f"  Combined queries   : {len(all_queries)} (deduplicated)")
    print(f"  Session identifiers: {len(session_terms)} unique targets")
    print(f"{'='*50}")

    return train_data, session_terms


def load_or_create_datasets(
    store: "ProjectStore",
    backend_id: str,
    excel_path: str | Path,
    *,
    sheet_column_map: dict | None = None,
    test_fraction: float = 0.2,
    seed: int = 42,
    force: bool = False,
) -> dict[str, list[dict]]:
    """Load datasets from store, or create from Excel + save.

    Returns:
        Dict with keys "train", "test_processes", "test_material",
        each a list of ``{"query", "ground_truth", ...}`` dicts.
    """
    col_map = sheet_column_map or SHEET_COLUMN_MAP

    # Check if already stored
    if not force:
        existing = store.datasets.load(backend_id, "train")
        if existing and existing.get("items"):
            result = {"train": existing["items"]}
            for name in ("test_processes", "test_material"):
                ds = store.datasets.load(backend_id, name)
                if ds:
                    result[name] = ds["items"]
                else:
                    result[name] = []
                    print(f"  WARNING: {name} not found on disk"
                          " -- run with force=True to recreate")
            total = sum(len(v) for v in result.values())
            print(f"Loaded stored datasets: {total} total rows")
            for name, items in result.items():
                print(f"  {name}: {len(items)} rows")
            return result

    # Create from Excel
    excel_path = Path(excel_path)
    print(f"Loading ground truth from {excel_path.name} ...")
    all_rows = _load_excel_gt(excel_path, col_map)
    print(f"  Total rows: {len(all_rows)}")

    train, test_sets = _train_test_split(all_rows, test_fraction=test_fraction, seed=seed)

    # Save all splits
    source = excel_path.name
    store.datasets.save(backend_id, "train", train, source_file=source)
    for name, items in test_sets.items():
        store.datasets.save(backend_id, name, items, source_file=source)

    result = {"train": train, **test_sets}
    print("\nDatasets saved:")
    for name, items in result.items():
        print(f"  {name}: {len(items)} rows")

    return result


def load_stored_dataset(
    store: "ProjectStore",
    backend_id: str,
    name: str,
) -> list[dict]:
    """Load a single named dataset from the store.

    Returns list of items, or empty list if not found.
    """
    ds = store.datasets.load(backend_id, name)
    if not ds or not ds.get("items"):
        print(f"Dataset '{name}' not found for backend '{backend_id}'.")
        return []
    items = ds["items"]
    print(f"Loaded dataset '{name}': {len(items)} rows "
          f"(source: {ds.get('source_file', '?')})")
    return items


# ---------------------------------------------------------------------------
# Langfuse operations (absorbed from _langfuse.py)
# ---------------------------------------------------------------------------


def configure_langfuse(
    *,
    enabled: bool | None = None,
    host: str | None = None,
    public_key: str | None = None,
    secret_key: str | None = None,
) -> None:
    """Configure Langfuse settings at runtime from a notebook cell."""
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
    store,
    backend_id: str,
    *,
    dataset_name: str = "termnorm_ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally push all runs."""
    from api.services.obs.langfuse_push import sync_langfuse_runs

    result = sync_langfuse_runs(
        store, backend_id,
        dataset_name=dataset_name, backfill=backfill, reset=reset,
    )
    if result is None:
        if not backfill:
            print(f"Langfuse dataset: {dataset_name} (backfill disabled)")
        else:
            print("No completed dataset runs yet — skipping Langfuse backfill.")
        return None
    return result


def push_langfuse(store, backend_id: str) -> dict:
    """Push all historical dataset_runs to cloud Langfuse (dataset-first)."""
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
