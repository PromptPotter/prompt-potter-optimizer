"""Campaign initialization service.

Sets up project store, backend client, and loads campaign data.
Prefers dataset loading via DatasetStore; falls back to experiment
sync from backend when no dataset_name is provided.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from promptpotter.config.settings import (
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.models.backend import BackendConnection
from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.backend_client import BackendClient
from promptpotter.services.campaign.config import CampaignConfig
from promptpotter.services.project_store import ProjectStore
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema


logger = logging.getLogger(__name__)

__all__ = [
    "SessionEnv",
    "apply_stored_overrides",
    "diff_campaign_config",
    "init_services",
    "load_baseline_prompt",
    "load_stored_campaign_config",
    "resolve_campaign_id",
    "save_campaign_winner",
]


@dataclass
class SessionEnv:
    """Return value from ``init_services()``."""

    store: ProjectStore
    backend_id: str
    experiment_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    synced: bool
    queries: list[dict] = field(default_factory=list)
    exp_data: dict = field(default_factory=dict)
    index_terms: list[str] = field(default_factory=list)


def load_baseline_prompt(
    exp_data: dict,
    prompt_node_names: list[str] | None = None,
) -> OptSearchPoint:
    """Extract baseline prompt from experiment data's prompt registry."""
    dependencies = exp_data.get("dependencies", {})
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

    # Fallback: no node names provided but prompts exist — use the first one
    if matched_prompt is None and not names and prompts:
        matched_key, matched_prompt = next(iter(prompts.items()))

    if matched_prompt is None:
        logger.info(
            "No prompt found for nodes %s — baseline uses empty prompt (param-only optimization)",
            names,
        )
        return OptSearchPoint(
            instruction="",
            changes_description="Baseline (no prompt node active — param-only optimization)",
        )

    label = names[0] if names else matched_key
    return OptSearchPoint(
        instruction=matched_prompt["template"],
        changes_description=f"Baseline prompt from {label} registry",
    )


def _validate_local_access(token: str | None) -> bool:
    """Check if the caller is authorized for local LLM evaluation.

    Uses ``hmac.compare_digest`` for constant-time comparison.
    Returns ``True`` if authorized, ``False`` otherwise (caller falls
    back to backend-routed evaluation).
    """
    from promptpotter.config.settings import settings

    secret = settings.LOCAL_EVAL_SECRET
    if not secret:
        logger.info("Local eval not enabled (LOCAL_EVAL_SECRET empty) — routing to backend")
        return False
    if not token:
        logger.info("No local_eval_token provided — routing to backend")
        return False
    if not hmac.compare_digest(token, secret):
        logger.warning("Invalid local_eval_token — routing to backend")
        return False
    return True


def _create_llm_only_client(project_root: Path, dataset_name: str | None) -> Any:
    """Build an :class:`LLMOnlyAdapter` from the dataset's pipeline.json config."""
    import json

    from promptpotter.services.llm_backend_adapter import LLMOnlyAdapter

    config: dict[str, Any] = {}
    if dataset_name:
        cfg_path = project_root / "datasets" / dataset_name / "pipeline.json"
        if cfg_path.exists():
            config = json.loads(cfg_path.read_text(encoding="utf-8"))

    # Extract LLM defaults from pipeline config
    llm_defaults = config.get("llm_defaults", {})
    node_config: dict[str, Any] = {}
    nodes = config.get("nodes", {})
    if nodes:
        first_node = next(iter(nodes.values()))
        node_config = first_node.get("config", {})

    from promptpotter.services.campaign.config import create_llm_client

    llm_client, _ = create_llm_client(
        {
            "optimizer_llm": {
                "provider": llm_defaults.get("provider", "groq"),
                "model": node_config.get("model", llm_defaults.get("model", "")),
            }
        }
    )

    return LLMOnlyAdapter(
        llm_client,
        model=node_config.get("model", llm_defaults.get("model")),
        temperature=node_config.get("temperature", 0.0),
        max_tokens=node_config.get("max_tokens", 1024),
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

    from promptpotter.services.pipeline_discovery import parse_pipeline_response

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
    dataset_type: str | None = None,
    local_eval_token: str | None = None,
    on_status: Callable[[str], None] | None = None,
) -> SessionEnv:
    """Initialize store, client, pipeline schema, and load eval data.

    All evaluation goes through :class:`BackendClient` by default.
    When ``dataset_type="llm-only"``, uses :class:`LLMOnlyAdapter` instead —
    gated behind ``LOCAL_EVAL_SECRET`` + ``local_eval_token`` for multi-tenant
    safety.
    """

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if project_root is None:
        # campaign/campaign_setup.py → services → promptpotter → repo_root
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    store = ProjectStore(base_dir=project_root / ".promptpotter" / "projects")

    # Decide eval client: local LLM-only (if authorized) or backend (default)
    use_local = dataset_type == "llm-only" and _validate_local_access(local_eval_token)

    pipeline_schema = _load_static_pipeline_schema(project_root, dataset_name)
    if pipeline_schema:
        _status(f"Pipeline: {pipeline_schema.name} ({len(pipeline_schema.nodes)} nodes)")

    if use_local:
        client = _create_llm_only_client(project_root, dataset_name)
        _status("Backend: llm-only (authorized)")
    else:
        client = BackendClient(backend_url)
        _status(f"Backend: {backend_url}")

        # Fetch pipeline schema from backend if no static config
        if not pipeline_schema:
            try:
                from promptpotter.services.pipeline_discovery import parse_pipeline_response

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
        backend_type = dataset_type or ("backend" if pipeline_schema else "unknown")

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
    )

    # --- Dataset store path (preferred when available) ---
    if dataset_name:
        ds = store.backends.load_dataset(backend_id, dataset_name)

        # Auto-load from DATASET_LOADERS registry when store is empty
        if not (ds and ds.get("items")):
            from promptpotter.services.dataset_builder import DATASET_LOADERS

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
    exp_data = store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")

    # Detect stale sync data: data exists but has no traces
    _has_traces = bool(exp_data and exp_data.get("runs") and exp_data["runs"][0].get("traces"))

    synced = False
    if not exp_data or not _has_traces:
        reason = "No stored experiment data" if not exp_data else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        _status(f"Syncing experiment {experiment_id} ...")
        try:
            exp_data = await client.sync_experiment(
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

    if not exp_data:
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
        queries, index_terms = extractor(exp_data)
    else:
        # Generic fallback: extract from evaluation_results
        queries, index_terms = _generic_extract_experiment(exp_data)
    exp_name = exp_data.get("experiment", {}).get("name", experiment_id)
    _status(f"Experiment: {exp_name} ({len(queries)} queries, {len(index_terms)} session terms)")

    base.queries = queries
    base.exp_data = exp_data
    base.index_terms = index_terms
    return base


def _generic_extract_experiment(exp_data: dict) -> tuple[list[dict], list[str]]:
    """Generic fallback: extract queries from evaluation_results."""
    runs = exp_data.get("runs", [])
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
    store: ProjectStore,
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


def apply_stored_overrides(
    campaign_config: CampaignConfig,
    stored_cfg: dict,
) -> dict | None:
    """Merge stored experiment config into campaign_config (in-place).

    Returns updated pipeline_params if stored, else None.
    """
    _OVERRIDE_KEYS: dict[str, tuple[str, ...]] = {
        "l1_patience": ("optimization",),
        "max_rounds": ("optimization",),
        "n_variants": ("optimization",),
        "creativity": ("optimization",),
        "model": ("optimizer_llm",),
        "sp_budget_ttest": (),
    }
    for key, path in _OVERRIDE_KEYS.items():
        val = stored_cfg.get(key)
        if val is not None:
            target: dict[str, Any] = cast(dict[str, Any], campaign_config)
            for p in path:
                target = target.setdefault(p, {})
            target[key] = val

    stored_pp = stored_cfg.get("pipeline_params")
    if stored_pp:
        campaign_config["pipeline_params"] = stored_pp
        return stored_pp
    return None


def save_campaign_winner(
    campaign_rounds: list,
    campaign_config: CampaignConfig,
    store: ProjectStore,
    backend_id: str,
    *,
    campaign_id: str | None = None,
) -> dict:
    """Find best round, save to store + link to campaign. Returns save_data dict."""
    from datetime import UTC, datetime

    winner = campaign_rounds[-1]["prompt_fields"]
    winner_acc = campaign_rounds[-1]["accuracy"]

    for rd in campaign_rounds:
        if rd["accuracy"] > winner_acc:
            winner = rd["prompt_fields"]
            winner_acc = rd["accuracy"]

    baseline_acc = campaign_rounds[0]["accuracy"] if campaign_rounds else None
    save_data = {
        "winner": winner.model_dump(),
        "accuracy": winner_acc,
        "campaign_rounds": len(campaign_rounds),
        "baseline_accuracy": baseline_acc,
        "improvement": (winner_acc - baseline_acc) if baseline_acc is not None else None,
        "config": campaign_config,
        "saved_at": datetime.now(UTC).isoformat(),
    }

    filename = f"optimization/campaign_winner_{winner.id[:12]}.json"
    store.backends.save_sync(backend_id, filename, save_data)

    if campaign_id:
        full_id = resolve_campaign_id(store, backend_id, campaign_id)
        if full_id:
            with graceful("Campaign metadata update skipped", level=logging.DEBUG):
                store.campaigns.update(
                    backend_id,
                    full_id,
                    {
                        "winner_prompt_fields_id": winner.id,
                        "winner_accuracy": winner_acc,
                        "winner_filename": filename,
                    },
                )

    logger.info("Winner saved: %s (acc=%.1f%%)", filename, winner_acc * 100)
    return {
        **save_data,
        "winner_id": winner.id,
        "filename": filename,
        "backend_id": backend_id,
    }


def diff_campaign_config(
    stored_config: dict,
    campaign_config: CampaignConfig,
    pipeline_schema: PipelineSchema | None = None,
) -> dict[str, dict]:
    """Compute parameter differences between stored and current campaign config."""
    from promptpotter.services.campaign.config import LoopConfig

    current = LoopConfig.from_campaign_config(
        campaign_config,
        pipeline_schema=pipeline_schema,
    ).model_dump()

    keys = [
        "max_rounds",
        "l1_patience",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "model",
        "sp_budget_ttest",
        "seed",
    ]

    diffs: dict[str, dict] = {}
    for k in keys:
        sv = stored_config.get(k)
        cv = current.get(k)
        if sv != cv:
            diffs[k] = {"stored": sv, "current": cv}

    # Compare pipeline params (derived from schema)
    sp = stored_config.get("pipeline_params")
    cp = pipeline_schema.to_pipeline_params() if pipeline_schema else None
    if sp != cp:
        for pk in sorted(set(sp or {}) | set(cp or {})):
            sv = (sp or {}).get(pk)
            cv = (cp or {}).get(pk)
            if sv != cv:
                diffs[f"pp.{pk}"] = {"stored": sv, "current": cv}

    return diffs


def load_stored_campaign_config(
    store: ProjectStore,
    backend_id: str,
    experiment_id: str,
) -> dict | None:
    """Load stored experiment config for a campaign. Returns config dict or None."""
    full_id = resolve_campaign_id(store, backend_id, experiment_id)
    if not full_id:
        return None
    campaign = store.campaigns.load(backend_id, full_id)
    if not campaign:
        return None
    return campaign.get("config")
