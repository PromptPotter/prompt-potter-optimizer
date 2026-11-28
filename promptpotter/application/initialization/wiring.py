"""Step 1 of run init: stores + LLM client + connector resolution → ``Session``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from pydantic import BaseModel

from promptpotter.application.datasets.loaders import (
    build_panel,
    resolve_bank,
    samples_from_dicts,
)
from promptpotter.application.datasets.prompts import pipeline_node_overlay
from promptpotter.application.datasets.tabular_ingest import read_answer_terms_file
from promptpotter.application.initialization.session import Session
from promptpotter.application.pipeline_resolve import (
    ServedPipeline,
    campaign_connector,
    served_pipeline,
)
from promptpotter.application.scoring.evaluators import (
    connector_report_owner,
    observation_key_refusals,
)
from promptpotter.config.paths import default_projects_root
from promptpotter.connectors.protocol import PACKAGE_CACHE_KEY, Connector, InProcessWorkload
from promptpotter.domain.campaign import CampaignCycle
from promptpotter.domain.pipeline_parsing import parse_pipeline_response
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.records.run_wiring import BackendRevision, PipelineRead
from promptpotter.domain.sample import Bank
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import ensure_model_capabilities
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    dataset_experiment,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.stores import Stores, build_stores
from promptpotter.shared.errors import PayloadInvalidError
from promptpotter.shared.identity import IdentityContext


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


def _verify_required_observation_keys(
    schema: PipelineSchema,
    connector: Connector,
    dataset_name: str | None,
) -> None:
    """RAISES, unlike the served revision drift: a dropped term is a wrong number, not drift."""
    required = connector.required_observation_keys
    if not required:
        return
    declared = {key for node in schema.running_nodes for key in node.output_keys}
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
    report = connector_report_owner(connector)
    if refused := observation_key_refusals(schema.observation_keys, report):
        raise PayloadInvalidError(
            f"{dataset_name or '<dataset>'}'s pipeline maps {'; '.join(refused)}, and a cell's "
            "formula reads one value per name. Map the observation under another key.",
            code="pipeline_config_invalid",
            details={"dataset_name": dataset_name, "refused_observation_keys": refused},
        )


def _verify_package_cache_scope(
    connector: Connector,
    experiment: BaseModel | None,
    dataset_name: str,
) -> None:
    # An extra or a declared field alike: the key is the panel's, whatever its model.
    scope = getattr(experiment, PACKAGE_CACHE_KEY, None)
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


async def session_bank(
    stores: Stores,
    dataset: ResolvedDataset,
    *,
    connector: Connector,
    experiment: BaseModel | None,
) -> tuple[Bank, tuple[str, ...]]:
    """The bank and its answer terms, empty where the connector owns the panel: the ONE build of
    an owned panel's bank, which a read outside a run takes as its newest mint labelled it."""
    # First, never a fallback: rows cached under the same dataset name describe another instrument.
    if connector.extract_experiment is not None:
        if experiment is None:
            raise PayloadInvalidError(
                f"Connector {connector.name!r} owns {dataset.name!r}'s panel, but its definition "
                f"declares no `panel:` and no `source:` builds one.",
                code="pipeline_config_invalid",
            )
        try:
            rows = await connector.extract_experiment(stores, experiment)
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            # Every guard above this seam catches `ValueError`; a raw `KeyError` walks through to a 500.
            raise ValueError(
                f"Connector {connector.name!r} could not read {dataset.name!r}'s panel: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return Bank.of(samples_from_dicts(rows)), ()
    bank = resolve_bank(stores, dataset)
    if not bank.samples:
        raise PayloadInvalidError(
            f"Dataset {dataset.name!r} holds no rows: no bank under its name and no "
            f"`source:` in its definition to cut one from.",
            code="dataset_not_found",
        )
    # Unioned with the ground-truth answers, never replacing them: every label stays rankable.
    return bank, read_answer_terms_file(dataset.directory)


@asynccontextmanager
async def init_services(
    dataset_name: str,
    *,
    identity: IdentityContext,
    stores: Stores | None = None,
    program: object | None = None,
    served: ServedPipeline | None = None,
) -> AsyncIterator[Session]:
    """A session on what *dataset_name*'s definition names: a campaign's own is bound by
    `bind_cycle_session`, on the connector its mint froze. *stores* is the ONE way to relocate the
    tree: the L4 inner runner passes a sandboxed one. *served* is a check-in Start's one backend
    read, under which it landed the definition. Its backend client closes as the block exits."""
    if stores is None:
        stores = build_stores(identity, projects_root=default_projects_root())
    dataset = resolve_dataset(stores, dataset_name)
    connector = runnable_definition(dataset).owner
    async with _session(
        stores, dataset, connector, identity=identity, program=program, served=served
    ) as session:
        yield session


@asynccontextmanager
async def _session(
    stores: Stores,
    dataset: ResolvedDataset,
    connector: Connector,
    *,
    identity: IdentityContext,
    program: object | None = None,
    served: ServedPipeline | None = None,
) -> AsyncIterator[Session]:
    """THE opener of a backend client, and its one closer: every door enters it as a block."""
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
        endpoint=definition.endpoint,
        workload=InProcessWorkload(
            experiment=experiment,
            experiment_dir=None if panel is None else panel.directory,
            program=program,
            workspace=Path(stores.base_dir),
        ),
        jobs_dir=stores.jobs_dir,
    )
    try:
        if served is None:
            served = await served_pipeline(
                stores, connector, endpoint=definition.endpoint, experiment=experiment
            )
        declaration = served.over(definition.pipeline)
        pipeline_schema = parse_pipeline_response(declaration)
        _verify_required_observation_keys(pipeline_schema, connector, dataset_name)
        _verify_observation_names(pipeline_schema, connector, dataset_name)
        backend_revision = await _read_backend_revision(client, connector)
        bank, answer_terms = await session_bank(
            stores, dataset, connector=connector, experiment=experiment
        )
        # By hash, never a label: every answer this session files names a bank the store holds.
        stores.tenant_datasets.put_bank(bank)
        yield Session(
            store=stores,
            backend_client=client,
            pipeline_read=PipelineRead(
                declaration=declaration,
                dataset_overlay=pipeline_node_overlay(definition.pipeline),
                endpoint=definition.endpoint,
            ),
            backend_revision=backend_revision,
            bank=bank,
            answer_terms=answer_terms,
            dataset_name=dataset_name,
            dataset=dataset,
            identity=identity,
        )
    finally:
        await client.aclose()


@asynccontextmanager
async def bind_cycle_session(stores: Stores, at: CampaignCycle) -> AsyncIterator[Session]:
    """Bound, never configured: a resume's drift refusal reads the schema before the config lands.
    Its backend client closes as the block exits."""
    async with _session(
        stores,
        resolve_dataset(stores, at.campaign.dataset_name),
        campaign_connector(at.campaign),
        identity=stores.identity,
    ) as session:
        yield session.bound(at)
