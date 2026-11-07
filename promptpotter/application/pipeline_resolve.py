from __future__ import annotations

import functools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, NamedTuple, assert_never, get_args

from pydantic import ValidationError

from promptpotter import connectors
from promptpotter.application.datasets.authored import (
    campaign_config_over,
    dataset_campaign_block,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import (
    UPLOAD_DEFINITION_KEYS,
    DraftCriterion,
    draft_criterion,
    upload_basis_block,
    upload_pipeline_json,
)
from promptpotter.application.datasets.prompts import (
    declared_origin_prompts,
    has_dataset_prompts,
    pipeline_declared_nodes,
    pipeline_node_overlay,
)
from promptpotter.application.datasets.startability import draft_samples
from promptpotter.application.free_tier import free_fence, free_task_models, with_free_tier_layer
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.coupon import Send
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.optimizers.nodes import MintFacts, admits
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    DatasetPipelineResponse,
    ForkPreviewResponse,
    SteerVerdict,
    optimizer_setup,
)
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.application.scoring.formula.compiler import (
    campaign_terms,
    split_scoring_block,
)
from promptpotter.application.scoring.formula.matchers import matcher_of
from promptpotter.application.scoring.judges.registry import judge_instrument
from promptpotter.connectors.protocol import PROBE_WORKLOAD
from promptpotter.domain.arms import Concluded
from promptpotter.domain.bench import partition_bank, restored_partition
from promptpotter.domain.campaign import CampaignRunsWith, RunsWithParam, VendorModels
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    CycleConfig,
    effective_config,
    scoring_locked,
)
from promptpotter.domain.checkin_draft import DatasetBasis, DraftFile, UploadBasis
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.opt_search_point import render_prompt
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import (
    apply_node_overlay,
    fold_output_contract,
    node_config_items,
    normalize_model_id,
    permitted_models_from_narrowing,
    steers_disallowed_model,
)
from promptpotter.domain.pipeline_parsing import (
    merge_node_blocks,
    parse_pipeline_response,
    parse_resolved_schema,
)
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    OUTPUT_SCHEMA_KEY,
    SCHEMA_TOGGLE_PARAM,
    CapabilityMenu,
    NestedPipelineRef,
    NodeConfigParam,
    NodeOutputSchema,
    NodeSearchNarrowing,
    ParamLayer,
    ParamSource,
    reach_map,
)
from promptpotter.domain.run_records import (
    DefaultsDrift,
    DefaultsPin,
    PipelineRead,
    ScoringLockedRecord,
)
from promptpotter.domain.scoring import ScoringFormulaError
from promptpotter.domain.search_point import missing_template_vars, strip_rendered_prompt
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import resolve_menu, resolve_schema_menu
from promptpotter.infrastructure.llm.pricing import route_priced
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    dataset_connector,
    dataset_experiment,
    declared_pipelines,
    resolve_dataset,
    runnable_definition,
)
from promptpotter.infrastructure.store.layout import CampaignLayout
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.infrastructure.store.stores import descend_store, owned_campaign
from promptpotter.shared.errors import (
    BadRequestError,
    DatasetAccessError,
    FileKind,
    FileUnreadableError,
    LedgerUnreadableError,
    PayloadInvalidError,
    validation_reason,
)
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import CAMPAIGN_BABYSIT_CAP, has_capability

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.config.prompt_blocks import PackShelf
    from promptpotter.connectors.protocol import Connector
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.judges import JudgeSpec
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.prompt_block import Presumption
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.spend import Budget
    from promptpotter.domain.treatment import Treatment
    from promptpotter.infrastructure.store.campaign_store.ledger_scan import ResolvedRun
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key the judges' fingerprint rides into measurement identity — ONE key over the
whole term → judge mapping. Twin of `connectors/harbor.py::INSTRUMENT_KEY`, named apart because
that one says which task bytes ran and this one which grader read the answer. Both are identity,
neither a tunable — nothing may put either in `param_keys`."""

PARAM_LAYERS: tuple[ParamLayer, ...] = get_args(ParamLayer)


@dataclass(frozen=True)
class FoldedParams:
    params: dict[str, Any]
    provenance: dict[str, dict[str, ParamSource]]

    @property
    def identity_keys(self) -> dict[str, frozenset[str]]:
        return {
            node: owned
            for node, stamps in self.provenance.items()
            if (owned := frozenset(k for k, source in stamps.items() if source == "identity"))
        }


@dataclass(frozen=True)
class ParamLayers:
    """What a subject's nodes run with, as ONE fold over `PARAM_LAYERS` in their declared order.
    A subject picks its layers by the data it lays: an empty layer lays nothing. Only the chain's
    steps are laid; the floors are read off the params laid before them."""

    steps: list[str]
    schema: PipelineSchema
    workspace: Path | None
    dataset: Mapping[str, Any] = field(default_factory=dict)
    campaign: Mapping[str, Any] = field(default_factory=dict)
    seed: Mapping[str, Any] = field(default_factory=dict)
    identity: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    evolved: Mapping[str, Any] = field(default_factory=dict)
    # A measured point's params: the identity it ran under stands over the one recomputed today.
    recorded: Mapping[str, Any] = field(default_factory=dict)

    def fold(self) -> FoldedParams:
        params: dict[str, Any] = {"steps": list(self.steps)}
        provenance: dict[str, dict[str, ParamSource]] = {}
        for source in PARAM_LAYERS:
            laid = {
                node: dict(cfg)
                for node, cfg in node_config_items(dict(self._layer(source, params)))
                if node in self.steps
            }
            params = apply_node_overlay(
                params, laid, self.schema, source=source, provenance=provenance
            )
        return FoldedParams(params, provenance)

    def _layer(self, source: ParamLayer, params: dict[str, Any]) -> Mapping[str, Any]:
        match source:
            case "dataset":
                return self.dataset
            case "campaign":
                return self.campaign
            case "seed":
                return self.seed
            case "model_floor":
                return self._floors(params)
            case "identity":
                return self._identity()
            case "evolved":
                return self.evolved
            case _:
                assert_never(source)

    def _identity(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for node, cfg in self.identity.items():
            ran = self.recorded.get(node)
            ran = ran if isinstance(ran, dict) else {}
            out[node] = {k: ran.get(k, v) for k, v in cfg.items()}
        return out

    def _floors(self, params: dict[str, Any]) -> dict[str, dict[str, Any]]:
        answering = schema_as_run(self.schema, params, self.steps, self.workspace)
        floors: dict[str, dict[str, Any]] = {}
        for name in self.steps:
            node = answering.get_node(name)
            cfg = params.get(name)
            if node is None or not isinstance(cfg, dict) or "reasoning_effort" in cfg:
                continue
            if "reasoning_effort" not in node.param_keys | node.param_keys_held:
                continue
            floor = answering.effort_floor(node, model=None)
            if floor is not None:
                floors[name] = {"reasoning_effort": floor}
        return floors


def _routed_as_run(
    schema: PipelineSchema, pipeline_params: dict[str, Any], active: list[str]
) -> PipelineSchema:
    running = {
        name: route
        for name in active
        if (
            route := {
                key: value
                for key in ("provider", "model")
                if isinstance(value := (pipeline_params.get(name) or {}).get(key), str) and value
            }
        )
    }
    if not running:
        return schema
    return schema.model_copy(
        update={
            "declared_nodes": [
                n.model_copy(update={"current_config": {**n.current_config, **running[n.name]}})
                if n.name in running
                else n
                for n in schema.declared_nodes
            ]
        }
    )


def schema_as_run(
    schema: PipelineSchema,
    pipeline_params: dict[str, Any],
    active: list[str],
    workspace: Path | None,
) -> PipelineSchema:
    routed = _routed_as_run(schema, pipeline_params, active)
    answered = schema.model_capabilities
    fresh = resolve_menu(
        sorted(
            (n.provider, model)
            for n in routed.declared_nodes
            if n.name in active
            and isinstance(model := n.current_config.get("model"), str)
            and model not in answered.get(n.provider, {})
        ),
        workspace=workspace,
    )
    if not fresh:
        return routed
    return routed.model_copy(
        update={
            "model_capabilities": {
                provider: {**answered.get(provider, {}), **fresh.get(provider, {})}
                for provider in {*answered, *fresh}
            }
        }
    )


def overlay_dataset_pipeline(served: Mapping[str, Any], local: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(served)
    if pipelines := declared_pipelines(local):
        out["pipelines"] = pipelines
    # The operator's model MENU bounds the L1 enum; dropped, the service's catalogue is searched.
    if local.get("available_models"):
        out["available_models"] = local["available_models"]
    out["nodes"] = merge_node_blocks(out.get("nodes") or {}, local.get("nodes") or {})
    return out


@dataclass(frozen=True)
class ServedPipeline:
    """What a connector serves of its pipeline; each reader lays its own local definition on it."""

    connector: str
    # `None`: the connector serves none, so the local definition is the whole of it.
    doc: Mapping[str, Any] | None

    def over(self, local: Mapping[str, Any]) -> dict[str, Any]:
        return dict(local) if self.doc is None else overlay_dataset_pipeline(self.doc, local)


async def served_pipeline(
    stores: Stores, connector: Connector, *, experiment: Mapping[str, Any] | None
) -> ServedPipeline:
    """Read live at every moment that needs it; raises ``BackendUnreachableError``, never a stand-in."""
    served: Mapping[str, Any] | None = None
    if connector.serves_pipeline:
        client = BackendClient(connector, workload=PROBE_WORKLOAD, jobs_dir=stores.jobs_dir)
        try:
            served = await client.fetch_pipeline()
        finally:
            await client.aclose()
    elif connector.pipeline_declaration is not None:
        served = connector.pipeline_declaration(stores, experiment)
    return ServedPipeline(connector.name, served)


def _draft_dataset(stores: Stores, draft: DraftFile) -> ResolvedDataset | None:
    match draft.basis:
        case UploadBasis():
            return None
        case DatasetBasis(dataset=name):
            return resolve_dataset(stores, name)
        case _:
            assert_never(draft.basis)


def _draft_local(stores: Stores, draft: DraftFile) -> dict[str, Any]:
    """What run init lays over the served pipeline once Start lands: an upload's definition, the
    draft's definition half (`UPLOAD_DEFINITION_KEYS`) laid in, which its Start writes; a reused
    dataset's own file."""
    match draft.basis:
        case UploadBasis() as upload:
            half = campaign_config_over(upload_basis_block(upload), draft.config)
            return upload_pipeline_json(upload, draft.slug, half.pipeline_overlay)
        case DatasetBasis(dataset=name):
            return runnable_definition(resolve_dataset(stores, name)).pipeline
        case _:
            assert_never(draft.basis)


def draft_backend_type(stores: Stores, draft: DraftFile) -> str:
    """The backend the draft's Start runs: an upload's pick, else the one its definition names."""
    match draft.basis:
        case UploadBasis(connector=connector):
            return connector
        case DatasetBasis(dataset=name):
            return runnable_definition(resolve_dataset(stores, name)).backend_type
        case _:
            assert_never(draft.basis)


async def draft_served(stores: Stores, draft: DraftFile) -> ServedPipeline:
    return await served_pipeline(
        stores,
        connectors.CONNECTORS.get(draft_backend_type(stores, draft)),
        experiment=experiment_outside_run(stores, _draft_dataset(stores, draft)),
    )


async def dataset_pipeline(
    stores: Stores, dataset: ResolvedDataset, *, experiment: Mapping[str, Any] | None
) -> dict[str, Any]:
    """A dataset's pipeline outside a run, over the experiment its panel holds today."""
    definition = runnable_definition(dataset)
    connector = connectors.CONNECTORS.get(definition.backend_type)
    served = await served_pipeline(stores, connector, experiment=experiment)
    return served.over(definition.pipeline)


def experiment_outside_run(
    stores: Stores, dataset: ResolvedDataset | None
) -> Mapping[str, Any] | None:
    if dataset is None:
        return None
    connector = dataset_connector(dataset)
    read = None if connector is None else dataset_experiment(stores, dataset, connector)
    return None if read is None else read.experiment


def identity_layer(
    stores: Stores | None,
    connector: Connector | None,
    experiment: Mapping[str, Any] | None,
    judges: Mapping[str, JudgeSpec] | None,
    active: list[str],
) -> dict[str, dict[str, Any]]:
    """The connector's and the judges' ONE channel into the archive key."""
    out: dict[str, dict[str, Any]] = {}
    if connector is not None and connector.identity_config is not None:
        if stores is None:
            raise ValueError(f"{connector.name}: its identity needs the stores")
        out.update(connector.identity_config(stores, experiment))
    if judges and active:
        # The TERMINAL step: a judge grades the answer that node emits.
        out.setdefault(active[-1], {})[JUDGE_INSTRUMENT_KEY] = judge_instrument(judges)
    return out


def _resolve_active_schema(
    pipeline_schema: PipelineSchema,
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
    declared: frozenset[str],
    workspace: Path | None = None,
) -> tuple[list[str], PipelineSchema]:
    active = pipeline_schema.active_steps_excluding(exclude)

    filtered = pipeline_schema
    if exclude:
        filtered = pipeline_schema.filter_to_steps(active)
    # Declared UNION the running chain: `promptpotter-self/panel` declares `l2_context` / `l3_plan`
    # off-chain on purpose, and a dataset declaring fewer nodes than it runs must lose none.
    if declared:
        keep = declared | set(filtered.active_steps)
        filtered = filtered.filter_to_steps(sorted(keep))
    if narrowing:
        filtered = filtered.narrow(narrowing)
    # Every run keys its cells with the pins, workspace or not.
    filtered = filtered.model_copy(update={"default_routes": default_routes()})
    # LAST, over the narrowed schema, so `selectable_routes` reads the nodes as the run holds them.
    if workspace is not None:
        filtered = filtered.model_copy(
            update={"model_capabilities": resolve_schema_menu(filtered, workspace=workspace)}
        )
    return active, filtered


def _apply_starting_prompts(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset: ResolvedDataset,
    dataset_name: str,
) -> None:
    """Assumes the caller checked ``has_dataset_prompts``."""
    prompt_nodes = [n for n in filtered.prompt_nodes() if n.name in active]
    authored = declared_origin_prompts(dataset, prompt_nodes)
    for pnode in prompt_nodes:
        if pnode.name not in authored:
            continue
        assert pnode.prompt_info is not None
        rendered = render_prompt(authored[pnode.name].fields, pnode.prompt_fields)
        declared = pnode.prompt_info.template_variables
        missing = missing_template_vars(rendered, declared, fields=pnode.prompt_fields)
        if missing:
            raise PayloadInvalidError(
                f"Dataset {dataset_name!r} prompt for node {pnode.name!r} is missing required "
                f"template variables {missing} — the backend injects these by literal "
                f"{{{{name}}}} substitution, so without them the query / research / output "
                f"schema never reach the model. Add the placeholders to "
                f"{dataset_name}'s origin.yaml under `{pnode.name}` "
                f"(node declares: {declared}).",
                code="pipeline_config_invalid",
            )
        pipeline_params.setdefault(pnode.name, {})["prompt"] = rendered
        logger.info("Starting prompt: %s origin.yaml[%s]", dataset_name, pnode.name)


def _validate_prompt_reach(
    *,
    filtered: PipelineSchema,
    active: list[str],
    has_starting_prompts: bool,
    prompt_fields_as_node_params: bool,
    dataset_name: str,
) -> None:
    if any(n.name in active for n in filtered.prompt_nodes()):
        return
    if has_starting_prompts:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r} ships starting prompts but no active node in {active} "
            f"declares `prompt_info`, so the rendered prompt is dropped before the wire and "
            f"every cell runs with an empty system prompt. An LLM node must declare it, in "
            f"GET /pipeline or in the dataset's `dataset.yaml::pipeline`.",
            code="pipeline_config_invalid",
        )
    if not prompt_fields_as_node_params:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r}: no active node in {active} declares `prompt_info`, and "
            f"this backend sends a node only the rendered prompt, so every candidate scores as "
            f"the same no-skill call and the round reports a tie it never measured. Declare "
            f"`prompt_info` with its `fields` on the node the prompt is meant for.",
            code="pipeline_config_invalid",
        )


def _validate_model_ownership(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset_name: str,
) -> None:
    for name in active:
        node_obj = filtered.get_node(name)
        if node_obj and node_obj.is_llm and not pipeline_params.get(name, {}).get("model"):
            raise PayloadInvalidError(
                f"dataset {dataset_name!r}: LLM node {name!r} has no owned model. "
                f"Declare it in the dataset's dataset.yaml::pipeline.nodes.{name}.config.model "
                f"— the dataset owns its task model, never the backend default.",
                code="pipeline_config_invalid",
            )


def _evolved_overlay(stores: Stores, at: Subject) -> tuple[dict[str, Any], dict[str, Any]]:
    """(sparse delta, complete config): the complete one read as a delta stamps every param ``evolved``."""
    if at.kind != "candidate" or not at.cycle_id:
        return {}, {}
    hop = CycleHop(campaign_id=at.campaign_id, cycle_id=at.cycle_id)
    # Newest round first: a re-measured point answers from the round that measured it last.
    for held in reversed(stores.campaigns.standing_rounds(hop, moment=None).rounds.values()):
        arm = held.close.arms.get(ArmKey(at.candidate_id))
        if isinstance(arm, Concluded):
            return dict(arm.overlay), dict(arm.resolved_params)
    return {}, {}


def _nests(schema: PipelineSchema) -> list[NestedPipelineRef]:
    """One per dataset a gateway's measurement runs, as the served declaration names it."""
    refs = [
        NestedPipelineRef(node=node.name, dataset=dataset)
        for node in schema.declared_nodes
        for dataset in node.nested_datasets
    ]
    return sorted(refs, key=lambda ref: (ref.dataset, ref.node))


def resolved_output_schemas(
    schema: PipelineSchema, params: Mapping[str, Any]
) -> dict[str, NodeOutputSchema | None]:
    """Folded as the WIRE folds it, never the declaration; ``None`` is a point that answers as text."""
    configs = dict(node_config_items(dict(params)))
    folded = fold_output_contract(configs, schema)
    out: dict[str, NodeOutputSchema | None] = {}
    for node, declared in schema.node_output_schemas().items():
        if configs.get(node, {}).get(SCHEMA_TOGGLE_PARAM) == ANSWER_AS_TEXT:
            out[node] = None
            continue
        js = folded.get(node, {}).get("output_schema")
        out[node] = (
            parse_resolved_schema({"json_schema": js}) if isinstance(js, dict) and js else declared
        )
    return out


def draft_config(stores: Stores, draft: DraftFile, declared: PipelineSchema) -> CampaignConfig:
    """What Start declares and every read before it shows, over *declared*: the draft's chain
    (`DraftPipeline.declared`). A free account's takes the tier's layer last, bound to each node's model."""
    config = draft_declared(stores, draft)
    fence = free_fence(stores, draft_backend_type(stores, draft))
    return config if fence is None else with_free_tier_layer(config, declared, fence)


def draft_budget(stores: Stores, draft: DraftFile) -> Budget:
    """The ceiling a Start admits: no node bears on it, so it reads no pipeline."""
    config = draft_declared(stores, draft)
    fence = free_fence(stores, draft_backend_type(stores, draft))
    if fence is not None:
        config = with_free_tier_layer(config, None, fence)
    return config.optimization.budget


def draft_sends(stores: Stores, draft: DraftFile) -> tuple[Send, ...]:
    """What a Start sends, and who pays each, off its declaration: it reads no pipeline."""
    return sends_on(stores, draft_declared(stores, draft), draft_backend_type(stores, draft))


def draft_declared(stores: Stores, draft: DraftFile) -> CampaignConfig:
    """The campaign half of ``draft.config``, laid over its basis: an upload's connector defaults,
    its definition's half (`UPLOAD_DEFINITION_KEYS`) left to `_draft_local`; a reused dataset's
    own block, every key. A draft's ``scoring`` is the criterion whole, so it replaces the
    basis's."""
    partial = draft.config
    match draft.basis:
        case UploadBasis() as upload:
            basis = upload_basis_block(upload)
            partial = {k: v for k, v in partial.items() if k not in UPLOAD_DEFINITION_KEYS}
        case DatasetBasis(dataset=name):
            basis = dataset_campaign_block(resolve_dataset(stores, name))
        case _:
            assert_never(draft.basis)
    if "scoring" in partial:
        basis = {k: v for k, v in basis.items() if k != "scoring"}
    return campaign_config_over(basis, partial)


def defaults_drift(campaign: Campaign, *, running: Treatment) -> DefaultsDrift | None:
    """The one answer to whether what the declaration is a delta over moved since its mint."""
    declared = DefaultsPin(
        bench=campaign.declaration.bench_defaults_pin, treatment=campaign.treatment
    )
    now = DefaultsPin(bench=bench_defaults_pin(), treatment=running)
    return None if now == declared else DefaultsDrift(declared=declared, running=now)


def cycle_config(stores: Stores, campaign: Campaign, hop: CycleHop | None) -> CycleConfig:
    """The declaration's effective config under every seed of *hop*'s line, the criterion its dials
    lock on the origin laid on; ``None`` is the declaration alone, which no cycle's seed moved."""
    try:
        declared = effective_config(campaign.declaration)
    except ValidationError as exc:
        raise FileUnreadableError(
            FileKind.MANIFEST,
            path=str(
                CampaignLayout(stores.campaigns.campaign_root_dir(campaign.campaign_id)).manifest
            ),
            reason=validation_reason(exc, shown=1),
        ) from exc
    campaigns = stores.campaigns
    if hop is None:
        root_lock = campaigns.last(campaign.root_hop, ScoringLockedRecord)
        return CycleConfig.laid(scoring_locked(declared, root_lock), [])
    return CycleConfig.laid(declared, campaigns.read_laid_spans(hop))


@dataclass(frozen=True)
class _Merge:
    declared: PipelineSchema
    filtered: PipelineSchema
    active: list[str]
    cfg: CampaignConfig
    layers: ParamLayers
    # The free tier fences the target's nodes: it is offered only the tier's task models.
    free: bool

    @functools.cached_property
    def optimizer(self) -> SelectedOptimizer:
        """`cfg`'s one selection, made where a read takes it: one that only folds the params
        (`_runs_with`) stands on a config whose overlay no longer selects."""
        return select_optimizer(self.cfg.optimization)


@dataclass(frozen=True)
class _CampaignMerge(_Merge):
    hop: CycleHop
    # The panel its mint recorded run init measured.
    experiment: dict[str, Any] | None


@dataclass(frozen=True)
class _DraftMerge(_Merge):
    # The definition the check-in lays over the served pipeline, which an upload's Start writes.
    local: dict[str, Any]


def _draft_merge(stores: Stores, draft: DraftFile, served: ServedPipeline) -> _DraftMerge:
    local = _draft_local(stores, draft)
    declared = parse_pipeline_response(served.over(local))
    cfg = draft_config(stores, draft, declared)
    active, filtered = _resolve_active_schema(
        declared,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        declared=pipeline_declared_nodes(local),
    )
    return _DraftMerge(
        # What was on OFFER before this draft narrowed it, so a menu cannot shrink to its own pick.
        declared=declared,
        filtered=filtered,
        active=active,
        cfg=cfg,
        layers=ParamLayers(
            steps=active,
            schema=filtered,
            workspace=stores.base_dir,
            dataset=pipeline_node_overlay(local),
            campaign=cfg.pipeline_overlay,
            identity=identity_layer(None, None, None, cfg.judges, active),
        ),
        free=_task_fenced(stores, served.connector),
        local=local,
    )


def _task_fenced(stores: Stores, connector: str) -> bool:
    fence = free_fence(stores, connector)
    return fence is not None and fence.task


def _campaign_merge(stores: Stores, campaign: Campaign, at: Subject) -> _CampaignMerge:
    """Off its mint's records alone, so a campaign outlives its dataset dir; lays no identity:
    what a point is keyed by is the subject's to add."""
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=at.cycle_id or campaign.root_cycle_id)
    resolved = stores.campaigns.read_resolved_run(hop)
    read = _recorded_read(resolved, hop)
    schema = parse_pipeline_response(read.declaration)
    cycle = cycle_config(stores, campaign, hop)
    cfg = cycle.config
    active, filtered = _resolve_active_schema(
        schema,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        declared=read.declared_nodes,
    )
    return _CampaignMerge(
        declared=schema,
        filtered=filtered,
        active=active,
        cfg=cfg,
        layers=ParamLayers(
            steps=active,
            schema=filtered,
            workspace=stores.base_dir,
            dataset=read.dataset_overlay,
            campaign=cfg.pipeline_overlay,
            seed=cycle.seed_overlay,
        ),
        free=_task_fenced(stores, campaign.connector),
        hop=hop,
        experiment=resolved.experiment,
    )


def recorded_schema(stores: Stores, hop: CycleHop) -> PipelineSchema:
    """What *hop* ran, as its mint recorded run init read it; never today's files or backend."""
    return parse_pipeline_response(
        _recorded_read(stores.campaigns.read_resolved_run(hop), hop).declaration
    )


def _recorded_read(resolved: ResolvedRun, hop: CycleHop) -> PipelineRead:
    if resolved.read is None:
        raise LedgerUnreadableError(
            ledger=f"campaigns/{hop.campaign_id}/cycles/{hop.cycle_id}",
            offset=None,
            reason="it holds no resolved run, which its mint appends before the campaign exists",
            campaign_id=hop.campaign_id,
        )
    return resolved.read


def _served_menu(m: _Merge, params: dict[str, Any], workspace: Path | None) -> CapabilityMenu:
    return resolve_schema_menu(_routed_as_run(m.filtered, params, m.active), workspace=workspace)


def campaign_admits(cfg: CampaignConfig, schema: PipelineSchema) -> frozenset[Presumption]:
    """What a campaign's chain and scorer let a block presume: its pools draw and its treatment hashes this one set."""
    per_sample = split_scoring_block(cfg.scoring, judge_instrument=None).per_sample
    return admits(schema, matcher_of(per_sample))


def mint_facts(
    cfg: CampaignConfig, demo: Sequence[Sample], schema: PipelineSchema, shelf: PackShelf
) -> MintFacts:
    return MintFacts(demo, campaign_admits(cfg, schema), shelf)


def _draft_mint(stores: Stores, draft: DraftFile, m: _Merge) -> MintFacts | None:
    """The demo split the draft's Start will draw, so its pool offers the keys before Start."""
    samples = draft_samples(stores, draft)
    if samples is None:
        return None
    demo = partition_bank(samples, m.cfg.dataset_split, seed=m.cfg.run_seed).demo
    return mint_facts(m.cfg, demo, m.filtered, stores.block_packs)


def _cycle_mint(stores: Stores, m: _CampaignMerge) -> MintFacts | None:
    drawn = stores.campaigns.read_bank_partition(m.hop)
    bank = None if drawn is None else stores.tenant_datasets.bank(drawn.dataset_hash)
    if drawn is None or bank is None:
        return None
    return mint_facts(
        m.cfg, restored_partition(bank.samples, drawn).demo, m.filtered, stores.block_packs
    )


def _rows(m: _Merge, folded: FoldedParams) -> dict[str, list[NodeConfigParam]]:
    return m.filtered.node_config_schema(
        values={n: c for n, c in folded.params.items() if isinstance(c, dict)},
        sources=folded.provenance,
        model_menu=_admitted_menu(m),
        declared=m.declared,
    )


def _admitted_menu(m: _Merge) -> list[str]:
    """Never offers a model the Start would refuse: one no rate prices under a dollar ceiling,
    or one the free tier does not list for a free account."""
    models = m.filtered.selectable_models()
    if m.free:
        models = [model for model in models if model in free_task_models()]
    providers = {n.provider for n in m.filtered.declared_nodes if n.provider}
    if not providers or m.cfg.optimization.budget.usd is None:
        return models
    return [
        model for model in models if any(route_priced(model, p) is not False for p in providers)
    ]


def _response(
    m: _Merge,
    folded: FoldedParams,
    workspace: Path,
    *,
    campaign_id: str,
    cycle_id: str,
    dataset_name: str,
    connector: str,
    mint: MintFacts | None,
) -> CampaignPipelineResponse:
    rows = _rows(m, folded)
    return CampaignPipelineResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
        connector=connector,
        self_optimization=is_self_optimization(connector),
        cell_terms=list(connectors.CONNECTORS.get(connector).cell_terms),
        optimizer=m.cfg.optimization.optimizer,
        optimizer_setup=optimizer_setup(m.optimizer, mint, m.filtered, workspace),
        params=folded.params,
        node_config_schema=rows,
        view=m.filtered.view,
        node_output_schema=resolved_output_schemas(m.filtered, folded.params),
        model_capabilities=_served_menu(m, folded.params, workspace),
        reach=reach_map(rows),
        nests=_nests(m.declared),
        is_single_node=m.filtered.is_single_node,
    )


def resolve_pipeline_for_draft(
    stores: Stores, draft: DraftFile, served: ServedPipeline
) -> CampaignPipelineResponse:
    m = _draft_merge(stores, draft, served)
    return _response(
        m,
        m.layers.fold(),
        stores.base_dir,
        # A draft is no campaign and runs no cycle until its Start mints both.
        campaign_id=draft.draft_id,
        cycle_id="",
        dataset_name=draft.slug,
        connector=served.connector,
        mint=_draft_mint(stores, draft, m),
    )


@dataclass(frozen=True)
class DraftPipeline:
    """The draft's pipeline before Start commits any of it: what its nodes run with, their rows,
    the chain they run in, the chain as declared before the draft narrowed it, its config (the
    campaign half), the definition it lays (an upload's Start writes it), and the backend that
    serves it."""

    params: dict[str, Any]
    rows: dict[str, list[NodeConfigParam]]
    chain: PipelineSchema
    declared: PipelineSchema
    config: CampaignConfig
    local: dict[str, Any]
    backend_type: str

    @functools.cached_property
    def optimizer(self) -> SelectedOptimizer:
        """`config`'s selection, made only where a read takes it: a draft whose overlay no
        longer selects still folds, rows and gates."""
        return select_optimizer(self.config.optimization)

    @functools.cached_property
    def criterion(self) -> DraftCriterion:
        """``config.scoring`` as a check-in picks it, its dials over this backend's terms."""
        return draft_criterion(self.config.scoring, campaign_terms(self.backend_type))


def draft_pipeline(stores: Stores, draft: DraftFile, served: ServedPipeline) -> DraftPipeline:
    m = _draft_merge(stores, draft, served)
    folded = m.layers.fold()
    return DraftPipeline(
        folded.params,
        _rows(m, folded),
        m.filtered,
        m.declared,
        m.cfg,
        m.local,
        served.connector,
    )


def resolve_pipeline_for_campaign(
    stores: Stores,
    campaign: Campaign,
    *,
    at: Subject,
) -> CampaignPipelineResponse:
    """``stores`` is already the store *campaign* lives in: the caller descends ``at.inside`` first."""
    m = _campaign_merge(stores, campaign, at)
    evolved, measured = _evolved_overlay(stores, at)
    folded = replace(
        m.layers,
        # The recorded roster alone: a live resolve recomputes a banked identity off one it never ran.
        identity=identity_layer(
            stores,
            connectors.CONNECTORS.get(campaign.connector),
            m.experiment,
            m.cfg.judges,
            m.active,
        ),
        evolved=evolved,
        recorded=measured,
    ).fold()
    return _response(
        m,
        folded,
        stores.base_dir,
        campaign_id=campaign.campaign_id,
        cycle_id=m.hop.cycle_id,
        dataset_name=campaign.dataset_name,
        connector=campaign.connector,
        mint=_cycle_mint(stores, m),
    )


def _pipeline_subject(at: str, campaign_id: str) -> Subject:
    if not at:
        return Subject(campaign_id)
    spec = Subject.parse(at)
    if spec.lens or spec.samples:
        raise BadRequestError(
            f"Subject {at!r} carries a scoring mask, and a mask cannot change what config a "
            "point RAN. Drop `lens=` / `samples=` — this read is configuration, not scoring."
        )
    if spec.campaign_id != campaign_id:
        raise BadRequestError(
            f"Subject {at!r} addresses campaign {spec.campaign_id!r} but the path names "
            f"{campaign_id!r} — one request, one subject."
        )
    return spec


def resolve_pipeline_at(stores: Stores, campaign_id: str, at: str) -> CampaignPipelineResponse:
    spec = _pipeline_subject(at, campaign_id)
    # An inner searchpoint lives in the sandbox `;in=` names; the tenant tree answers for the OUTER.
    leaf = descend_store(stores, spec.inside)
    return resolve_pipeline_for_campaign(leaf, owned_campaign(leaf, campaign_id), at=spec)


def permitted_narrowing(
    stores: Stores, campaign: Campaign, hop: CycleHop | None = None
) -> dict[str, NodeSearchNarrowing]:
    """What each node permits at *hop* (the root's, ``None``): its recorded schema's value spaces
    narrowed by the line, never the campaign's own layer alone, which holds none of the dataset's
    optimizer blocks. A steer outside them is the babysit act (decisions.md § 0005)."""
    at = Subject(campaign.campaign_id) if hop is None else Subject.of((hop,))
    return {
        n.name: NodeSearchNarrowing(param_allowed_values=dict(n.param_allowed_values))
        for n in _campaign_merge(stores, campaign, at).filtered.declared_nodes
        if n.param_allowed_values
    }


def steer_verdict(
    stores: Stores, narrowing: Mapping[str, NodeSearchNarrowing], overlay: dict[str, Any] | None
) -> SteerVerdict:
    """The babysit act (decisions.md § 0005): a steer outside what the node permits needs `campaign.babysit`."""
    if not steers_disallowed_model(narrowing, overlay):
        return "clean"
    return "babysat" if has_capability(stores.identity, CAMPAIGN_BABYSIT_CAP) else "refused"


def fork_preview(stores: Stores, campaign_id: str, overlay: dict[str, Any]) -> ForkPreviewResponse:
    narrowing = permitted_narrowing(stores, owned_campaign(stores, campaign_id))
    return ForkPreviewResponse(
        steer=steer_verdict(stores, narrowing, overlay),
        permitted=permitted_models_from_narrowing(narrowing),
    )


async def resolve_pipeline_for_dataset(
    stores: Stores, dataset_name: str
) -> DatasetPipelineResponse:
    dataset = resolve_dataset(stores, dataset_name)
    experiment = experiment_outside_run(stores, dataset)
    declared = await dataset_pipeline(stores, dataset, experiment=experiment)
    cfg = load_dataset_campaign_config(dataset)
    definition = runnable_definition(dataset)
    schema = parse_pipeline_response(declared)
    _, filtered = _resolve_active_schema(
        schema,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        declared=pipeline_declared_nodes(definition.pipeline),
    )
    rows = filtered.node_config_schema()
    return DatasetPipelineResponse(
        connector=definition.backend_type,
        view=filtered.view,
        node_config_schema=rows,
        reach=reach_map(rows),
        nests=_nests(schema),
    )


# A prompt, a description and a nested value are no setting one line can print.
_RUNS_WITH_KINDS = frozenset({"model", "enum", "number", "bool", "string"})
# The typed refusals a root's own data raises; any other exception is a defect and propagates.
_ROOT_UNRESOLVED = (
    DatasetAccessError,
    FileUnreadableError,
    LedgerUnreadableError,
    PayloadInvalidError,
    ScoringFormulaError,
)


def campaign_runs_with(stores: Stores, campaign: Campaign) -> CampaignRunsWith | None:
    """``None`` is a root whose pipeline did not resolve: a broken dataset drops its own row only."""
    try:
        key = _root_key(stores, campaign)
    except _ROOT_UNRESOLVED:
        logger.exception("runs_with: root of %s did not resolve", campaign.campaign_id)
        return None

    def resolved() -> CampaignRunsWith | None:
        try:
            return _runs_with(stores, campaign)
        except _ROOT_UNRESOLVED:
            logger.exception("runs_with: root of %s did not resolve", campaign.campaign_id)
            return None

    return derived(
        ("runs_with", stores.campaigns.cycle_dir(campaign.root_hop)), sig=key, compute=resolved
    )


class LineConfig(NamedTuple):
    """``config`` ``None`` serves a row's config-bound readings null, never a failed list."""

    config: CampaignConfig | None
    # Why it did not resolve; ``None`` where it did, or names no formula and so grades nothing.
    unread: str | None


def line_config(stores: Stores, campaign: Campaign, hop: CycleHop) -> LineConfig:
    """Compiles no scorer: a formula's own refusal is the bench's, the one reading that grades."""
    try:
        config = cycle_config(stores, campaign, hop).config
        if not split_scoring_block(config.scoring, judge_instrument=None).per_sample:
            return LineConfig(None, None)
    except _ROOT_UNRESOLVED as exc:
        return LineConfig(None, str(exc))
    return LineConfig(config, None)


def _root_key(stores: Stores, campaign: Campaign) -> tuple[Any, ...]:
    # VALUES, never the ledger's stat: a running root appends there on every sample.
    cycle = cycle_config(stores, campaign, campaign.root_hop)
    read = stores.campaigns.read_resolved_run(campaign.root_hop).read
    return (
        stable_hash(cycle.config.model_dump(mode="json")),
        stable_hash(cycle.seed_overlay),
        campaign.root_cycle_id,
        stable_hash(
            None
            if read is None
            else [read.declaration, read.dataset_overlay, sorted(read.declared_nodes)]
        ),
    )


def _runs_with(stores: Stores, campaign: Campaign) -> CampaignRunsWith:
    m = _campaign_merge(stores, campaign, Subject(campaign.campaign_id))
    # What the root SENDS: no identity layer, which keys a cell and is never sent.
    folded = m.layers.fold()
    stripped = strip_rendered_prompt(folded.params)
    nodes = {n.name: n for n in m.filtered.declared_nodes}
    out: list[RunsWithParam] = []
    by_vendor: dict[str, list[str]] = {}
    for name in m.active:
        if name not in stripped:
            continue
        cfg = stripped[name]
        for key in sorted(set(cfg) - {OUTPUT_SCHEMA_KEY}):
            kind = nodes[name].param_kind(key)
            if kind not in _RUNS_WITH_KINDS:
                continue
            out.append(
                RunsWithParam(
                    node=name, key=key, value=cfg[key], source=folded.provenance[name][key]
                )
            )
            if kind == "model" and cfg[key] is not None:
                models = by_vendor.setdefault(_vendor_of(cfg[key]), [])
                if cfg[key] not in models:
                    models.append(cfg[key])
    return CampaignRunsWith(
        params=out,
        vendors=[VendorModels(vendor=v, models=ms) for v, ms in by_vendor.items()],
        optimizer=m.cfg.optimization.optimizer,
        budget_rounds=m.cfg.optimization.budget.rounds,
    )


def _vendor_of(model: str) -> str:
    return normalize_model_id(model).partition("/")[0]


def configure_and_apply_pipeline(session: Session, cycle: CycleConfig) -> Session:
    campaign_config = cycle.config
    exclude = list(campaign_config.exclude_nodes)
    dataset_name = session.measured_dataset
    dataset = session.dataset

    active, filtered = _resolve_active_schema(
        session.pipeline_schema,
        exclude=exclude,
        narrowing=campaign_config.optimizer_narrowing,
        declared=session.pipeline_read.declared_nodes,
        workspace=session.store.base_dir,
    )
    folded = ParamLayers(
        steps=active,
        schema=filtered,
        workspace=session.store.base_dir,
        dataset=session.pipeline_read.dataset_overlay,
        campaign=campaign_config.pipeline_overlay,
        seed=cycle.seed_overlay,
        identity=identity_layer(
            session.store,
            session.backend_client.connector,
            session.backend_client.workload.experiment,
            campaign_config.judges,
            active,
        ),
    ).fold()
    pipeline_params = folded.params

    ships_prompts = dataset is not None and has_dataset_prompts(dataset)
    _validate_prompt_reach(
        filtered=filtered,
        active=active,
        has_starting_prompts=ships_prompts,
        prompt_fields_as_node_params=session.backend_client.connector.prompt_fields_as_node_params,
        dataset_name=dataset_name,
    )

    if dataset is not None and ships_prompts:
        _apply_starting_prompts(
            pipeline_params,
            filtered=filtered,
            active=active,
            dataset=dataset,
            dataset_name=dataset_name,
        )

    _validate_model_ownership(
        pipeline_params, filtered=filtered, active=active, dataset_name=dataset_name
    )
    # Asked here so a channel the connector cannot run stops init, not every cell of the origin.
    session.backend_client.prompt_delivery(pipeline_params)

    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    logger.info("Active nodes: %s%s", ", ".join(active), excl_str)

    return replace(
        session,
        pipeline_schema=schema_as_run(filtered, pipeline_params, active, session.store.base_dir),
        pipeline_params=pipeline_params,
        identity_keys=folded.identity_keys,
    )
