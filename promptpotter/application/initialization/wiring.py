"""Stores + LLMClient + connector resolution → ``Session``.

``init_services`` opens stores under the tenant root, applies the tenant-pointer
guard, resolves the connector, fetches the pipeline schema, registers the backend,
and loads the dataset. Identity + scoring lifecycle live in ``session`` +
``loop_start``."""

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
) -> PipelineSchema:
    """Backend ``GET /pipeline`` is authoritative for runtime defaults; local
    ``{dataset_config_dir}/pipeline.yaml`` is the operator overlay. Merged
    here before parsing — backend underneath, dataset on top. Backend
    unreachable → local file alone (offline mode).

    An ``in_process`` connector (``promptpotter``) has NO remote backend, so the
    local ``pipeline.yaml`` IS the whole schema — skip the fetch entirely
    (otherwise it would hit ``backend_url`` and merge an unrelated backend's
    nodes, e.g. TermNorm's, under the dataset overlay).

    **Raises rather than returning ``None``.** Neither branch is optional in practice:
    ``_read_backend_type`` has already read this same file and raised unless it parses
    and declares a ``backend_type``, so the only way to arrive with nothing is a
    pipeline.yaml that fails to parse — a setup error, not a mode. Returning ``None``
    for it would make the schema optional on ``Session`` and therefore optional at the ~40
    readers downstream, where a missing schema does not fail: it drops the rendered
    prompt from the searchpoint, picks the other ``sp_hash`` algorithm, and stamps a
    different origin cycle id. The run completes and the numbers are wrong."""
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
    """Resolve backend_type from ``{dataset_config_dir}/pipeline.yaml``. Required field.

    Typed for the same reason its sibling :func:`_resolve_pipeline_schema` is: both fail
    on an unusable ``pipeline.yaml``, both fire on the web Start path, and a bare
    ``ValueError`` reaches the API's catch-all as a 500 — which the webapp classifies
    ``transient`` and retries forever against a config only the operator can fix.
    """
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
    """Connector kind of *dataset_name*, or ``""`` when it cannot be resolved.

    THE predicate for "which connector does this dataset use?" for every read-side caller —
    the sidebar's self-optimization test and the L4 prompt ranking's corpus filter both
    ask it, so neither hand-maintains a list of dataset NAMES (a name allowlist silently skips
    an arm, a fork, or a renamed dataset instead of loudly rejecting it).

    Tolerant, unlike the strict twin ``_read_backend_type`` above: a campaign outlives its
    dataset dir, and a reader that raises because one old dataset was deleted is worse than one
    that treats that campaign as a plain (non-L4) campaign. Init still raises, because
    there a missing kind means the run cannot pick a connector at all.
    """
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
    """Populate session.samples + index_terms.

    Precedence: tenant Origin (``projects/{tenant}/datasets/{slug}/``) →
    repo benchmark (``datasets/{name}/``) → the ``dataset_loader`` resolver's
    one-shot download into the benchmark tree. Tenant uploads never
    cross-contaminate the install-global benchmark slot.

    A connector that declares an :attr:`~Connector.experiment_file` (the
    in-process ``promptpotter`` L4 dataset, whose outer "samples" ARE the inner
    tasks in ``inner_tasks.yaml``) loads through ``extract_experiment`` when no
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
    """Load samples from the connector's on-disk experiment doc (L4: the inner
    tasks in ``inner_tasks.yaml``) via ``extract_experiment`` — the same seam the
    experiment-sync path uses, but reading the dataset dir instead of a backend."""
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
    """Init store, client, pipeline schema, scoring data — step 1 of run init.

    Preconditions: ``.promptpotter/`` tree + ``datasets/{dataset_name}/pipeline.yaml``
    declaring ``backend_type``. Returns a wired ``Session`` (no scoring yet).
    ``dataset_name`` is REQUIRED — the dataset resolves the config dir, which resolves
    the connector, so there is no session without one.
    ``identity`` defaults to the Stage-0 single-operator :func:`default_identity`.
    Active-session pointers are per-tenant on disk, so two operators on the
    same machine cannot collide.

    ``store`` injects a pre-built :class:`Stores` instead of letting
    :func:`build_stores` resolve the user-data root: the L4 inner-cycle runner passes a
    sandboxed store rooted at the flat ``<workspace>/.inner/<key>/`` registry, keyed on
    the owning (tenant, campaign, cycle)
    — named by the spawning cycle, never nested under it — so an inner campaign's state
    never touches the outer's active pointer. It is the ONE way to relocate the tree;
    a ``project_root`` parameter sat beside it doing a weaker version of the same job,
    lost its last caller when the roots moved into ``config/paths.py``, and is gone."""

    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    resolved_identity = identity if identity is not None else default_identity()

    if stores is None:
        stores = build_stores(resolved_identity, projects_root=DEFAULT_PROJECTS_ROOT)

    maintain_measurement_index(stores)

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
