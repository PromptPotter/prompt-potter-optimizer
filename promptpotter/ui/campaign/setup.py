"""Service init, LLM setup, backend status, dataset loading."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.campaign.campaign_setup import (
    SessionEnv,
)
from promptpotter.application.campaign.campaign_setup import (
    init_services as _init_services,
)
from promptpotter.application.campaign.config import (
    create_llm_client as setup_llm,
)
from promptpotter.application.campaign.data import (
    build_all_index_terms,
)
from promptpotter.application.campaign.mgmt import (
    save_campaign_winner,
)
from promptpotter.application.search import load_variant_library
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.project_store import ProjectStore

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.infrastructure.llm.client import LLMClientBase

__all__ = [
    "build_all_index_terms",
    # Langfuse
    "configure_langfuse",
    # Pipeline config
    "configure_pipeline",
    # Task context
    "decompose_task_context",
    # Dev
    "dev_reload",
    # Service init
    "init_services",
    "load_variant_library",
    "prepare_datasets",
    "prepare_scoring_context",
    "push_langfuse",
    # Re-exports
    "save_campaign_winner",
    "setup_llm",
    # Backend status & datasets
    "show_backend_status",
    # Pipeline snapshot
    "show_pipeline_snapshot",
    "sync_langfuse",
]


# ---------------------------------------------------------------------------
# Task context decomposition
# ---------------------------------------------------------------------------


async def decompose_task_context(
    task_description: str,
    campaign_config: CampaignConfig,
    session: SessionEnv,
    *,
    llm_client: LLMClientBase | None = None,
    model: str | None = None,
) -> TaskDecomposition:
    """Decompose TASK_DESCRIPTION into structured domain context fields via LLM.

    Delegates to ``promptpotter.application.optimization.pipeline.decompose_task_context()``
    and prints the decomposed fields for visibility.
    """
    from promptpotter.application.optimization.pipeline import (
        decompose_task_context as _decompose_task_context,
    )

    if not task_description:
        print("  (no task description provided)")
        return TaskDecomposition()

    if llm_client is None or model is None:
        _client, _model = setup_llm(campaign_config)
        llm_client = llm_client or _client
        model = model or _model

    result = await _decompose_task_context(
        task_description,
        llm_client,
        model,
        store_base_dir=session.store.base_dir if session.store else None,
        backend_id=session.backend_id,
    )

    cache_tag = " (cached)" if result.was_cached else ""
    print(f"TASK CONTEXT DECOMPOSITION{cache_tag}")
    print("-" * 50)
    for f in result.task_context.FIELDS:
        val = getattr(result.task_context, f, "")
        print(f"  {f}: {val or '(empty)'}")

    if result.consultation:
        print(f"  Consultation: {result.consultation}")

    return result.task_context


# ---------------------------------------------------------------------------
# Dev helpers
# ---------------------------------------------------------------------------


def dev_reload() -> None:
    """Force-reload api modules so code edits take effect without kernel restart."""
    import importlib
    import sys

    for mod in [
        # Service layer — safe to reload (no Pydantic model classes)
        "promptpotter.shared.hashing",
        "promptpotter.application.campaign.config",
        "promptpotter.application.campaign.lifecycle",
        "promptpotter.application.optimization.nodes.escalation",
        "promptpotter.application.optimization.nodes.layer_transitions",
        "promptpotter.application.optimization.nodes.critique",
        "promptpotter.application.optimization.nodes.round_execution",
        "promptpotter.application.campaign.runner",
        "promptpotter.infrastructure.store.dataset_run_store",
        "promptpotter.application.scoring.stale_data",
        "promptpotter.application.scoring.sample_measurement",
        "promptpotter.services.dataset_scoring",
        "promptpotter.application.optimization.nodes.generate",
        "promptpotter.application.optimization.nodes.score",
        "promptpotter.application.optimization.nodes.formatting",
        "promptpotter.application.search.smart_search",
        "promptpotter.application.search.sensitivity_scanner",
        "promptpotter.application.search.scan_baseline",
        "promptpotter.application.campaign.campaign_setup",
        # Display layer — safe to reload (no model classes)
        "promptpotter.ui.campaign.display",
        "promptpotter.ui.campaign.phase_display",
        "promptpotter.ui.campaign.optimize",
        "promptpotter.ui.campaign.setup",
        "promptpotter.ui.campaign.campaigns",
        "promptpotter.ui.campaign.search",
        "promptpotter.ui.campaign.search_scan",
        # NOTE: Do NOT reload promptpotter.domain.* or dataclass modules —
        # Pydantic/dataclass classes break when reloaded (existing
        # instances fail type checks).  scan_results.py has ScanBrief
        # dataclass, so it must not be reloaded.
        # For model/dataclass changes, restart the kernel.
    ]:
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


async def show_pipeline_snapshot(session: SessionEnv) -> dict:
    """Fetch and display full pipeline config from backend.

    Prints: pipeline name/version, node list, resolved schemas/prompts,
    full JSON config. Returns raw pipeline config dict.
    """
    import json

    import httpx

    try:
        pipeline_raw = await session.backend_client.fetch_pipeline()
    except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
        base = session.backend_client.base_url
        print("WARNING: Backend unreachable — is the backend running?")
        print(f"  Could not connect to {base}/pipeline")
        print(f"  Error: {exc}")
        return {}

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


def configure_pipeline(session: SessionEnv, campaign_config: CampaignConfig) -> dict:
    """Build pipeline_params from live pipeline schema and campaign_config.

    Delegates to shared orchestration and adds display-specific tags.
    """
    from promptpotter.application.campaign.config import configure_and_apply_pipeline

    from .display import set_display_tags

    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=print)
    set_display_tags(session.pipeline_schema)
    return pipeline_params


# ---------------------------------------------------------------------------
# Setup (thin wrapper over init.init_services)
# ---------------------------------------------------------------------------


async def init_services(
    backend_url: str = "http://127.0.0.1:8000",
    backend_id: str = "",
    experiment_id: str = "1_production_historical",
    dataset_name: str | None = None,
    dataset_type: str | None = None,
    local_scoring_token: str | None = None,
) -> SessionEnv:
    """Initialize store, client, and load experiment data.

    All evaluation goes through :class:`BackendClient` by default.
    When *dataset_type* is ``"llm-only"``, uses :class:`LLMOnlyAdapter` —
    gated behind ``LOCAL_SCORING_SECRET`` + *local_scoring_token*.
    """
    from promptpotter.config.logging import setup_logging

    setup_logging()

    # campaign/setup.py → display → promptpotter → repo_root
    project_root = Path(__file__).resolve().parent.parent.parent.parent

    session = await _init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        dataset_type=dataset_type,
        local_scoring_token=local_scoring_token,
        on_status=print,
    )

    if dataset_name and session.queries:
        print(f"Dataset    : {dataset_name} ({len(session.queries)} queries)")
        print(f"Session terms: {len(session.index_terms)}")
        return session

    if not session.experiment_extract:
        print(
            "WARNING: No experiment data available. "
            "Use prepare_datasets() to load from Excel, "
            "or sync from backend."
        )
        return session

    mappings = session.experiment_extract.get("mappings", [])
    verified = sum(1 for m in mappings if m.get("dataset_entry", "").strip() not in ("", "--"))
    print(
        f"Experiment : {session.experiment_extract.get('experiment', {}).get('name', experiment_id)}"
    )
    print(f"Mappings   : {len(mappings)} total, {verified} with verified ground truth")
    print(f"Queries    : {len(session.queries)}  |  Session terms: {len(session.index_terms)}")

    return session


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
        print("  Upgrade your backend to get detailed status info.")
        return status
    if "error" in status and st == "error":
        print("BACKEND STATUS: error")
        print(f"  {status['error']}")
        return status

    # Success -- Backend wraps data under "data" key
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


async def prepare_scoring_context(
    session: SessionEnv,
    train_data: list[dict] | None,
    campaign_config: CampaignConfig | None = None,
    run_baseline: bool = True,
    pipeline_params: dict | None = None,
) -> tuple[OptSearchPoint, list[dict], list, list]:
    """Load baseline prompt, set dataset, optionally run baseline.

    Delegates to shared orchestration with notebook display.
    """
    from promptpotter.application.campaign.data import (
        prepare_scoring_context as _svc_prepare,
    )

    baseline, dataset, campaign_rounds, baseline_results = await _svc_prepare(
        session.experiment_extract,
        train_data,
        campaign_config,
        run_baseline=run_baseline,
        pipeline_params=pipeline_params,
        pipeline_schema=session.pipeline_schema,
        svc=session,
    )

    print(f"\nEvaluation data: {len(dataset)} queries")
    return baseline, dataset, campaign_rounds, baseline_results


def prepare_datasets(
    store: ProjectStore,
    backend_id: str,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> tuple[list[dict] | None, list[str]]:
    """Load/create datasets, build session terms, and display summary.

    Thin display wrapper around
    ``promptpotter.application.campaign.data.prepare_datasets()``.
    """
    from promptpotter.application.campaign.data import (
        prepare_datasets as _prepare_datasets,
    )

    if excel_path:
        print(f"Loading ground truth from {Path(excel_path).name} ...")

    result = _prepare_datasets(store, backend_id, excel_path, force=force)

    print(f"\n{'=' * 50}")
    print(f"  Train              : {len(result.splits.get('train', []))} queries")
    print(f"  Test (processes)   : {len(result.splits.get('test_processes', []))} queries")
    print(f"  Test (material)    : {len(result.splits.get('test_material', []))} queries")
    print(f"  {'-' * 48}")
    print(f"  Combined queries   : {result.n_unique_queries} (deduplicated)")
    print(f"  Session identifiers: {len(result.index_terms)} unique targets")
    print(f"{'=' * 50}")

    return result.train_data, result.index_terms


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
    from promptpotter.config.settings import settings
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

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
    dataset_name: str = "ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally push all runs."""
    from promptpotter.infrastructure.tracing.langfuse_push import sync_langfuse_runs

    result = sync_langfuse_runs(
        store,
        backend_id,
        dataset_name=dataset_name,
        backfill=backfill,
        reset=reset,
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
    from promptpotter.infrastructure.tracing.langfuse_push import push_all_runs

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
