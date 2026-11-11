"""Step 1 of run init: stores + LLM client + connector resolution → ``Session``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from promptpotter import connectors
from promptpotter.application import (
    health,  # noqa: F401 -- a reader no run imports joins the roster
    optimizers,
)
from promptpotter.application.bench.resume_and_fork.replayers import replayers
from promptpotter.application.datasets.loaders import (
    build_panel,
    resolve_bank,
    samples_from_dicts,
)
from promptpotter.application.datasets.prompts import (
    pipeline_declared_nodes,
    pipeline_node_overlay,
)
from promptpotter.application.datasets.tabular_ingest import read_candidate_library_file
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import checkin_manifest, select_optimizer
from promptpotter.application.pipeline_resolve import ServedPipeline, served_pipeline
from promptpotter.application.scoring.evaluators import (
    connector_name_refusals,
    connector_report_owner,
    observation_key_refusals,
)
from promptpotter.application.scoring.judges import registry as judge_registry
from promptpotter.config.paths import default_projects_root
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.connectors.protocol import PACKAGE_CACHE_KEY, Connector, InProcessWorkload
from promptpotter.domain.campaign_config import OptimizerChoice
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.run_records import BackendRevision, PipelineRead
from promptpotter.domain.sample import Bank, Sample
from promptpotter.domain.scoring import all_verifier_graded
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import ensure_model_capabilities
from promptpotter.infrastructure.projections import (
    event_stream,  # noqa: F401 -- a reader no run imports joins the roster
)
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    dataset_experiment,
    extract_panel_rows,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.read_model import LedgerIndex
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.identity import IdentityContext

if TYPE_CHECKING:
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.cycle_paths import CycleHop

logger = logging.getLogger(__name__)


async def _read_backend_revision(
    client: BackendClient, connector: Connector
) -> BackendRevision | None:
    """Each launch's wiring records it, and a drift is served (the dashboard, the readout), never
    refused: every contract a wrong number rides is checked on its own and raises — the schema,
    the observation keys, the package cache, harbor's trial layout (`harbor.py::_layout_fault`) —
    so a revision gate would only stop a backend
    upgrade that moved none of them."""
    expected = connector.expected_revision
    check = connector.version_check
    if not expected or check is None:
        return None
    try:
        reported = await check(client.http, client.base_url)
    except (httpx.HTTPError, ValueError) as exc:
        return BackendRevision(expected=expected, reported=None, unread=type(exc).__name__)
    return BackendRevision(expected=expected, reported=reported, unread=None)


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
    """RAISES, unlike the served revision drift: a dropped term is a wrong number, not drift."""
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


def _verify_observation_names(
    schema: PipelineSchema,
    connector: Connector,
    dataset_name: str | None,
) -> None:
    """An observation named like another owner's term would give one name two values."""
    report = connector_report_owner(connector.name, connector.cell_report_keys)
    if refused := observation_key_refusals(schema.observation_keys, report):
        raise PayloadInvalidError(
            f"{dataset_name or '<dataset>'}'s pipeline maps {'; '.join(refused)}, and a cell's "
            "formula reads one value per name. Map the observation under another key.",
            code="pipeline_config_invalid",
            details={"dataset_name": dataset_name, "refused_observation_keys": refused},
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


def read_treatment(name: str) -> None:
    """An optimizer as every launch reads it, an install shadow included; its source digest raises
    on a prompt-shaping helper nothing hashes."""
    select_optimizer(OptimizerChoice(optimizer=name)).treatment(
        frozenset(Presumption), PackShelf(account=None)
    )


def complete_registries(*, every_treatment: bool = True) -> None:
    """Every process calls it once, before it reads a ledger; one running ONE campaign passes ``False``."""
    connectors.CONNECTORS.complete()
    for name, connector in connectors.CONNECTORS.table.items():
        if shadowed := connector_name_refusals(connector.cell_terms, connector.reported_keys):
            where = f"connector {name!r} [{connectors.CONNECTORS.origins[name]}]"
            raise RuntimeError(f"{where}: {'; '.join(shadowed)}.")
    judge_registry.JUDGES.complete()
    optimizers.MANIFESTS.complete()
    for member in optimizers.NODE_MEMBERS.table.values():
        member.complete()
    if every_treatment:
        for name in optimizers.MANIFESTS.table:
            read_treatment(name)
        # The check-in as every check-in and mint reads it, an install shadow included.
        checkin_manifest()
        LedgerIndex.check_every_arm_read()
    replayers()
    for connector in connectors.CONNECTORS.table.values():
        if connector.completion_check is not None:
            connector.completion_check()


async def init_services(
    dataset_name: str,
    *,
    identity: IdentityContext,
    stores: Stores | None = None,
    program: object | None = None,
    served: ServedPipeline | None = None,
) -> Session:
    """A session on what *dataset_name*'s definition names: a campaign's own is bound by
    `bind_cycle_session`, on the connector its mint froze. *stores* is the ONE way to relocate the
    tree: the L4 inner runner passes a sandboxed one. *served* is a check-in Start's one backend
    read, under which it landed the definition."""
    if stores is None:
        stores = build_stores(identity, projects_root=default_projects_root())
    dataset = resolve_dataset(stores, dataset_name)
    connector = connectors.CONNECTORS.get(runnable_definition(dataset).backend_type)
    return await _open_session(
        stores, dataset, connector, identity=identity, program=program, served=served
    )


async def _open_session(
    stores: Stores,
    dataset: ResolvedDataset,
    connector: Connector,
    *,
    identity: IdentityContext,
    program: object | None = None,
    served: ServedPipeline | None = None,
) -> Session:
    await ensure_model_capabilities(Path(stores.base_dir))
    dataset_name = dataset.name
    definition = runnable_definition(dataset)
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
        jobs_dir=stores.jobs_dir,
    )
    logger.info("Backend: %s %s", connector.name, client.base_url)

    if served is None:
        served = await served_pipeline(stores, connector, experiment=experiment)
    declaration = served.over(definition.pipeline)
    pipeline_schema = parse_pipeline_response(declaration)
    logger.info("Pipeline: %s (%d nodes)", dataset.name, len(pipeline_schema.nodes))
    _verify_required_observation_keys(pipeline_schema, connector, dataset_name)
    _verify_observation_names(pipeline_schema, connector, dataset_name)
    backend_revision = await _read_backend_revision(client, connector)
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
        backend_client=client,
        pipeline_schema=pipeline_schema,
        pipeline_read=PipelineRead(
            declaration=declaration,
            dataset_overlay=pipeline_node_overlay(definition.pipeline),
            declared_nodes=pipeline_declared_nodes(definition.pipeline),
        ),
        backend_revision=backend_revision,
        bank=bank,
        candidate_library=candidate_library,
        dataset_name=dataset_name,
        dataset=dataset,
        identity=identity,
    )


async def bind_cycle_session(
    stores: Stores,
    campaign: Campaign,
    hop: CycleHop,
) -> Session:
    """Bound, never configured: a resume's drift refusal reads the schema before the config lands."""
    session = await _open_session(
        stores,
        resolve_dataset(stores, campaign.dataset_name),
        connectors.CONNECTORS.get(campaign.connector),
        identity=stores.identity,
    )
    return session.bound(hop, purpose=campaign.declaration.purpose)
