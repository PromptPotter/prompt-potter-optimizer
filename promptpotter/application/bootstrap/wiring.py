"""Stores + LLMClient + connector resolution → ``Session``.

``init_services`` opens stores under the tenant root, applies the tenant-pointer
guard, resolves the connector, fetches the pipeline schema, registers the
backend, and either loads the dataset or syncs an experiment from the backend.
Identity + scoring lifecycle live in ``session`` + ``scoring_context``."""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from promptpotter import connectors
from promptpotter.application.bootstrap.session import Session
from promptpotter.application.datasets import (
    read_candidate_library_file,
    resolve_dataset_items,
    samples_from_dicts,
)
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
    DEFAULT_EXPERIMENT_ID,
)
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.backend import BackendClient, build_backend_client
from promptpotter.infrastructure.store import Stores, build_stores
from promptpotter.infrastructure.store.io import read_json_optional
from promptpotter.shared.identity import IdentityContext, default_identity

logger = logging.getLogger(__name__)


def _apply_dataset_overlay(
    backend_resp: dict[str, Any], local_raw: dict[str, Any]
) -> dict[str, Any]:
    """Merge dataset ``pipeline.json`` overlay onto the backend response.
    Overlay carries ``pipelines.default`` / per-node config deltas / metadata; backend stays SoT for runtime defaults."""
    out = copy.deepcopy(backend_resp.get("data") or backend_resp)
    if "pipelines" in local_raw:
        out["pipelines"] = local_raw["pipelines"]
    for node_name, node_def in (local_raw.get("nodes") or {}).items():
        if not isinstance(node_def, dict):
            continue
        out.setdefault("nodes", {}).setdefault(node_name, {})
        for k, v in node_def.items():
            # ``config`` and ``optimizer`` shallow-merge onto the backend node so a
            # partial overlay (e.g. a connector seed narrowing
            # ``optimizer.param_allowed_values``) augments the live schema instead of
            # clobbering the backend's ``observation_mappings`` / ``param_keys``. A
            # full authored block (justlogic) still fully overrides — its keys win.
            if k in ("config", "optimizer") and isinstance(v, dict):
                out["nodes"][node_name].setdefault(k, {}).update(v)
            else:
                out["nodes"][node_name][k] = v
    return out


async def _verify_connector_revision(
    client: BackendClient,
    connector: connectors.Connector,
) -> None:
    """Compare ``connector.expected_revision`` to the live backend's reported
    revision; WARN on drift. No-op when either field is ``None`` — opt-in
    per connector. Network errors are swallowed (the WARN says
    "could not verify", not "mismatch")."""
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


def resolve_dataset_config_dir(store: Stores, project_root: Path, dataset_name: str) -> Path:
    """Return the dataset's config dir — tenant Origin first, then repo benchmark.

    Tenant uploads land at ``{tenant_root}/datasets/{slug}/`` (the chat-first
    ingest path); repo ``datasets/{name}/`` holds install-global benchmarks
    (`aime_2025`, `bbeh`, `gsm8k`, `justlogic`, `lca-termnorm`,
    `promptpotter-self`, ...). On collision the tenant copy wins, so a user
    can override a benchmark by ingesting their own slug of the same name.
    """
    tenant_dir = store.tenant_datasets.dataset_dir(dataset_name)
    if (tenant_dir / "pipeline.json").is_file() or (tenant_dir / "cache.json").is_file():
        return tenant_dir
    return project_root / "datasets" / dataset_name


def _warn_if_no_terminal_ranker(schema: PipelineSchema, status: Callable[[str], None]) -> None:
    """A pipeline yields a prediction only if some ranker/candidate_source node emits a
    ranked list — ``terminal_ranking`` reads the terminal one's head as ``predicted``.
    Without such a node every sample silently scores NO_RESULT (the lca-bom-termnorm trap).
    Surface it loudly at setup, on the operator-visible status line, not at score time."""
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
) -> PipelineSchema | None:
    """Backend ``GET /pipeline`` is authoritative for runtime defaults; local
    ``{dataset_config_dir}/pipeline.json`` is the operator overlay. Merged
    here before parsing — backend underneath, dataset on top. Backend
    unreachable → local file alone (offline mode).

    ``in_process`` connectors (``llm_only`` / ``promptpotter``) have NO remote
    backend, so the local ``pipeline.json`` IS the whole schema — skip the fetch
    entirely (otherwise it would hit ``backend_url`` and merge an unrelated
    backend's nodes, e.g. TermNorm's, under the dataset overlay)."""
    backend_resp: dict[str, Any] | None = None
    if in_process:
        pass  # no remote backend — local pipeline.json is authoritative
    else:
        try:
            backend_resp = await client.fetch_pipeline()
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.info("Could not fetch pipeline schema from backend: %s", exc)

    local_raw: dict[str, Any] | None = None
    if dataset_config_dir is not None:
        local_raw = read_json_optional(dataset_config_dir / "pipeline.json")

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
            logger.warning("Failed to parse offline pipeline.json: %s", exc)

    status("Pipeline: unavailable")
    return None


def _read_backend_type(dataset_config_dir: Path | None, dataset_name: str | None) -> str:
    """Resolve backend_type from ``{dataset_config_dir}/pipeline.json``. Required field."""
    if not dataset_name or dataset_config_dir is None:
        raise ValueError("dataset_name required to resolve backend_type for connector lookup")
    raw = read_json_optional(dataset_config_dir / "pipeline.json")
    bt = (raw or {}).get("backend_type")
    if not isinstance(bt, str) or not bt:
        raise ValueError(f"backend_type missing or empty in {dataset_config_dir}/pipeline.json")
    return bt.lower()


def backend_type_of_dataset(store: Stores, project_root: Path, dataset_name: str) -> str:
    """Connector kind of *dataset_name*, or ``""`` when it cannot be resolved.

    THE predicate for "which connector does this dataset use?" for every read-side caller —
    the sidebar's self-optimization test and the meta-champion reducer's corpus filter both
    ask it, so neither hand-maintains a list of dataset NAMES (a name allowlist silently skips
    an arm, a fork, or a renamed dataset instead of loudly rejecting it).

    Tolerant, unlike the strict twin ``_read_backend_type`` above: a campaign outlives its
    dataset dir, and a reader that raises because one old dataset was deleted is worse than one
    that treats that campaign as a plain (non-L4) campaign. Bootstrap still raises, because
    there a missing kind means the run cannot pick a connector at all.
    """
    try:
        raw = read_json_optional(
            resolve_dataset_config_dir(store, project_root, dataset_name) / "pipeline.json"
        )
    except (OSError, ValueError):
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
    """Populate session.samples + index_terms.

    Precedence: tenant Origin (``projects/{tenant}/datasets/{slug}/``) →
    repo benchmark (``datasets/{name}/``) → ``DATASET_LOADERS`` registry
    one-shot download into the benchmark tree. Tenant uploads never
    cross-contaminate the install-global benchmark slot.

    A connector that declares an :attr:`~Connector.experiment_file` (the
    in-process ``promptpotter`` L4 dataset, whose outer "samples" ARE the inner
    tasks in ``inner_tasks.json``) loads through ``extract_experiment`` when no
    CSV/loader samples exist — so the same ``new <dataset>`` path serves it.
    """
    items = resolve_dataset_items(session.store, dataset_name, status=status)
    if not items and connector is not None and connector.experiment_file:
        _load_experiment_file_into_session(session, connector, status)
        return
    if not items:
        status(f"Dataset '{dataset_name}' not available")
        raise ValueError(
            f"Dataset {dataset_name!r} not found in tenant uploads, repo benchmarks, "
            f"or DATASET_LOADERS. Add a loader to DATASET_LOADERS in dataset_builder.py."
        )

    valid = [item for item in items if item.get("query") and item.get("ground_truth")]
    session.samples = samples_from_dicts(valid)
    gt_terms = {r["ground_truth"] for r in items if r.get("ground_truth")}
    config_dir = resolve_dataset_config_dir(session.store, Path(session.project_root), dataset_name)
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
    """Load samples from the connector's on-disk experiment doc (L4: the inner
    tasks in ``inner_tasks.json``) via ``extract_experiment`` — the same seam the
    experiment-sync path uses, but reading the dataset dir instead of a backend."""
    config_dir = session.dataset_config_dir
    exp_path = (config_dir / connector.experiment_file) if config_dir else None
    data = read_json_optional(exp_path) if exp_path else None
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
    status(f"Experiment: {exp_name} ({len(queries)} samples, {len(index_terms)} session terms)")

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
    identity: IdentityContext | None = None,
    store: Stores | None = None,
    enable_tracing: bool = True,
) -> Session:
    """Init store, client, pipeline schema, scoring data — step 1 of bootstrap.

    Preconditions: ``.promptpotter/`` tree + ``datasets/{dataset_name}/pipeline.json``
    declaring ``backend_type``. Returns a wired ``Session`` (no scoring yet).
    ``identity`` defaults to the Stage-0 single-operator :func:`default_identity`.
    Active-session pointers are per-tenant on disk, so two operators on the
    same machine cannot collide.

    ``store`` injects a pre-built :class:`Stores` instead of rooting one under
    ``project_root/.promptpotter``: the L4 inner-cycle runner passes a sandboxed
    store rooted at the spawning cycle's ``.runtime/inner/`` so an inner campaign's
    state never touches the outer's active pointer. ``project_root`` still resolves
    repo-benchmark dataset dirs (``datasets/{name}/``), so the sandbox reads the
    inner dataset from the repo while writing campaign state into the sandbox."""

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    resolved_identity = identity if identity is not None else default_identity()

    if project_root is None:
        project_root = (
            Path(__file__).resolve().parent.parent.parent.parent
        )  # wiring → bootstrap → application → promptpotter → repo_root

    if store is None:
        store = build_stores(
            resolved_identity, projects_root=project_root / ".promptpotter" / "projects"
        )

    dataset_config_dir = (
        resolve_dataset_config_dir(store, project_root, dataset_name) if dataset_name else None
    )
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
                for b in store.backends.list_all()
                if b.base_url.rstrip("/") == norm and b.backend_type == backend_type
            ),
            None,
        )
        backend_id = existing.id if existing else DEFAULT_BACKEND_ID
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
        dataset_name=dataset_name,
        dataset_config_dir=dataset_config_dir,
        identity=resolved_identity,
        project_root=str(store.base_dir),
        # ``enable_tracing=False`` (L4 inner campaigns) force-disables the cloud
        # Langfuse logger so ``bridge.from_settings`` skips ``LangfuseSink`` — no
        # cloud spans, no ``_trace_metadata`` accumulation, no quota burn. The
        # local ``FileSink`` (gated on OBS_ENABLED) is untouched, so on-disk inner
        # traces still exist for the self-potter-hop drill-down.
        langfuse=LangfuseLogger(enabled=enable_tracing),
    )

    if dataset_name:
        _load_dataset_into_session(session, dataset_name, status, connector=connector)
    else:
        await _sync_and_extract_experiment(session, backend_url, experiment_id, status)
    return session


__all__ = ["backend_type_of_dataset", "init_services", "resolve_dataset_config_dir"]
