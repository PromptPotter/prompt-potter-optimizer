"""Step 1 of run init: stores + LLM client + connector resolution → ``Session``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter import connectors
from promptpotter.application import optimizers
from promptpotter.application.bench.resume_and_fork.replayers import replayers
from promptpotter.application.datasets.loaders import (
    build_panel,
    resolve_bank,
    samples_from_dicts,
)
from promptpotter.application.datasets.tabular_ingest import read_candidate_library_file
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import resolve_optimizer
from promptpotter.application.pipeline_resolve import (
    dataset_pipeline_declaration,
    overlay_dataset_pipeline,
    resolve_campaign_config,
)
from promptpotter.application.run_observers import check_every_record_reported
from promptpotter.application.scoring.judges import registry as judge_registry
from promptpotter.config.paths import DEFAULT_PROJECTS_ROOT
from promptpotter.connectors.protocol import PACKAGE_CACHE_KEY, Connector, InProcessWorkload
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import ensure_model_capabilities
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    dataset_experiment,
    declared_backend_type,
    extract_panel_rows,
    resolve_dataset,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.campaign_config import CampaignConfig
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
    except (httpx.HTTPError, ValueError) as exc:
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
    samples: Sequence[Sample],
    connector: Connector | None,
) -> None:
    """The converse is deliberately not warned: a ranker with no labels is no fault."""
    if not schema.nodes or not samples:
        return
    if all_verifier_graded(s.ground_truth for s in samples):
        return
    if connector is not None and connector.answer_key:
        return
    if schema.emits_ranking:
        return
    logger.warning(
        "The pipeline has no terminal ranker — no node emits a ranked list, so every sample will "
        "score NO_RESULT against a real label (check node_role on the final node)"
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
            f"{dataset_name or '<dataset>'}'s pipeline declares no observation_mappings for "
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
        f"{dataset_name}'s panel declares `{PACKAGE_CACHE_KEY}: {scope}`, "
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
    dataset: ResolvedDataset,
    *,
    connector: Connector,
    experiment: dict[str, Any] | None,
) -> tuple[PipelineSchema, dict[str, Any]]:
    """RAISES, never ``None``: a schema optional at its readers is a run that completes with wrong numbers.
    A remote backend's schema is its own answer with the dataset's file laid on, so one that cannot
    answer raises: the file alone declares another pipeline."""
    local_raw = dataset_pipeline_declaration(stores, dataset, experiment)
    if connector.execution == "in_process":
        declaration = local_raw
    else:
        declaration = overlay_dataset_pipeline(await client.fetch_pipeline(), local_raw or {})
    if declaration is None:
        raise PayloadInvalidError(
            f"{dataset.name} declares no pipeline. Every measurement is keyed on its "
            f"schema, so there is no run without it.",
            code="pipeline_config_invalid",
            details={"dataset_dir": str(dataset.directory)},
        )
    schema = parse_pipeline_response(declaration)
    logger.info("Pipeline: %s (%d nodes)", dataset.name, len(schema.nodes))
    return schema, declaration


def _session_bank(
    stores: Stores,
    dataset: ResolvedDataset,
    *,
    connector: Connector,
    experiment: dict[str, Any] | None,
) -> tuple[Bank, tuple[str, ...]]:
    """The bank and its candidate library, empty where the connector owns the panel."""
    # First, never a fallback: rows cached under the same dataset name describe another instrument.
    if connector.owns_panel:
        if experiment is None:
            raise PayloadInvalidError(
                f"Connector {connector.name!r} owns {dataset.name!r}'s panel, but its definition "
                f"declares no `panel:` and no `source:` builds one.",
                code="pipeline_config_invalid",
            )
        rows = extract_panel_rows(connector, dataset.name, experiment)
        return Bank.of(samples_from_dicts(rows)), ()
    bank = resolve_bank(stores, dataset)
    if not bank.samples:
        raise PayloadInvalidError(
            f"Dataset {dataset.name!r} holds no rows: no bank under its name and no "
            f"`source:` in its definition to cut one from.",
            code="dataset_not_found",
        )
    # Unioned with the ground-truth answers, never replacing them: every label stays rankable.
    return bank, read_candidate_library_file(dataset.directory)


def complete_registries(*, every_treatment: bool = True) -> None:
    """Every process calls it once, before it reads a ledger; one running ONE campaign passes ``False``."""
    connectors.CONNECTORS.complete()
    judge_registry.JUDGES.complete()
    optimizers.MANIFESTS.complete()
    for member in optimizers.NODE_MEMBERS.table.values():
        member.complete()
    # Its source digest raises on a prompt-shaping helper nothing hashes, stopping the boot.
    for name in optimizers.MANIFESTS.table if every_treatment else ():
        resolve_optimizer(name, {}, gate="off").treatment()
    replayers()
    check_every_record_reported()
    for connector in connectors.CONNECTORS.table.values():
        if connector.completion_check is not None:
            connector.completion_check()


async def init_services(
    dataset_name: str,
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

    dataset = resolve_dataset(stores, dataset_name)
    connector = connectors.CONNECTORS.get(declared_backend_type(dataset.directory))
    panel = dataset_experiment(stores, dataset, connector)
    if panel is None and connector.owns_panel and build_panel(stores, dataset):
        panel = dataset_experiment(stores, dataset, connector)
    experiment = None if panel is None else panel.experiment
    _verify_package_cache_scope(connector, experiment, dataset_name)
    client = BackendClient(
        connector,
        workload=InProcessWorkload(
            experiment=experiment,
            experiment_dir=None if panel is None else panel.directory,
            program=program,
        ),
    )
    logger.info("Backend: %s %s", connector.name, client.base_url)

    pipeline_schema, pipeline_declaration = await _resolve_pipeline_schema(
        client,
        stores,
        dataset,
        connector=connector,
        experiment=experiment,
    )
    _verify_required_observation_keys(pipeline_schema, connector, dataset_name)
    await _verify_connector_revision(client, connector)
    bank, candidate_library = _session_bank(
        stores, dataset, connector=connector, experiment=experiment
    )
    # By hash, never a label: every answer this session files names a bank the store holds.
    stores.tenant_datasets.put_bank(bank)
    logger.info("Dataset: %s (%d samples)", dataset_name, len(bank.samples))
    # After the samples, never before: the invariant is about the schema AND the bank together.
    _warn_if_labels_have_no_ranker(pipeline_schema, bank.samples, connector)
    return Session(
        store=stores,
        backend_id=connector.name,
        backend_client=client,
        pipeline_schema=pipeline_schema,
        pipeline_declaration=pipeline_declaration,
        bank=bank,
        candidate_library=candidate_library,
        dataset_name=dataset_name,
        dataset=dataset,
        identity=identity,
        # ``enable_tracing=False`` (L4 inner campaigns) drops only the cloud sink, never ``FileSink``.
        langfuse=LangfuseLogger(enabled=enable_tracing),
    )


async def bind_cycle_session(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
) -> tuple[Session, CampaignConfig]:
    """Bound, never configured: a resume's drift refusal reads the schema before the config lands."""
    session = await init_services(
        dataset_name=campaign.dataset_name,
        identity=stores.identity,
        stores=stores,
    )
    bound = session.bound(hop, purpose=campaign.declaration.purpose)
    return bound, resolve_campaign_config(stores, campaign, hop)
