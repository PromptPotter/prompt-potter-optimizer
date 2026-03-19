"""Service init, LLM setup, backend status, dataset loading."""

from __future__ import annotations

from pathlib import Path
from api.models.prompt_state import PromptState
from api.services.backend_client import load_pipeline_config
from api.services.llm_client import LLMClientBase, get_llm_client
from api.services.project_store import ProjectStore

from api.services.campaign.campaign_init import init_services as _init_services
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
    # Notebook-facing wrappers
    "smoke_test_override",
    # Re-exports
    "save_campaign_winner",
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

    llm_client, llm_model = setup_llm(campaign_config)
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

    print(f"{'=' * 70}")
    print(f"TASK CONTEXT DECOMPOSITION{cache_tag}")
    print(f"{'=' * 70}")
    for field in TASK_CONTEXT_FIELDS:
        val = task_context.get(field, "")
        if val:
            print(f"  {field}: {val}")
        else:
            print(f"  {field}: (empty)")

    if result.get("consultation"):
        print(f"\n  Consultation: {result['consultation']}")
    print(f"{'=' * 70}")

    return task_context


# ---------------------------------------------------------------------------
# Smoke-test helpers
# ---------------------------------------------------------------------------


async def smoke_test_override(
    svc: dict,
    pipeline_config: dict,
    eval_data: list,
    *,
    step: str = "entity_profiling",
    schema_ref: str = "entity_profile/1",
    extra_fields: dict[str, str] | None = None,
) -> None:
    """Fire one /matches call with a schema override and print the result.

    Parameters
    ----------
    svc : dict
        Services dict from ``init_services()``.
    pipeline_config : dict
        Full pipeline config from ``fetch_pipeline()``.
    eval_data : list
        Eval dataset (first query used as test input).
    step : str
        Pipeline step to override (default ``entity_profiling``).
    schema_ref : str
        Key into ``resolved_schemas`` (default ``entity_profile/1``).
    extra_fields : dict
        ``{field_name: description}`` to inject into the schema.
        Defaults to ``{"industry_sector": "Primary industry sector ..."}``.
    """
    import copy

    if extra_fields is None:
        extra_fields = {
            "industry_sector":
                "Primary industry sector (e.g. automotive, construction, electronics)",
        }

    bc = svc["backend_client"]
    query = eval_data[0]["query"] if eval_data else "Stahl S235"

    bc.init_session(svc.get("session_terms", []))

    schema = copy.deepcopy(
        pipeline_config["resolved_schemas"][schema_ref]["json_schema"],
    )
    for name, desc in extra_fields.items():
        schema["properties"][name] = {"type": "string", "description": desc}
        schema.setdefault("required", []).append(name)

    result = bc.run_match(
        query,
        pipeline_params={"node_config": {step: {"output_schema": schema}}},
    )

    data = result.get("data", {})
    profile = data.get("entity_profile", {})
    top = (data.get("ranked_candidates") or [{}])[0]

    print(f"Query   : {query}")
    print(f"Top hit : {top.get('candidate', 'N/A')}"
          f" (score: {top.get('relevance_score', 0):.3f})")
    added = set(extra_fields)
    found = added & set(profile)
    print(f"Override: added {added} -- {'found' if found == added else 'MISSING'} in response")
    print()
    for k, v in profile.items():
        tag = " <- injected" if k in added else ""
        print(f"  {k}: {v}{tag}")


# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------


def setup_llm(campaign_config: dict) -> tuple[LLMClientBase, str]:
    """Create LLM client + model from campaign_config['eval_llm']."""
    eval_llm = campaign_config["eval_llm"]
    url = eval_llm.get("provider_url", "")
    if "anthropic.com" in url:
        provider = "anthropic"
    elif "openai.com" in url:
        provider = "openai"
    else:
        provider = "groq"
    return get_llm_client(provider), eval_llm.get("model", "")


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: dict,
    store: "ProjectStore",
    backend_id: str,
    *,
    experiment_id: str | None = None,
) -> dict:
    """Find best round, save to store + link to campaign. Returns save_data dict."""
    from datetime import datetime, timezone

    winner = campaign_rounds[-1]["prompt_state"]
    winner_acc = campaign_rounds[-1]["accuracy"]

    for rd in campaign_rounds:
        if rd["accuracy"] > winner_acc:
            winner = rd["prompt_state"]
            winner_acc = rd["accuracy"]

    baseline_acc = campaign_rounds[0]["accuracy"] if campaign_rounds else None
    save_data = {
        "winner": winner.model_dump(),
        "accuracy": winner_acc,
        "campaign_rounds": len(campaign_rounds),
        "baseline_accuracy": baseline_acc,
        "improvement": (winner_acc - baseline_acc) if baseline_acc is not None else None,
        "config": campaign_config,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.id[:12]}.json"
    store.backends.save_sync(backend_id, filename, save_data)

    # Link winner to campaign store if experiment_id provided
    if experiment_id:
        from notebooks._campaign_lib._optimize import _resolve_experiment_id
        full_id = _resolve_experiment_id(store, backend_id, experiment_id)
        if full_id:
            try:
                store.campaigns.update(backend_id, full_id, {
                    "winner_prompt_state_id": winner.id,
                    "winner_accuracy": winner_acc,
                    "winner_filename": filename,
                })
            except Exception:
                pass  # campaign may not exist yet

    print(f"Winner saved: {filename} (acc={winner_acc:.1%})")
    return {
        **save_data,
        "winner_id": winner.id,
        "filename": filename,
        "backend_id": backend_id,
    }


def show_pipeline_snapshot(svc: dict) -> dict:
    """Fetch and display full pipeline config from backend.

    Prints: pipeline name/version, node list, resolved schemas/prompts,
    full JSON config. Returns raw pipeline config dict.
    """
    import json

    pipeline_raw = svc["backend_client"].fetch_pipeline()
    config = pipeline_raw.get("data", pipeline_raw)

    name = config.get("name", "?")
    version = config.get("version", "?")
    nodes = list(config.get("nodes", {}).keys())
    schemas = list(config.get("resolved_schemas", {}).keys())
    prompts = list(config.get("resolved_prompts", {}).keys())

    print("=" * 70)
    print(f"  PIPELINE SNAPSHOT: {name} {version}")
    print("=" * 70)
    print(f"  Nodes:   {nodes}")
    print(f"  Schemas: {schemas}")
    print(f"  Prompts: {prompts}")
    print()
    print(json.dumps(config, indent=2))
    print("=" * 70)

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
        pipeline_config = load_pipeline_config(svc["exp_data"])
        all_steps = [s["name"] for s in pipeline_config["steps"]]

    active_steps = [s for s in all_steps if s not in (exclude or [])]
    pipeline_params: dict = {"steps": active_steps}

    # Apply overrides via schema if available
    if overrides and pipeline_schema:
        for flat_name, value in overrides.items():
            node_config = pipeline_schema.resolve_flat_param(flat_name)
            if node_config:
                pipeline_params.update(node_config)
            else:
                pipeline_params[flat_name] = value
    elif overrides:
        pipeline_params.update(overrides)

    campaign_config["pipeline_params"] = pipeline_params

    print(f"Active steps: {active_steps}")
    if exclude:
        print(f"  Excluded: {exclude}")

    return pipeline_params


# ---------------------------------------------------------------------------
# Setup (thin wrapper over campaign_init.init_services)
# ---------------------------------------------------------------------------


def init_services(
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

    svc = _init_services(
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


def show_backend_status(client) -> dict:
    """Call backend /status and display formatted status table.

    Returns the raw status dict (or error dict).
    """
    status = client.check_status()

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

    print("\nBACKEND STATUS")
    print("=" * 50)
    for key, val in data.items():
        if key == "experiments":
            continue  # displayed separately below
        label = key.replace("_", " ").title()
        print(f"  {label:<30s} {val}")

    # Per-experiment breakdown (if backend provides it)
    experiments = data.get("experiments")
    if experiments:
        print(f"  {'-' * 48}")
        print(f"  {'Experiments':30s}")
        for exp in experiments:
            eid = exp.get("id", "?")
            count = exp.get("mappings", 0)
            print(f"    {eid:<28s} {count} mappings")

    print("=" * 50)

    return status


def prepare_eval_context(
    svc: dict,
    train_data: list[dict] | None,
) -> tuple[PromptState, list[dict], dict]:
    """Load baseline prompt, set eval_data, check backend.

    Returns:
        (baseline, eval_data, backend_status)
    """
    from api.services.prompt_eval import load_baseline_prompt
    baseline = load_baseline_prompt(svc["exp_data"])
    eval_data = train_data or []
    backend_status = show_backend_status(svc["backend_client"])

    print(f"\nEvaluation data: {len(eval_data)} queries")
    return baseline, eval_data, backend_status


def build_all_session_terms(
    store: "ProjectStore",
    backend_id: str,
) -> list[str]:
    """Unique ground_truth identifiers across all stored datasets (train + test).

    For /match to work correctly, the session must contain ALL identifiers:
    - Train: query->ground_truth mappings (used for optimization evaluation)
    - Test: ground_truth only (identifiers in candidate pool, no query mapping)
    """
    gt_set: set[str] = set()
    for name in ("train", "test_processes", "test_material"):
        ds = store.datasets.load(backend_id, name)
        if ds and ds.get("items"):
            for item in ds["items"]:
                gt = item.get("ground_truth", "").strip()
                if gt:
                    gt_set.add(gt)
    return sorted(gt_set)


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
