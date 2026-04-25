"""Service init, LLM setup, backend status, dataset loading."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.campaign.campaign_setup import (
    Session,
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
from promptpotter.application.campaign.utils import (
    save_campaign_winner,
)
from promptpotter.application.intelligence import load_variant_library
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store import Stores

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.infrastructure.llm.client import LLMClientBase

__all__ = [
    "build_all_index_terms",
    "configure_pipeline",
    "decompose_task_context",
    "dev_reload",
    "init_services",
    "load_variant_library",
    "prepare_datasets",
    "prepare_scoring_context",
    "save_campaign_winner",
    "setup_llm",
    "show_backend_status",
    "show_pipeline_snapshot",
]


# ---------------------------------------------------------------------------
# Task context decomposition
# ---------------------------------------------------------------------------


async def decompose_task_context(
    task_description: str,
    campaign_config: CampaignConfig,
    session: Session,
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
        "promptpotter.application.optimization.elimination",
        "promptpotter.application.optimization.layer_escalation",
        "promptpotter.application.optimization.nodes.layer_transitions",
        "promptpotter.application.optimization.nodes.l1_critique",
        "promptpotter.application.optimization.nodes.round_execution",
        "promptpotter.application.campaign.runner",
        "promptpotter.infrastructure.store.dataset_run_store",
        "promptpotter.application.scoring.stale_data",
        "promptpotter.application.scoring.sample_measurement",
        "promptpotter.services.dataset_scoring",
        "promptpotter.application.optimization.nodes.generate",
        "promptpotter.application.optimization.nodes.score",
        "promptpotter.application.optimization.nodes.formatting",
        "promptpotter.application.campaign.campaign_setup",
        # Display layer — safe to reload (no model classes)
        "promptpotter.presentation.views.display_primitives",
        "promptpotter.presentation.ui.campaign.notebook_phase",
        "promptpotter.presentation.ui.campaign.notebook_display",
        "promptpotter.presentation.ui.campaign.optimize",
        "promptpotter.presentation.ui.campaign.setup",
        "promptpotter.presentation.ui.campaign.campaigns",
        # NOTE: Do NOT reload promptpotter.domain.* or dataclass modules —
        # Pydantic/dataclass classes break when reloaded (existing
        # instances fail type checks).  For model/dataclass changes,
        # restart the kernel.
    ]:
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


async def show_pipeline_snapshot(session: Session) -> dict:
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


def configure_pipeline(session: Session, campaign_config: CampaignConfig) -> dict:
    """Build pipeline_params from live pipeline schema and campaign_config.

    Delegates to shared orchestration and adds display-specific tags.
    """
    from promptpotter.application.campaign.config import configure_and_apply_pipeline
    from promptpotter.presentation.views.display_primitives import set_display_tags

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
    take_over: bool = False,
) -> Session:
    """Initialize store, client, and load experiment data.

    All evaluation goes through :class:`BackendClient` against a running
    TermNorm-compatible backend.
    """
    from promptpotter.config.logging import setup_logging

    setup_logging()

    # campaign/setup.py → ui → presentation → promptpotter → repo_root
    project_root = Path(__file__).resolve().parent.parent.parent.parent.parent

    session = await _init_services(
        backend_url=backend_url,
        backend_id=backend_id,
        experiment_id=experiment_id,
        project_root=project_root,
        dataset_name=dataset_name,
        on_status=print,
        take_over=take_over,
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
    session: Session,
    train_data: list | None,
    campaign_config: CampaignConfig | None = None,
    pipeline_params: dict | None = None,
    listener: Any | None = None,
) -> tuple[OptSearchPoint, list, list, list]:
    """Load baseline prompt, set dataset, run baseline on full dataset.

    Delegates to shared orchestration with notebook display.  A tqdm
    progress bar + per-query print wraps the caller's ``listener`` so
    baseline scoring is visible in the notebook without losing the
    dashboard updates the caller's emitter drives on top.
    """
    from tqdm.auto import tqdm

    from promptpotter.application.campaign.data import (
        prepare_scoring_context as _svc_prepare,
    )
    from promptpotter.infrastructure.tracing import ObservabilityBridge
    from promptpotter.presentation.views.query_format import _fmt_query_result

    _raw_scoring = campaign_config.scoring if campaign_config else None
    scoring_formula: str | None = (
        _raw_scoring.get("per_query") if isinstance(_raw_scoring, dict) else _raw_scoring
    )
    pbar: Any = None

    class _NotebookBaselineListener:
        """Wraps the caller's listener and adds tqdm for the baseline phase."""

        def __init__(self, inner: Any | None) -> None:
            self._inner = inner

        def on_phase(self, event: Any) -> None:
            if self._inner is not None:
                self._inner.on_phase(event)

        def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
            nonlocal pbar
            if pbar is None:
                pbar = tqdm(total=qt or 1, desc="Baseline eval", unit="query")
            else:
                pbar.total = qt
            if self._inner is not None:
                self._inner.on_sample_started(ci, ct, qi, qt, query_text)

        def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
            is_cached = result.get("cached", False)
            tqdm.write(_fmt_query_result(result, cached=is_cached, scoring_formula=scoring_formula))
            if pbar is not None:
                pbar.update(1)
            if self._inner is not None:
                self._inner.on_sample_scored(ci, ct, qi, qt, result)

        def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
            if self._inner is not None:
                self._inner.on_candidate_scored(idx, total, scores)

        def on_round_complete(self, rr: Any, l1_stall_count: int) -> None:
            if self._inner is not None:
                self._inner.on_round_complete(rr, l1_stall_count)

    _obs = ObservabilityBridge.file_only(session.store.base_dir, session.backend_id)

    try:
        baseline, dataset, campaign_rounds, baseline_results = await _svc_prepare(
            session.experiment_extract,
            train_data,
            campaign_config,
            pipeline_params=pipeline_params,
            pipeline_schema=session.pipeline_schema,
            svc=session,
            listener=_NotebookBaselineListener(listener),
            obs=_obs,
        )
    finally:
        if pbar is not None:
            pbar.close()

    print(f"\nEvaluation data: {len(dataset)} queries")
    return baseline, dataset, campaign_rounds, baseline_results


def prepare_datasets(
    store: Stores,
    excel_path: str | Path | None = None,
    *,
    force: bool = False,
) -> tuple[list | None, list[str]]:
    """Load/create datasets, build session terms, and display summary.

    Thin display wrapper around
    ``promptpotter.application.campaign.data.prepare_datasets()``.
    """
    from promptpotter.application.campaign.data import (
        prepare_datasets as _prepare_datasets,
    )

    if excel_path:
        print(f"Loading ground truth from {Path(excel_path).name} ...")

    result = _prepare_datasets(store, excel_path, force=force)

    print(f"\n{'=' * 50}")
    print(f"  Train              : {len(result.splits.get('train', []))} queries")
    print(f"  Test (processes)   : {len(result.splits.get('test_processes', []))} queries")
    print(f"  Test (material)    : {len(result.splits.get('test_material', []))} queries")
    print(f"  {'-' * 48}")
    print(f"  Combined queries   : {result.n_unique_queries} (deduplicated)")
    print(f"  Session identifiers: {len(result.index_terms)} unique targets")
    print(f"{'=' * 50}")

    return result.train_data, result.index_terms
