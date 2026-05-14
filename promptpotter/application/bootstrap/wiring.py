"""Stores + LLMClient + connector resolution → ``Session``.

``init_services`` is the entry point: opens stores under the tenant root,
applies the tenant-pointer guard, resolves the connector, builds the
``BackendClient``, fetches the pipeline schema, registers the backend,
constructs the ``Session``, and either loads the dataset or auto-syncs an
experiment extract from the backend.

Identity + scoring lifecycle live next door (:mod:`session`,
:mod:`scoring_context`).
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from pathlib import Path

from promptpotter import connectors
from promptpotter.application.bootstrap.session import Session, TenantContext
from promptpotter.application.datasets import (
    DATASET_LOADERS,
    samples_from_dicts,
)
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
    settings,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store import (
    build_stores,
    clear_active_pointer,
    read_active_pointer,
)
from promptpotter.infrastructure.store.base import read_json_optional
from promptpotter.shared.errors import ActiveSessionMismatchError

logger = logging.getLogger(__name__)


def _apply_tenant_guard(tenant_id: str, take_over: bool, status: Callable[[str], None]) -> None:
    """Refuse tenant drift unless take_over=True; on take-over, clear the pointer."""
    active_tid, active_sid, _ = read_active_pointer()
    if not (active_tid and active_tid != tenant_id):
        return
    if not take_over:
        raise ActiveSessionMismatchError(
            active_tenant_id=active_tid,
            active_session_id=active_sid,
            requested_tenant_id=tenant_id,
        )
    clear_active_pointer()
    status(f"Took over active session: cleared pointer (was tenant {active_tid!r})")


def _apply_dataset_overlay(backend_resp: dict, local_raw: dict) -> dict:
    """Merge dataset pipeline.json overlay onto the backend response.

    Dataset overlay can carry: ``pipelines.default`` (which subset of nodes
    is active for this dataset), per-node config deltas, and metadata like
    ``available_models`` / ``prompt_meta`` / ``optimizer.param_keys``. The
    backend stays SoT for runtime defaults; the overlay layers operator
    intent on top.
    """
    out = copy.deepcopy(backend_resp.get("data") or backend_resp)
    if "pipelines" in local_raw:
        out["pipelines"] = local_raw["pipelines"]
    for node_name, node_def in (local_raw.get("nodes") or {}).items():
        if not isinstance(node_def, dict):
            continue
        out.setdefault("nodes", {}).setdefault(node_name, {})
        for k, v in node_def.items():
            if k == "config" and isinstance(v, dict):
                out["nodes"][node_name].setdefault("config", {}).update(v)
            else:
                out["nodes"][node_name][k] = v
    return out


async def _resolve_pipeline_schema(
    client: BackendClient,
    project_root: Path,
    dataset_name: str | None,
    status: Callable[[str], None],
) -> PipelineSchema | None:
    """Backend ``GET /pipeline`` is authoritative for runtime defaults.

    Local ``datasets/{name}/pipeline.json`` is the operator overlay:
    ``pipelines.default`` selects the active node subset, ``nodes.X.config``
    carries per-key deltas, plus per-dataset metadata. The two are merged
    here before parsing — backend underneath, dataset on top. Backend
    unreachable → fall back to the local file alone (offline mode).
    """
    backend_resp: dict | None = None
    try:
        backend_resp = await client.fetch_pipeline()
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.info("Could not fetch pipeline schema from backend: %s", exc)

    local_raw: dict | None = None
    if dataset_name:
        local_raw = read_json_optional(project_root / "datasets" / dataset_name / "pipeline.json")

    if backend_resp:
        merged = _apply_dataset_overlay(backend_resp, local_raw or {})
        try:
            schema = parse_pipeline_response(merged)
            status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes)")
            return schema
        except Exception as exc:
            logger.warning("Failed to parse merged pipeline schema: %s", exc)

    if local_raw is not None:
        try:
            schema = parse_pipeline_response(local_raw)
            status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes, offline)")
            return schema
        except Exception as exc:
            logger.warning("Failed to parse offline pipeline.json: %s", exc)

    status("Pipeline: unavailable")
    return None


def _read_backend_type(project_root: Path, dataset_name: str | None) -> str:
    """Resolve backend_type from datasets/{name}/pipeline.json. Required field."""
    if not dataset_name:
        raise ValueError("dataset_name required to resolve backend_type for connector lookup")
    raw = read_json_optional(project_root / "datasets" / dataset_name / "pipeline.json")
    bt = (raw or {}).get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise ValueError(f"backend_type missing or empty in datasets/{dataset_name}/pipeline.json")
    return bt.lower()


def _load_dataset_into_session(
    session: Session, dataset_name: str, status: Callable[[str], None]
) -> None:
    """Populate session.samples + index_terms from DatasetStore or DATASET_LOADERS."""
    ds = session.store.backends.load_dataset(dataset_name)
    if not (ds and ds.get("items")) and dataset_name in DATASET_LOADERS:
        status(f"Loading dataset '{dataset_name}' from registry ...")
        loader_items = DATASET_LOADERS[dataset_name]()
        session.store.backends.save_dataset(dataset_name, loader_items)
        ds = {"items": loader_items}

    if not (ds and ds.get("items")):
        status(f"Dataset '{dataset_name}' not available")
        raise ValueError(
            f"Dataset {dataset_name!r} not found in DatasetStore or DATASET_LOADERS. "
            f"Add a loader to DATASET_LOADERS in dataset_builder.py."
        )

    items = ds["items"]
    valid = [item for item in items if item.get("query") and item.get("ground_truth")]
    session.samples = samples_from_dicts(valid)
    session.index_terms = sorted({r["ground_truth"] for r in items if r.get("ground_truth")})
    status(f"Dataset: {dataset_name} ({len(items)} queries)")


async def _sync_and_extract_experiment(
    session: Session,
    backend_url: str,
    experiment_id: str,
    status: Callable[[str], None],
) -> None:
    """Populate queries/index_terms/experiment_extract; auto-sync from backend if missing."""
    backend_id = session.backend_id
    extract = session.store.backends.load_sync(backend_id, f"experiments/{experiment_id}.json")
    has_traces = bool(extract and extract.get("runs") and extract["runs"][0].get("traces"))

    if not extract or not has_traces:
        reason = "No stored experiment data" if not extract else "Stored data has no traces"
        logger.info("%s — syncing from %s ...", reason, backend_url)
        status(f"Syncing experiment {experiment_id} ...")
        try:
            extract = await session.backend_client.sync_experiment(
                session.store, backend_id, experiment_id, include_traces=True
            )
            session.backend_index_synced = True
            status("Sync complete")
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.warning("Auto-sync failed: %s", exc)
            status(f"Sync failed: {exc}")

    if not extract:
        logger.warning(
            "No experiment data available. "
            "Downstream calls will fail until data is synced or datasets are loaded."
        )
        status("WARNING: No experiment data available")
        return

    schema_key = session.pipeline_schema.name.lower() if session.pipeline_schema else ""
    try:
        connector = connectors.get(schema_key) if schema_key else None
    except KeyError:
        connector = None
    if connector is not None:
        queries, index_terms = connector.extract_experiment(extract)
    else:
        runs = extract.get("runs", [])
        queries = []
        gt_set: set[str] = set()
        for er in runs[0].get("evaluation_results", []) if runs else []:
            q, gt = er.get("query", ""), er.get("ground_truth", "")
            if q and gt:
                queries.append({"query": q, "ground_truth": gt})
                gt_set.add(gt)
        index_terms = sorted(gt_set)

    exp_name = extract.get("experiment", {}).get("name", experiment_id)
    status(f"Experiment: {exp_name} ({len(queries)} queries, {len(index_terms)} session terms)")

    session.samples = samples_from_dicts(queries)
    session.experiment_extract = extract
    session.index_terms = index_terms


async def init_services(
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = "",
    experiment_id: str = DEFAULT_EXPERIMENT_ID,
    project_root: Path | None = None,
    dataset_name: str | None = None,
    on_status: Callable[[str], None] | None = None,
    take_over: bool = False,
    tenant_id: str = "default",
) -> Session:
    """Init store, client, pipeline schema, scoring data. Refuses tenant drift unless ``take_over=True``."""

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if not backend_id:
        backend_id = dataset_name or DEFAULT_BACKEND_ID
    if project_root is None:
        # bootstrap/wiring.py → bootstrap → application → promptpotter → repo_root
        project_root = Path(__file__).resolve().parent.parent.parent.parent

    store = build_stores(project_root / ".promptpotter" / "projects", tenant_id=tenant_id)
    _apply_tenant_guard(tenant_id, take_over, status)

    backend_type = _read_backend_type(project_root, dataset_name)
    connector = connectors.get(backend_type)
    client = BackendClient(
        backend_url,
        wire_adapter=connector.wire_adapter,
        session=connector.session_factory(),
        auth_token=settings.TERMNORM_TOKEN or None,
    )
    status(f"Backend: {backend_url}")

    pipeline_schema = await _resolve_pipeline_schema(client, project_root, dataset_name, status)

    if not store.backends.get(backend_id):
        store.backends.register(
            BackendConnection(
                id=backend_id,
                name=pipeline_schema.name if pipeline_schema else "Unknown",
                backend_type=backend_type,
                base_url=backend_url,
            )
        )

    from promptpotter.infrastructure.tracing import LangfuseLogger

    session = Session(
        store=store,
        backend_id=backend_id,
        experiment_id=experiment_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        backend_index_synced=False,
        dataset_name=dataset_name,
        tenant=TenantContext(tenant_id=tenant_id),
        project_root=str(store.base_dir),
        langfuse=LangfuseLogger(),
    )

    if dataset_name:
        _load_dataset_into_session(session, dataset_name, status)
    else:
        await _sync_and_extract_experiment(session, backend_url, experiment_id, status)
    return session
