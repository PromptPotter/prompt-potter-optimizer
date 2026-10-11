"""Step 1 of run init: stores + LLM client + connector resolution → ``Session``."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter import connectors
from promptpotter.application import optimizers
from promptpotter.application.bench.resume_and_fork.replayers import replayers
from promptpotter.application.datasets.csv_ingest import read_candidate_library_file
from promptpotter.application.datasets.loaders import (
    bank_samples,
    resolve_dataset_items,
    samples_from_dicts,
)
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import resolve_optimizer
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    overlay_dataset_pipeline,
    resolve_campaign_config,
)
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.config.settings import (
    DEFAULT_BACKEND_ID,
    DEFAULT_BACKEND_URL,
)
from promptpotter.connectors.protocol import PACKAGE_CACHE_KEY, Connector, InProcessWorkload
from promptpotter.domain.backend import BackendConnection
from promptpotter.domain.optimizer_state import round_payload_type
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import ensure_model_capabilities
from promptpotter.infrastructure.store.dataset_access import (
    dataset_experiment,
    declared_backend_type,
    extract_panel_rows,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger
from promptpotter.judges import registry as judge_registry
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop

logger = logging.getLogger(__name__)


async def _verify_connector_revision(
    client: BackendClient,
    connector: Connector,
) -> None:
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
    connector: Connector | None,
) -> None:
    """The converse is deliberately not warned: a ranker with no labels is no fault."""
    if not schema.nodes or not samples:
        return
    if all_verifier_graded(s.ground_truth for s in samples):
        return
    if connector is not None and connector.answer_key:
        return
    if any(n.emits_ranking and n.output_keys for n in schema.nodes):
        return
    logger.warning(
        "Pipeline %r has no terminal ranker — no node emits a ranked list, so every sample will "
        "score NO_RESULT against a real label (check node_role on the final node)",
        schema.name,
    )


def _verify_required_observation_keys(
    schema: PipelineSchema,
    connector: Connector,
    dataset_name: str | None,
) -> None:
    """RAISES, unlike its advisory revision sibling: a dropped term is a wrong number, not drift."""
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


def _verify_package_cache_scope(
    connector: Connector,
    experiment: dict[str, Any] | None,
    dataset_name: str,
) -> None:
    scope = (experiment or {}).get(PACKAGE_CACHE_KEY)
    if scope is None or scope in connector.package_cache_scopes:
        return
    honoured = sorted(connector.package_cache_scopes)
    raise PayloadInvalidError(
        f"{dataset_name}'s {connector.experiment_file} declares `{PACKAGE_CACHE_KEY}: {scope}`, "
        f"but backend {connector.name!r} "
        + (f"honours only {honoured}." if honoured else "routes nothing through the package cache.")
        + f" Remove the key{' or name one of those' if honoured else ''}.",
        code="pipeline_config_invalid",
        details={
            "dataset_name": dataset_name,
            PACKAGE_CACHE_KEY: scope,
            "package_cache_scopes": honoured,
        },
    )


async def _resolve_pipeline_schema(
    client: BackendClient,
    stores: Stores,
    dataset_config_dir: Path | None,
    *,
    connector: Connector,
    experiment: dict[str, Any] | None,
) -> tuple[PipelineSchema, dict[str, Any]]:
    """RAISES, never ``None``: a schema optional at its readers is a run that completes with wrong numbers."""
    backend_resp: dict[str, Any] | None = None
    if connector.execution != "in_process":
        try:
            backend_resp = await client.fetch_pipeline()
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.info("Could not fetch pipeline schema from backend: %s", exc)

    local_raw: dict[str, Any] | None = None
    if dataset_config_dir is not None:
        local_raw = dataset_pipeline_declaration(stores, dataset_config_dir, experiment)

    # A `PayloadInvalidError` is a DECLARATION error: falling back would answer with another pipeline.
    if backend_resp:
        merged = overlay_dataset_pipeline(backend_resp, local_raw or {})
        try:
            schema = parse_pipeline_response(merged)
            logger.info("Pipeline: %s (%d nodes)", schema.name, len(schema.nodes))
            return schema, merged
        except PayloadInvalidError:
            raise
        except Exception as exc:
            logger.warning("Failed to parse merged pipeline schema: %s", exc)

    if local_raw is not None:
        try:
            schema = parse_pipeline_response(local_raw)
            logger.info("Pipeline: %s (%d nodes, offline)", schema.name, len(schema.nodes))
            return schema, local_raw
        except PayloadInvalidError:
            raise
        except Exception as exc:
            logger.warning("Failed to parse offline pipeline.yaml: %s", exc)

    raise PayloadInvalidError(
        f"could not resolve a pipeline schema for {dataset_config_dir}. The backend "
        f"returned nothing usable and the dataset's own pipeline.yaml did not parse "
        f"(see the warnings above). Every measurement is keyed on this schema, so there "
        f"is no run without it — fix the file, or point --backend-url at a reachable backend.",
        code="pipeline_config_invalid",
        details={"dataset_config_dir": str(dataset_config_dir)},
    )


def _load_dataset_into_session(
    session: Session,
    dataset_name: str,
    *,
    connector: Connector,
    experiment: dict[str, Any] | None,
) -> None:
    # First, never a fallback: rows cached under the same dataset name describe another instrument.
    if connector.experiment_file:
        if experiment is None:
            raise PayloadInvalidError(
                f"Connector {connector.name!r} owns {dataset_name!r}'s panel, but "
                f"{connector.experiment_file!r} is not in "
                f"{readable_dataset_dir(session.store, dataset_name)}. The panel has not been "
                f"generated on this machine (it is gitignored — rebuild it).",
                code="pipeline_config_invalid",
            )
        queries = extract_panel_rows(connector, dataset_name, experiment)
        session.samples = samples_from_dicts(queries)
        logger.info("Experiment: %s (%d tasks)", connector.experiment_file, len(queries))
        return
    items = resolve_dataset_items(session.store, dataset_name)
    if not items:
        raise PayloadInvalidError(
            f"Dataset {dataset_name!r} not found in tenant uploads, repo benchmarks, "
            f"or any registered loader. Add one to DATASET_LOADERS in "
            f"application/datasets/loaders.py.",
            code="dataset_not_found",
        )

    session.samples = bank_samples(items)
    gt_terms = {r["ground_truth"] for r in items if r.get("ground_truth")}
    config_dir = readable_dataset_dir(session.store, dataset_name)
    # Unioned with the ground-truth answers, never replacing them: every label stays rankable.
    library = read_candidate_library_file(config_dir)
    session.index_terms = sorted(gt_terms | set(library))
    if library:
        logger.info(
            "Candidate library: +%d targets (term index now %d)",
            len(set(library) - gt_terms),
            len(session.index_terms),
        )
    logger.info("Dataset: %s (%d samples)", dataset_name, len(items))


def _resolve_backend_id(
    stores: Stores,
    requested: str,
    backend_url: str,
    backend_type: str,
    name: str,
) -> str:
    """*requested* is a PREFERENCE: an id a DIFFERENT endpoint holds never absorbs the run."""
    norm = backend_url.rstrip("/")
    for b in stores.backends.list_all():
        if b.base_url.rstrip("/") == norm and b.backend_type == backend_type:
            return b.id
    backend_id = requested or DEFAULT_BACKEND_ID
    if stores.backends.get(backend_id) is not None:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", norm.split("://", 1)[-1]).strip("-")
        backend_id = f"{backend_type}-{slug}"
    stores.backends.register(
        BackendConnection(
            id=backend_id,
            name=name,
            backend_type=backend_type,
            base_url=backend_url,
        )
    )
    return backend_id


def complete_registries(*, every_treatment: bool = True) -> None:
    """Every process calls it once, before it reads a ledger; one running ONE campaign passes ``False``."""
    table = connectors.registered()
    judge_registry.registered()
    optimizers.registered()
    for runtime in optimizers.runtimes().values():
        round_payload_type(runtime.name)
        runtime.complete()
        # Its source digest raises on a prompt-shaping helper nothing hashes, stopping the boot.
        if every_treatment:
            resolve_optimizer(runtime.name, {}).treatment()
    replayers()
    for connector in table.values():
        if connector.completion_check is not None:
            connector.completion_check()


async def init_services(
    dataset_name: str,
    backend_url: str = DEFAULT_BACKEND_URL,
    backend_id: str = "",
    *,
    identity: IdentityContext,
    stores: Stores | None = None,
    enable_tracing: bool = True,
    program: object | None = None,
) -> Session:
    """*stores* is the ONE way to relocate the tree: the L4 inner runner passes a sandboxed one."""
    if stores is None:
        stores = build_stores(identity, projects_root=DEFAULT_PROJECTS_ROOT)

    await ensure_model_capabilities(Path(stores.base_dir))

    dataset_config_dir = readable_dataset_dir(stores, dataset_name)
    backend_type = declared_backend_type(dataset_config_dir)
    connector = connectors.get(backend_type)
    experiment = dataset_experiment(dataset_config_dir, connector)
    _verify_package_cache_scope(connector, experiment, dataset_name)
    client = BackendClient(
        connector, backend_url, workload=InProcessWorkload(experiment=experiment, program=program)
    )
    logger.info("Backend: %s", backend_url)

    pipeline_schema, pipeline_declaration = await _resolve_pipeline_schema(
        client,
        stores,
        dataset_config_dir,
        connector=connector,
        experiment=experiment,
    )
    _verify_required_observation_keys(pipeline_schema, connector, dataset_name)
    await _verify_connector_revision(client, connector)

    backend_id = _resolve_backend_id(
        stores, backend_id, backend_url, backend_type, pipeline_schema.name
    )

    session = Session(
        store=stores,
        backend_id=backend_id,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        pipeline_declaration=pipeline_declaration,
        dataset_name=dataset_name,
        dataset_config_dir=dataset_config_dir,
        identity=identity,
        tenant_root=str(stores.base_dir),
        # ``enable_tracing=False`` (L4 inner campaigns) drops only the cloud sink, never ``FileSink``.
        langfuse=LangfuseLogger(enabled=enable_tracing),
    )

    _load_dataset_into_session(session, dataset_name, connector=connector, experiment=experiment)
    # After the samples, never before: the invariant is about the schema AND the bank together.
    _warn_if_labels_have_no_ranker(pipeline_schema, session.samples, connector)
    return session


async def bind_cycle_session(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
) -> tuple[Session, CampaignConfig]:
    """Unbound, the runner mints a fresh campaign and steals the active pointer from this cycle."""
    session = await init_services(
        backend_url=campaign.backend_url,
        backend_id=campaign.backend_id,
        dataset_name=campaign.dataset_name,
        identity=stores.identity,
        stores=stores,
    )
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id
    return session, resolve_campaign_config(stores, campaign, hop)


__all__ = ["bind_cycle_session", "complete_registries", "init_services"]
