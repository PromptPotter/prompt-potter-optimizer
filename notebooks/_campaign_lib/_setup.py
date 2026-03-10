"""Service init, LLM setup, backend status, dataset loading."""

from __future__ import annotations

import os
from pathlib import Path
from api.models.prompt_state import PromptState
from api.services.backend_client import (
    build_pipeline_params,
    load_pipeline_config,
)
from api.services.llm_client import AnthropicClient, GroqClient, LLMClientBase, OpenAIClient
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
    "configure_pipeline", "load_pipeline_config", "build_pipeline_params",
    # Pipeline snapshot
    "show_pipeline_snapshot",
    # Notebook-facing wrappers
    "smoke_test_override",
    # Re-exports
    "save_campaign_winner",
]


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

    await bc.init_session(svc.get("session_terms", []))

    schema = copy.deepcopy(
        pipeline_config["resolved_schemas"][schema_ref]["json_schema"],
    )
    for name, desc in extra_fields.items():
        schema["properties"][name] = {"type": "string", "description": desc}
        schema.setdefault("required", []).append(name)

    result = await bc.run_match(
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


def _infer_api_key(provider_url: str) -> str:
    """Return the appropriate API key env-var value for a provider URL."""
    if "anthropic.com" in provider_url:
        return os.environ.get("ANTHROPIC_API_KEY", "")
    if "openai.com" in provider_url:
        return os.environ.get("OPENAI_API_KEY", "")
    return os.environ.get("GROQ_API_KEY", "")


def _make_llm_client(provider_url: str = "", api_key: str = "") -> LLMClientBase:
    """Create an LLM client from a provider URL or key."""
    if "anthropic.com" in provider_url:
        return AnthropicClient(api_key=api_key)
    if "groq.com" in provider_url:
        return GroqClient(api_key=api_key)
    if "openai.com" in provider_url:
        return OpenAIClient(api_key=api_key)
    return GroqClient(api_key=api_key)


def setup_llm(
    campaign_config: dict, api_key: str = "",
) -> tuple[LLMClientBase, str]:
    """Create LLM client + model from campaign_config['eval_llm'].

    When *api_key* is empty, auto-detects the correct environment variable
    based on the provider URL (ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY).

    Returns:
        (llm_client, model) tuple for passing to service functions.
    """
    eval_llm = campaign_config["eval_llm"]
    provider_url = eval_llm.get("provider_url", "")
    key = api_key or _infer_api_key(provider_url)
    client = _make_llm_client(provider_url, key)
    return client, eval_llm.get("model", "")


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: dict,
    store: "ProjectStore",
    backend_id: str,
) -> dict:
    """Find best round, save to store. Returns save_data dict."""
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

    print(f"Winner saved: {filename} (acc={winner_acc:.1%})")
    return {
        **save_data,
        "winner_id": winner.id,
        "filename": filename,
        "backend_id": backend_id,
    }


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
    """Build pipeline_params from experiment data and campaign_config.

    Reads ``exclude_steps`` and ``pipeline_overrides`` from *campaign_config*,
    builds the active-step params, stores the result back into
    ``campaign_config["pipeline_params"]``, and returns the params dict.
    """
    pipeline_config = load_pipeline_config(svc["exp_data"])
    exclude = campaign_config.get("exclude_steps", [])
    overrides = campaign_config.get("pipeline_overrides")

    pipeline_params = build_pipeline_params(
        pipeline_config,
        overrides=overrides,
        exclude_steps=exclude,
    )
    campaign_config["pipeline_params"] = pipeline_params

    print(f"Active steps: {pipeline_params['steps']}")
    if exclude:
        print(f"  Excluded: {exclude}")

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


async def prepare_eval_context(
    svc: dict,
    train_data: list[dict] | None,
) -> tuple[PromptState, list[dict], dict]:
    """Load baseline prompt, set eval_data, check backend.

    Returns:
        (baseline, eval_data, backend_status)
    """
    from api.services.prompt_eval import extract_baseline_prompt as load_baseline_prompt
    baseline = load_baseline_prompt(svc["exp_data"])
    eval_data = train_data or []
    backend_status = await show_backend_status(svc["backend_client"])

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
