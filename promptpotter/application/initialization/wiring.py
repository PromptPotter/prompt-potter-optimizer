"""Stores + LLMClient + connector resolution → ``Session`` — step 1 of run init. Identity and
the scoring lifecycle live in ``session`` + ``loop_start``."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.datasets.csv_ingest import read_candidate_library_file
from promptpotter.application.datasets.loaders import resolve_dataset_items, samples_from_dicts
from promptpotter.application.initialization.session import Session
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.pipeline_parsing import merge_node_blocks, parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.backend import BackendClient, build_backend_client
from promptpotter.infrastructure.store.archive_views import maintain_measurement_index
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.identity import IdentityContext, default_identity

logger = logging.getLogger(__name__)


def _apply_dataset_overlay(
    backend_resp: dict[str, Any], local_raw: dict[str, Any]
) -> dict[str, Any]:
    """Merge dataset ``pipeline.yaml`` overlay onto the backend response.
    Overlay carries ``pipelines.default`` / per-node config deltas / metadata; backend stays SoT for runtime defaults."""
    out = copy.deepcopy(backend_resp.get("data") or backend_resp)
    if "pipelines" in local_raw:
        out["pipelines"] = local_raw["pipelines"]
    out["nodes"] = merge_node_blocks(out.get("nodes") or {}, local_raw.get("nodes") or {})
    return out


async def _verify_connector_revision(
    client: BackendClient,
    connector: connectors.Connector,
) -> None:
    """WARN on drift between ``connector.expected_revision`` and the live backend's. Opt-in per
    connector; a network error says "could not verify", never "mismatch"."""
    expected = connector.expected_revision
    check = connector.version_check
    if not expected or check is None:
        return
    try:
        actual = await check(client.http, client.base_url)
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise
    except Exception as exc:
        logger.warning(
            "connector[%s]: could not verify backend revision (%s) — expected %s",
            connector.name,
            exc,
            expected,
        )
        return
    if actual is None:
        logger.warning(
            "connector[%s]: backend did not report a revision — expected %s",
            connector.name,
            expected,
        )
        return
    if actual != expected:
        logger.warning(
            "connector[%s]: backend revision drift — expected %s, got %s",
            connector.name,
            expected,
            actual,
        )


def _warn_if_no_terminal_ranker(schema: PipelineSchema, status: Callable[[str], None]) -> None:
    """Without a ranker/candidate_source node emitting a ranked list, every sample silently scores
    NO_RESULT — so it is surfaced loudly at setup, on the status line, not at score time."""
    if not schema.nodes:
        return
    if any(n.node_type in ("ranker", "candidate_source") and n.output_keys for n in schema.nodes):
        return
    msg = (
        f"Pipeline {schema.name!r} has no terminal ranker — no node emits a ranked list, "
        "so every sample will score NO_RESULT (check node_role on the final node)"
    )
    logger.warning(msg)
    status(f"⚠ {msg}")


async def _resolve_pipeline_schema(
    client: BackendClient,
    dataset_config_dir: Path | None,
    status: Callable[[str], None],
    *,
    in_process: bool = False,
) -> PipelineSchema:
    """Backend schema underneath, dataset overlay on top; an ``in_process`` connector has no backend, so the local file IS
    the schema. RAISES rather than returning ``None`` — optional at ~40 readers means a run completes with wrong numbers."""
    backend_resp: dict[str, Any] | None = None
    if in_process:
        pass  # no remote backend — local pipeline.yaml is authoritative
    else:
        try:
            backend_resp = await client.fetch_pipeline()
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.info("Could not fetch pipeline schema from backend: %s", exc)

    local_raw: dict[str, Any] | None = None
    if dataset_config_dir is not None:
        local_raw = read_yaml_optional(dataset_pipeline_path(dataset_config_dir))

    if backend_resp:
        merged = _apply_dataset_overlay(backend_resp, local_raw or {})
        try:
            schema = parse_pipeline_response(merged)
            _warn_if_no_terminal_ranker(schema, status)
            status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes)")
            return schema
        except Exception as exc:
            logger.warning("Failed to parse merged pipeline schema: %s", exc)

    if local_raw is not None:
        try:
            schema = parse_pipeline_response(local_raw)
            _warn_if_no_terminal_ranker(schema, status)
            status(f"Pipeline: {schema.name} ({len(schema.nodes)} nodes, offline)")
            return schema
        except Exception as exc:
            logger.warning("Failed to parse offline pipeline.yaml: %s", exc)

    status("Pipeline: unavailable")
    raise PayloadInvalidError(
        f"could not resolve a pipeline schema for {dataset_config_dir}. The backend "
        f"returned nothing usable and the dataset's own pipeline.yaml did not parse "
        f"(see the warnings above). Every measurement is keyed on this schema, so there "
        f"is no run without it — fix the file, or point --backend-url at a reachable backend.",
        code="pipeline_config_invalid",
        details={"dataset_config_dir": str(dataset_config_dir)},
    )


def _read_backend_type(dataset_config_dir: Path | None, dataset_name: str | None) -> str:
    """Resolve backend_type from the dataset's ``pipeline.yaml``. Typed for the same reason its
    sibling is: a bare ``ValueError`` becomes a 500 the webapp retries forever."""
    if not dataset_name or dataset_config_dir is None:
        raise PayloadInvalidError(
            "dataset_name required to resolve backend_type for connector lookup",
            code="pipeline_config_invalid",
        )
    raw = read_yaml_optional(dataset_pipeline_path(dataset_config_dir))
    bt = (raw or {}).get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise PayloadInvalidError(
            f"backend_type missing or empty in {dataset_config_dir}/pipeline.yaml",
            code="pipeline_config_invalid",
            details={"dataset_config_dir": str(dataset_config_dir)},
        )
    return bt.lower()


def backend_type_of_dataset(stores: Stores, dataset_name: str) -> str:
    """THE predicate for "which connector does this dataset use?", so no reader hand-maintains a list
    of dataset NAMES. Tolerant, unlike its strict init twin: a campaign outlives its dataset dir."""
    try:
        raw = read_yaml_optional(dataset_pipeline_path(readable_dataset_dir(stores, dataset_name)))
    except (OSError, ValueError, DatasetAccessError):
        return ""
    bt = (raw or {}).get("backend_type")
    return bt.lower() if isinstance(bt, str) else ""


def _load_dataset_into_session(
    session: Session,
    dataset_name: str,
    status: Callable[[str], None],
    *,
    connector: connectors.Connector | None = None,
) -> None:
    """Populate session.samples + index_terms — tenant Origin, then repo benchmark, then the
    loader's one-shot download. A connector declaring an ``experiment_file`` loads through that."""
    items = resolve_dataset_items(session.store, dataset_name, status=status)
    if not items and connector is not None and connector.experiment_file:
        _load_experiment_file_into_session(session, connector, status)
        return
    if not items:
        status(f"Dataset '{dataset_name}' not available")
        raise ValueError(
            f"Dataset {dataset_name!r} not found in tenant uploads, repo benchmarks, "
            f"or any registered loader. Add one to DATASET_LOADERS in "
            f"application/datasets/loaders.py."
        )

    valid = [item for item in items if item.get("query") and item.get("ground_truth")]
    session.samples = samples_from_dicts(valid)
    gt_terms = {r["ground_truth"] for r in items if r.get("ground_truth")}
    config_dir = readable_dataset_dir(session.store, dataset_name)
    # The candidate library is part of the per-pipeline origin; read it through the
    # one origin-file seam. Unioned with the ground-truth answers (never replacing
    # them) so every label stays rankable even when the library uses a different
    # surface form — the SimaPro Cut-off **S** labels vs the Cut-off **U** library
    # that share no verbatim string, where a plain swap would zero the score.
    library = read_candidate_library_file(config_dir)
    session.index_terms = sorted(gt_terms | set(library))
    if library:
        status(
            f"Candidate library: +{len(set(library) - gt_terms)} targets "
            f"(term index now {len(session.index_terms)})"
        )
    status(f"Dataset: {dataset_name} ({len(items)} samples)")


def _load_experiment_file_into_session(
    session: Session,
    connector: connectors.Connector,
    status: Callable[[str], None],
) -> None:
    """Load samples from the connector's on-disk experiment doc — the same ``extract_experiment``
    seam the sync path uses, reading the dataset dir instead of a backend."""
    config_dir = session.dataset_config_dir
    exp_path = (config_dir / connector.experiment_file) if config_dir else None
    data = read_yaml_optional(exp_path) if exp_path else None
    if not data:
        status(f"Experiment file '{connector.experiment_file}' missing or empty")
        raise ValueError(
            f"Connector {connector.name!r} expects {connector.experiment_file!r} in the "
            f"dataset config dir ({config_dir}), but it is missing or empty."
        )
    queries, index_terms = connector.extract_experiment(data)
    session.samples = samples_from_dicts(queries)
    session.index_terms = index_terms
    status(f"Experiment: {connector.experiment_file} ({len(queries)} tasks)")


async def init_services(
    dataset_name: str,
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = "",
    on_status: Callable[[str], None] | None = None,
    identity: IdentityContext | None = None,
    stores: Stores | None = None,
    enable_tracing: bool = True,
) -> Session:
    """``store`` injects a pre-built :class:`Stores` rather than resolving the user-data root: it is
    the ONE way to relocate the tree, and the L4 inner runner passes a sandboxed one."""

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    resolved_identity = identity if identity is not None else default_identity()

    if stores is None:
        stores = build_stores(resolved_identity, projects_root=DEFAULT_PROJECTS_ROOT)

    # Off-thread: this takes a CROSS-PROCESS lock on the tenant-global index, which no sandbox
    # isolates — held on the event loop it blocks every other cell in the group and every
    # heartbeat keeping them alive.
    await asyncio.to_thread(maintain_measurement_index, stores)

    dataset_config_dir = readable_dataset_dir(stores, dataset_name)
    backend_type = _read_backend_type(dataset_config_dir, dataset_name)
    connector = connectors.get(backend_type)
    client = build_backend_client(connector, backend_url)
    status(f"Backend: {backend_url}")

    pipeline_schema = await _resolve_pipeline_schema(
        client, dataset_config_dir, status, in_process=connector.execution == "in_process"
    )
    await _verify_connector_revision(client, connector)

    # One physical endpoint = one BackendConnection. With no explicit
    # --backend-id, REUSE an existing registration for this (base_url,
    # backend_type) instead of minting a fresh per-dataset backend — the old
    # `dataset_name` fallback spawned one "termnorm" row per dataset, polluting
    # the "Other backends" list. Fall back to DEFAULT_BACKEND_ID only when this
    # endpoint is genuinely new.
    if not backend_id:
        norm = backend_url.rstrip("/")
        existing = next(
            (
                b
                for b in stores.backends.list_all()
                if b.base_url.rstrip("/") == norm and b.backend_type == backend_type
            ),
            None,
        )
        backend_id = existing.id if existing else DEFAULT_BACKEND_ID
    if not stores.backends.get(backend_id):
        stores.backends.register(
            BackendConnection(
                id=backend_id,
                name=pipeline_schema.name,
                backend_type=backend_type,
                base_url=backend_url,
            )
        )

    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger

    session = Session(
        store=stores,
        backend_id=backend_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        dataset_name=dataset_name,
        dataset_config_dir=dataset_config_dir,
        identity=resolved_identity,
        tenant_root=str(stores.base_dir),
        # ``enable_tracing=False`` (L4 inner campaigns) force-disables the cloud
        # Langfuse logger so ``bridge.from_settings`` skips ``LangfuseSink`` — no
        # cloud spans, no ``_trace_metadata`` accumulation, no quota burn. The
        # local ``FileSink`` (gated on OBS_ENABLED) is untouched, so on-disk inner
        # traces still exist for the self-potter-hop drill-down.
        langfuse=LangfuseLogger(enabled=enable_tracing),
    )

    _load_dataset_into_session(session, dataset_name, status, connector=connector)
    return session


__all__ = ["backend_type_of_dataset", "init_services"]
