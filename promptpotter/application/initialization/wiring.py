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
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.backend import BackendClient, build_backend_client
from promptpotter.infrastructure.store.archive_views import maintain_measurement_index
from promptpotter.infrastructure.store.dataset_access import (
    dataset_panel_rows,
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


def _warn_if_labels_have_no_ranker(
    schema: PipelineSchema,
    samples: list[Sample],
    connector: connectors.Connector | None,
    status: Callable[[str], None],
) -> None:
    """**Labels and a ranker travel together.** A dataset carrying ground truth and no node emitting
    a ranked list is mis-wired — every sample silently scores ``NO_RESULT`` against a real label —
    so it is surfaced at setup, on the status line, not at score time.

    Judging it needs the schema AND the samples, which is why it is called from ``init_services``
    after they resolve rather than from ``_resolve_pipeline_schema``, which sees neither.

    **The converse is deliberately not warned.** A ranker with no labels is not a fault —
    ``promptpotter-self`` declares ``l1_critique`` as one so its summary reaches ``predicted`` for
    a human reading the round file — and neither is a backend that carries its answer elsewhere,
    which a declared ``Connector.answer_key`` says."""
    if not schema.nodes or not samples:
        return
    if all_verifier_graded(s.ground_truth for s in samples):
        return
    if connector is not None and connector.answer_key:
        return
    if any(n.emits_ranking and n.output_keys for n in schema.nodes):
        return
    msg = (
        f"Pipeline {schema.name!r} has no terminal ranker — no node emits a ranked list, "
        "so every sample will score NO_RESULT against a real label "
        "(check node_role on the final node)"
    )
    logger.warning(msg)
    status(f"⚠ {msg}")


def _verify_required_observation_keys(
    schema: PipelineSchema,
    connector: connectors.Connector,
    dataset_name: str | None,
) -> None:
    """Fails at arm time rather than letting a dropped key reach the formula as a measurement
    nobody took. RAISES, unlike its advisory revision sibling — a silently dropped term is a wrong
    number, not drift (``connectors/CLAUDE.md`` § Conventions)."""
    required = connector.required_observation_keys
    if not required:
        return
    declared = {key for node in schema.nodes for key in node.output_keys}
    missing = [k for k in required if k not in declared]
    if missing:
        raise PayloadInvalidError(
            f"backend {connector.name!r} always emits {missing}, but "
            f"{dataset_name or '<dataset>'}'s pipeline.yaml declares no observation_mappings for "
            "them — an undeclared key never reaches pipeline_data, so the scoring formula would "
            "grade a measurement that was silently dropped.",
            code="pipeline_config_invalid",
            details={"dataset_name": dataset_name, "missing_observation_keys": missing},
        )


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


def _load_dataset_into_session(
    session: Session,
    dataset_name: str,
    status: Callable[[str], None],
    *,
    connector: connectors.Connector | None = None,
) -> None:
    """Populate session.samples + index_terms — a connector's own experiment file where it declares
    one, else tenant Origin, then repo benchmark, then the loader's one-shot download."""
    # First, never a fallback: a connector declaring an `experiment_file` OWNS its panel, and rows
    # cached under the same dataset name describe a different instrument.
    if connector is not None and connector.experiment_file:
        panel = dataset_panel_rows(session.store, dataset_name)
        if panel is None:
            raise ValueError(
                f"Connector {connector.name!r} owns {dataset_name!r}'s panel, but its "
                f"pipeline.yaml no longer names that backend_type."
            )
        queries, session.index_terms = panel
        session.samples = samples_from_dicts(queries)
        status(f"Experiment: {connector.experiment_file} ({len(queries)} tasks)")
        return
    items = resolve_dataset_items(session.store, dataset_name, status=status)
    if not items:
        status(f"Dataset '{dataset_name}' not available")
        raise ValueError(
            f"Dataset {dataset_name!r} not found in tenant uploads, repo benchmarks, "
            f"or any registered loader. Add one to DATASET_LOADERS in "
            f"application/datasets/loaders.py."
        )

    # Whether a MISSING label disqualifies a row is DERIVED from the set, not declared: if any row
    # carries one this is a labelled dataset and a row without is broken (drop it, as always); if
    # none does, the dataset is verifier-graded and dropping on that test empties it entirely.
    # `harbor` escaped only because it declares an `experiment_file` and returned above; a
    # labelless dataset arriving through the loader registry or a tenant upload yielded
    # `session.samples == []` and a run that measured nothing, with nothing raised.
    labelled = any(item.get("ground_truth") for item in items)
    valid = [
        item for item in items if item.get("query") and (item.get("ground_truth") or not labelled)
    ]
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
        client,
        dataset_config_dir,
        status,
        in_process=connector.execution == "in_process",
    )
    _verify_required_observation_keys(pipeline_schema, connector, dataset_name)
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
    # After the samples, never before: the invariant is about the schema AND the bank together.
    _warn_if_labels_have_no_ranker(pipeline_schema, session.samples, connector, status)
    return session


__all__ = ["init_services"]
