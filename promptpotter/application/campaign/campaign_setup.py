"""Campaign initialization and scan orchestration.

Sets up project store, backend client, and loads campaign data.
Prefers dataset loading via DatasetStore; falls back to experiment
sync from backend when no dataset_name is provided.

Scan orchestration wires the sensitivity scanner to campaign state
and persistence.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.tenant import TenantContext
from promptpotter.infrastructure.backend.client import BackendClient
from promptpotter.infrastructure.store import Stores, build_stores

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.recon.recon_report import ReconBrief
    from promptpotter.domain.pipeline_schema import PipelineSchema


logger = logging.getLogger(__name__)

__all__ = [
    "SessionEnv",
    "init_services",
    "load_baseline_prompt",
    "load_recon_brief",
    "resolve_campaign_id",
    "run_recon_and_persist",
]


@dataclass
class SessionEnv:
    """Return value from ``init_services()``."""

    store: Stores
    backend_id: str
    experiment_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    synced: bool
    queries: list[dict] = field(default_factory=list)
    experiment_extract: dict = field(default_factory=dict)
    index_terms: list[str] = field(default_factory=list)
    tenant: TenantContext | None = None
    dataset_name: str | None = None


def load_baseline_prompt(
    experiment_extract: dict,
    prompt_node_names: list[str] | None = None,
    dataset_name: str | None = None,
) -> OptSearchPoint:
    """Load the baseline OptSearchPoint for the optimizer.

    Resolution order:
      1. ``experiment_extract.dependencies.prompts`` — legacy registry path
         used when synced from a live backend's experiment extract.
      2. ``datasets/{dataset_name}/prompts/{node}.json`` (or ``default.json``)
         — canonical per-dataset prompt store. This is the path all
         dataset-first campaigns (bbeh/gsm8k/aime_2025/lca-termnorm) take.
      3. Empty OptSearchPoint — param-only optimization.

    Without the canonical fallback, the pipeline_params would get the
    right starting prompt (via ``configure_pipeline``) while the optimizer
    itself would start from an empty ``OptSearchPoint`` — causing L1 to
    generate variants from nothing and silently overwrite the pipeline's
    prompt with hallucinated replacements.
    """
    dependencies = experiment_extract.get("dependencies", {})
    prompts = dependencies.get("prompts", {})
    names = prompt_node_names or []

    matched_prompt = None
    matched_key = None
    for node_name in names:
        for key, prompt_info in prompts.items():
            if node_name in key:
                matched_prompt = prompt_info
                matched_key = key
                break
        if matched_prompt:
            break

    # Fallback 1: no node names provided but prompts exist — use the first one
    if matched_prompt is None and not names and prompts:
        matched_key, matched_prompt = next(iter(prompts.items()))

    if matched_prompt is not None:
        label = names[0] if names else matched_key
        return OptSearchPoint(
            instruction=matched_prompt["template"],
            changes_description=f"Baseline prompt from {label} registry",
        )

    # Fallback 2: canonical per-dataset prompt store
    if dataset_name and names:
        from promptpotter.application.datasets.prompt_store import (
            has_dataset_prompts,
            load_node_prompt,
        )

        if has_dataset_prompts(dataset_name):
            for node_name in names:
                try:
                    template = load_node_prompt(dataset_name, node_name, "default")
                except FileNotFoundError:
                    continue
                logger.info(
                    "Baseline loaded from canonical store: datasets/%s/prompts/ → %s",
                    dataset_name,
                    node_name,
                )
                return OptSearchPoint.from_prompt_fields(
                    template.prompt_field_dict(),
                    changes_description=(
                        f"Baseline from datasets/{dataset_name}/prompts/ ({node_name})"
                    ),
                )

    logger.info(
        "No prompt found for nodes %s — baseline uses empty prompt (param-only optimization)",
        names,
    )
    return OptSearchPoint(
        instruction="",
        changes_description="Baseline (no prompt node active — param-only optimization)",
    )


def _load_static_pipeline_schema(
    project_root: Path, dataset_name: str | None
) -> PipelineSchema | None:
    """Load PipelineSchema from a static ``datasets/{name}/pipeline.json``."""
    if not dataset_name:
        return None
    import json

    cfg_path = project_root / "datasets" / dataset_name / "pipeline.json"
    if not cfg_path.exists():
        logger.info("No static pipeline.json at %s", cfg_path)
        return None

    from promptpotter.application.pipeline_discovery import parse_pipeline_response

    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    try:
        schema = parse_pipeline_response(data)
        logger.info("Static pipeline schema loaded: %s v%s", schema.name, schema.version)
        return schema
    except Exception as exc:
        logger.warning("Failed to parse static pipeline.json: %s", exc)
        return None


async def init_services(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = "",
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    project_root: Path | None = None,
    dataset_name: str | None = None,
    on_status: Callable[[str], None] | None = None,
    take_over: bool = False,
) -> SessionEnv:
    """Initialize store, client, pipeline schema, and load eval data.

    All evaluation goes through :class:`BackendClient` against a running
    TermNorm-compatible backend. BBEH / GSM8K / AIME use the same
    ``/matches`` endpoint with a ``steps: ["llm_only"]`` pipeline; there is
    no local evaluation path.

    Refuses to run if the active-session pointer
    (``.promptpotter/active_session.json``) names a different ``backend_id``
    unless ``take_over=True`` is passed. CLI ``init`` passes ``take_over=True``;
    other surfaces (notebook, smoke tool) inherit the guardrail by default.
    """
    from promptpotter.shared.errors import ActiveSessionMismatchError

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not backend_id:
        backend_id = dataset_name or DEFAULT_BACKEND_ID

    if project_root is None:
        # campaign/campaign_setup.py → services → promptpotter → repo_root
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    store = build_stores(project_root / ".promptpotter" / "projects")

    # Guardrail: refuse to drift to a different project silently.
    active_bid, active_sid = store.sessions.read_active_pointer()
    if active_bid and active_bid != backend_id:
        if not take_over:
            raise ActiveSessionMismatchError(
                active_backend_id=active_bid,
                active_session_id=active_sid,
                requested_backend_id=backend_id,
            )
        # Take-over: clear the pointer. The smoke tool / notebook is sessionless
        # by design (M9 gap), so writing {backend_id, ""} would be a lie that
        # downstream `SessionStore.load_active()` turns into an ugly "session not
        # found" error. Clearing leaves the workspace in "no active session"
        # state, which is what a CLI `init` then correctly recovers from.
        store.sessions.clear_active_pointer()
        _status(f"Took over active session: cleared pointer (was {active_bid!r})")

    pipeline_schema = _load_static_pipeline_schema(project_root, dataset_name)
    if pipeline_schema:
        _status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")

    client = BackendClient(backend_url)
    _status(f"Backend: {backend_url}")

    # Fetch pipeline schema from backend if no static config
    if not pipeline_schema:
        try:
            from promptpotter.application.pipeline_discovery import parse_pipeline_response

            pipeline_resp = await client.fetch_pipeline()
            pipeline_schema = parse_pipeline_response(pipeline_resp)
            logger.info(
                "Pipeline schema loaded: %s v%s",
                pipeline_schema.name,
                pipeline_schema.version,
            )
            _status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.info("Could not fetch pipeline schema: %s", exc)
            _status("Pipeline: unavailable")

    # Register backend connection
    if not store.backends.get(backend_id):
        backend_name = pipeline_schema.name if pipeline_schema else "Unknown"
        backend_type = "backend" if pipeline_schema else "unknown"

        store.backends.register(
            BackendConnection(
                id=backend_id,
                name=backend_name,
                backend_type=backend_type,
                base_url=backend_url,
            )
        )

    base = SessionEnv(
        store=store,
        backend_id=backend_id,
        experiment_id=experiment_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        synced=False,
        dataset_name=dataset_name,
    )

    # --- Dataset store path (preferred when available) ---
    if dataset_name:
        ds = store.backends.load_dataset(backend_id, dataset_name)

        # Auto-load from DATASET_LOADERS registry when store is empty
        if not (ds and ds.get("items")):
            from promptpotter.application.datasets.builder import DATASET_LOADERS

            if dataset_name in DATASET_LOADERS:
                _status(f"Loading dataset '{dataset_name}' from registry ...")
                loader_items = DATASET_LOADERS[dataset_name]()
                store.backends.save_dataset(backend_id, dataset_name, loader_items)
                ds = {"items": loader_items}
                logger.info("Auto-loaded dataset %r: %d items", dataset_name, len(loader_items))

        if ds and ds.get("items"):
            items = ds["items"]
            index_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
            logger.info(
                "Loaded dataset %r from store: %d items, %d session terms",
                dataset_name,
                len(items),
                len(index_terms),
            )
            _status(f"Dataset: {dataset_name} ({len(items)} queries)")
            base.queries = _dataset_items_to_queries(items)
            base.index_terms = index_terms
            return base
        _status(f"Dataset '{dataset_name}' not available")
        raise ValueError(
            f"Dataset {dataset_name!r} not found in DatasetStore or DATASET_LOADERS. "
            f"Add a loader to DATASET_LOADERS in dataset_builder.py."
        )

    # --- Experiment sync path (when no dataset_name — via experiment traces) ---
    experiment_extract = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Detect stale sync data: data exists but has no traces
    _has_traces = bool(
        experiment_extract
        and experiment_extract.get("runs")
        and experiment_extract["runs"][0].get("traces")
    )

    synced = False
    if not experiment_extract or not _has_traces:
        reason = (
            "No stored experiment data" if not experiment_extract else "Stored data has no traces"
        )
        logger.info("%s — syncing from %s ...", reason, backend_url)
        _status(f"Syncing experiment {experiment_id} ...")
        try:
            experiment_extract = await client.sync_experiment(
                store,
                backend_id,
                experiment_id,
                include_traces=True,
            )
            synced = True
            _status("Sync complete")
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)
            _status(f"Sync failed: {exc}")

    base.synced = synced

    if not experiment_extract:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced or datasets are loaded."
        )
        _status("WARNING: No experiment data available")
        return base

    from promptpotter.config.extractors import EXPERIMENT_EXTRACTORS

    schema_key = pipeline_schema.name.lower() if pipeline_schema else ""
    extractor = EXPERIMENT_EXTRACTORS.get(schema_key)
    if extractor:
        queries, index_terms = extractor(experiment_extract)
    else:
        # Generic fallback: extract from evaluation_results
        queries, index_terms = _generic_extract_experiment(experiment_extract)
    exp_name = experiment_extract.get("experiment", {}).get("name", experiment_id)
    _status(f"Experiment: {exp_name} ({len(queries)} queries, {len(index_terms)} session terms)")

    base.queries = queries
    base.experiment_extract = experiment_extract
    base.index_terms = index_terms
    return base


def _generic_extract_experiment(experiment_extract: dict) -> tuple[list[dict], list[str]]:
    """Generic fallback: extract queries from evaluation_results."""
    runs = experiment_extract.get("runs", [])
    if not runs:
        return [], []
    queries: list[dict] = []
    gt_set: set[str] = set()
    for er in runs[0].get("evaluation_results", []):
        q = er.get("query", "")
        gt = er.get("ground_truth", "")
        if q and gt:
            queries.append({"query": q, "ground_truth": gt})
            gt_set.add(gt)
    return queries, sorted(gt_set)


def _dataset_items_to_queries(items: list[dict]) -> list[dict]:
    """Convert DatasetStore items to the query format used by replay/eval.

    Passes through all fields from each item — any connector-specific
    enrichment (e.g. bom_material, query_fields) is expected to already
    be present from dataset load time.
    """
    return [item for item in items if item.get("query") and item.get("ground_truth")]


def resolve_campaign_id(
    store: Stores,
    backend_id: str,
    short_id: str,
) -> str | None:
    """Resolve short prefix/suffix to full campaign_id."""
    campaigns = store.campaigns.list_all(backend_id)
    matches = [c for c in campaigns if short_id in c["campaign_id"]]
    if len(matches) == 1:
        return matches[0]["campaign_id"]
    if len(matches) > 1:
        logger.warning(
            "Ambiguous ID '%s' — %d matches: %s",
            short_id,
            len(matches),
            [m["campaign_id"] for m in matches],
        )
        return None
    logger.warning("No campaign matching '%s'", short_id)
    return None


# ---------------------------------------------------------------------------
# Scan orchestration — run sensitivity scan, persist, load scan context
# ---------------------------------------------------------------------------


def load_recon_brief(
    session: SessionEnv,
    session_id: str,
    recon_variants: dict,
    baseline_acc: float,
) -> ReconBrief | None:
    """Reconstruct scan context from persisted scan results.

    Shared by CLI and notebook — avoids inlining DataFrame construction
    in each entry point.
    """
    recon_data = session.store.sessions.load_recon_results(
        session.backend_id,
        session_id,
    )
    if not recon_data:
        return None
    import pandas as pd

    from promptpotter.application.recon.recon_report import prepare_recon_brief

    recon_df = pd.DataFrame(recon_data["recon_df"])
    axis_profiles = recon_data["axis_profiles"]
    return prepare_recon_brief(recon_df, axis_profiles, recon_variants, baseline_acc)


async def run_recon_and_persist(
    baseline,
    campaign_config: CampaignConfig,
    recon_variants: dict,
    dataset: list,
    *,
    session: SessionEnv,
    recon_sample_size: int = 0,
    experiment_id: str = "",
    session_id: str = "",
    log: Callable[[str], None] = logger.info,
    progress_cb: Callable | None = None,
    on_result: Callable | None = None,
):
    """Decompose scan baseline, run sensitivity scan, persist results.

    Mints a session when called with ``session_id=""`` so non-CLI entry
    points (notebook, future API) produce the same on-disk artifacts as
    the CLI ``recon`` command. The CLI path passes a concrete id and is
    unchanged.

    Returns ``(recon_baseline_sp, baseline_opt, df, profiles)``.
    """
    from promptpotter.application.campaign.config import (
        configure_and_apply_pipeline,
        create_llm_client,
    )
    from promptpotter.application.campaign.session_state import auto_mint_session
    from promptpotter.application.recon.recon_report import (
        decompose_recon_baseline as _decompose_scan_baseline,
    )
    from promptpotter.application.recon.recon_runner import (
        run_recon as _run_recon,
    )

    if not session_id:
        session_id = auto_mint_session(
            session,
            campaign_config,
            dataset_size=len(dataset),
            experiment_id=experiment_id or None,
        )

    # Configure pipeline (ensures filtered schema is applied with overrides baked in)
    pipeline_params = configure_and_apply_pipeline(session, campaign_config, log=log)

    ps = session.pipeline_schema

    # Decompose scan baseline
    llm_client, llm_model = create_llm_client(campaign_config)
    result = await _decompose_scan_baseline(
        baseline,
        campaign_config,
        llm_client,
        llm_model,
        pipeline_params=pipeline_params,
        session=session,
        recon_variants=recon_variants,
        pipeline_schema=ps,
    )
    recon_baseline_sp = result.baseline_jsp
    baseline_opt = result.search_baseline

    # Init backend session
    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)

    # Run scan
    log(f"Running sensitivity scan ({len(recon_variants)} axes) ...")
    scan_kwargs: dict[str, Any] = {
        "sample_size": recon_sample_size,
        "pipeline_schema": ps,
        "experiment_id": experiment_id,
        "scoring_formula": campaign_config.get("scoring"),
    }
    if progress_cb is not None:
        scan_kwargs["progress_cb"] = progress_cb
    if on_result is not None:
        scan_kwargs["on_result"] = on_result

    df, profiles = await _run_recon(
        recon_baseline_sp,
        recon_variants,
        dataset,
        session,
        baseline_opt=baseline_opt,
        **scan_kwargs,
    )

    if df is None or (hasattr(df, "empty") and df.empty):
        log("Scan returned no results")
        return recon_baseline_sp, baseline_opt, None, []

    log(f"Sensitivity scan complete: {len(df)} variants evaluated")

    # Failure group sensitivity — cross-tabulate scan results with failure groups
    if session.store and session.backend_id:
        from promptpotter.application.intelligence.search_memory import SearchMemory
        from promptpotter.application.recon.failure_groups import (
            failure_group_sensitivity,
        )

        _sm_path = Path(session.store.base_dir) / session.backend_id / "search_memory.json"
        _sm = SearchMemory.load(_sm_path)
        _sm.refresh(session.store, session.backend_id)
        clusters = _sm.failure_clusters()
        if clusters:
            scan_rows = df.to_dict(orient="records")
            fg_result = failure_group_sensitivity(scan_rows, clusters)
            if fg_result.sensitivities:
                _sm.ingest_failure_groups(fg_result)
                _sm.save(_sm_path)
                log(
                    f"Failure group analysis: {len(fg_result.sensitivities)} "
                    f"axis x group correlations ingested into SearchMemory"
                )

    # Persist results
    if session_id and session.store and session.backend_id:
        session.store.sessions.save_recon_results(
            session.backend_id,
            session_id,
            df.to_dict(orient="records"),
            profiles,
        )
        log(f"Scan results persisted to session {session_id}")

    return recon_baseline_sp, baseline_opt, df, profiles
