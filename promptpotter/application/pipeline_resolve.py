from __future__ import annotations

import functools
import logging
from collections.abc import Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple, assert_never, get_args

from pydantic import ValidationError

from promptpotter import connectors
from promptpotter.application.datasets.authored import (
    ConfigLayers,
    dataset_campaign_block,
    effective_config,
    load_dataset_campaign_config,
    selected_config,
)
from promptpotter.application.datasets.draft_campaign import (
    UPLOAD_DEFINITION_KEYS,
    DraftCriterion,
    draft_criterion,
    upload_pipeline_json,
)
from promptpotter.application.datasets.prompts import (
    declared_origin_prompts,
    has_dataset_prompts,
    pipeline_node_overlay,
)
from promptpotter.application.datasets.startability import draft_samples
from promptpotter.application.free_tier import (
    FreeFence,
    free_fence,
    free_task_models,
    free_tier_layers,
    free_tier_overlay,
)
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.knobs import ConfigMapResponse, bench_defaults_pin, config_map
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.optimizers.nodes import MintFacts
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    DatasetPipelineResponse,
    ForkPreviewResponse,
    NestedPipeline,
    SteerVerdict,
    optimizer_setup,
)
from promptpotter.application.runner.inner.tasks import is_self_optimization
from promptpotter.application.scoring.formula.compiler import (
    campaign_terms,
    split_scoring_block,
)
from promptpotter.application.scoring.formula.matchers import admits, matcher_of
from promptpotter.application.scoring.judges.registry import judge_instrument
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.connectors.protocol import PROBE_WORKLOAD, Connector
from promptpotter.domain.arms import Concluded
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import Campaign, CampaignRunsWith, RunsWithParam, VendorModels
from promptpotter.domain.campaign_config import (
    CAMPAIGN_RULE,
    CampaignConfig,
    CycleConfig,
    LaidSpan,
)
from promptpotter.domain.checkin_draft import DatasetBasis, DraftFile, UploadBasis
from promptpotter.domain.cycle_paths import CycleHop, Subject
from promptpotter.domain.judges import JudgeSpec
from promptpotter.domain.layer_fold import Layer, fold_layers
from promptpotter.domain.opt_search_point import render_prompt
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import (
    fold_output_contract,
    normalize_model_id,
    permitted_models_from_narrowing,
    steers_disallowed_model,
)
from promptpotter.domain.pipeline_params import (
    NodeCall,
    PipelineParams,
    laid_calls,
    node_rule,
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
    KnobLayer,
    KnobLayers,
    NestedPipelineRef,
    NodeConfigParam,
    NodeOutputSchema,
    NodeSearchNarrowing,
    ParamLayer,
    ParamSource,
    PipelineSchema,
    reach_map,
    running_only,
)
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.run_records import (
    DefaultsDrift,
    DefaultsPin,
    PipelineRead,
    ScoringLockedRecord,
)
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import ScoringFormulaError
from promptpotter.domain.search_point import missing_template_vars, strip_rendered_prompt
from promptpotter.domain.spend import Budget
from promptpotter.domain.treatment import Treatment
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import resolve_menu, resolve_schema_menu
from promptpotter.infrastructure.llm.pricing import remembered_priced
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    ResolvedRun,
    scan_resolved_run,
)
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_READ_ERRORS,
    ResolvedDataset,
    campaign_defaults,
    dataset_connector,
    dataset_experiment,
    declared_pipelines,
    resolve_dataset,
    runnable_definition,
    with_node_templates,
)
from promptpotter.infrastructure.store.layout import CampaignLayout
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.infrastructure.store.stores import Stores, descend_store, owned_campaign
from promptpotter.shared.errors import (
    BadRequestError,
    FileKind,
    FileUnreadableError,
    PayloadInvalidError,
    PotterError,
    validation_reason,
)
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.identity import CAMPAIGN_BABYSIT_CAP, has_capability

logger = logging.getLogger(__name__)

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key the judges' fingerprint rides into measurement identity — ONE key over the
whole term → judge mapping. Twin of `connectors/harbor.py::INSTRUMENT_KEY`, named apart because
that one says which task bytes ran and this one which grader read the answer. Both are identity,
neither a tunable — nothing may put either in `param_keys`."""

PARAM_LAYERS: tuple[ParamLayer, ...] = get_args(ParamLayer)


@dataclass(frozen=True)
class FoldedParams:
    params: PipelineParams
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
    A subject picks its layers by the data it lays: an empty layer lays nothing. Only the running
    set is laid, the chain's steps ride the wire; the floors are read off the params laid before them."""

    schema: PipelineSchema
    workspace: Path | None
    dataset: Mapping[str, NodeCall] = field(default_factory=dict)
    campaign: Mapping[str, NodeCall] = field(default_factory=dict)
    free_tier: Mapping[str, Any] = field(default_factory=dict)
    seed: Mapping[str, Any] = field(default_factory=dict)
    identity: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    evolved: Mapping[str, Any] = field(default_factory=dict)
    # A measured point's params: the identity it ran under stands over the one recomputed today.
    recorded: Mapping[str, Any] = field(default_factory=dict)

    def fold(self) -> FoldedParams:
        layers = [
            Layer(source, functools.partial(self._running, source)) for source in PARAM_LAYERS
        ]
        folded = fold_layers(node_rule(self.schema), layers)
        provenance: dict[str, dict[str, ParamSource]] = {
            node: dict(stamps) for node, stamps in folded.stamped().items()
        }
        return FoldedParams(self._params(folded.document), provenance)

    def _params(self, nodes: Mapping[str, Mapping[str, Any]]) -> PipelineParams:
        return PipelineParams().with_steps(list(self.schema.active_steps)).with_nodes(nodes)

    def _running(self, source: ParamLayer, laid: Mapping[str, Any]) -> dict[str, Any]:
        running = self.schema.running
        layer = self._layer(source, self._params(laid))
        return {node: cfg for node, cfg in layer.items() if node in running}

    def _layer(self, source: ParamLayer, params: PipelineParams) -> Mapping[str, Any]:
        match source:
            case "dataset":
                return laid_calls(self.dataset)
            case "campaign":
                return laid_calls(self.campaign)
            case "free_tier":
                return self.free_tier
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

    def _floors(self, params: PipelineParams) -> dict[str, dict[str, Any]]:
        answering = schema_as_run(self.schema, params, self.workspace)
        floors: dict[str, dict[str, Any]] = {}
        for name in self.schema.running:
            node = answering.get_node(name)
            cfg = params.nodes.get(name)
            if node is None or cfg is None or "reasoning_effort" in cfg.model_fields_set:
                continue
            if "reasoning_effort" not in node.param_keys | node.param_keys_held:
                continue
            floor = answering.effort_floor(node, model=None)
            if floor is not None:
                floors[name] = {"reasoning_effort": floor}
        return floors


def _routed_as_run(schema: PipelineSchema, pipeline_params: PipelineParams) -> PipelineSchema:
    running = {
        name: route
        for name in schema.running
        if (
            route := {
                key: value
                for key in ("provider", "model")
                if (value := getattr(pipeline_params.node(name), key))
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
    pipeline_params: PipelineParams,
    workspace: Path | None,
) -> PipelineSchema:
    routed = _routed_as_run(schema, pipeline_params)
    answered = schema.model_capabilities
    fresh = resolve_menu(
        sorted(
            (n.provider, model)
            for n in routed.running_nodes
            if isinstance(model := n.current_config.get("model"), str)
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
    # A served node the definition's walks leave on none is no node the graph fires: it leaves.
    walked = {n for steps in declared_pipelines(out).values() for n in steps}
    displaced = {n for steps in declared_pipelines(served).values() for n in steps} - walked
    nodes = merge_node_blocks(out.get("nodes") or {}, local.get("nodes") or {})
    out["nodes"] = {n: block for n, block in nodes.items() if n not in displaced}
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
        probe = BackendClient(connector, workload=PROBE_WORKLOAD, jobs_dir=stores.jobs_dir)
        async with aclosing(probe) as client:
            served = await client.fetch_pipeline()
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
            half = ConfigLayers(campaign_defaults(), draft.config).config()
            return upload_pipeline_json(upload, half.pipeline_overlay)
        case DatasetBasis(dataset=name):
            return runnable_definition(resolve_dataset(stores, name)).pipeline
        case _:
            assert_never(draft.basis)


def draft_connector(stores: Stores, draft: DraftFile) -> str:
    """The backend the draft's Start runs: an upload's pick, else the one its definition names."""
    match draft.basis:
        case UploadBasis(connector=connector):
            return connector
        case DatasetBasis(dataset=name):
            return runnable_definition(resolve_dataset(stores, name)).connector
        case _:
            assert_never(draft.basis)


async def draft_served(stores: Stores, draft: DraftFile) -> ServedPipeline:
    return await served_pipeline(
        stores,
        connectors.CONNECTORS.get(draft_connector(stores, draft)),
        experiment=experiment_outside_run(stores, _draft_dataset(stores, draft)),
    )


async def dataset_pipeline(
    stores: Stores, dataset: ResolvedDataset, *, experiment: Mapping[str, Any] | None
) -> dict[str, Any]:
    """A dataset's pipeline outside a run, over the experiment its panel holds today."""
    definition = runnable_definition(dataset)
    connector = connectors.CONNECTORS.get(definition.connector)
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
    chain: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """The connector's and the judges' ONE channel into the archive key."""
    out: dict[str, dict[str, Any]] = {}
    if connector is not None and connector.identity_config is not None:
        if stores is None:
            raise ValueError(f"{connector.name}: its identity needs the stores")
        out.update(connector.identity_config(stores, experiment))
    if judges and chain:
        # The TERMINAL step: a judge grades the answer that node emits.
        out.setdefault(chain[-1], {})[JUDGE_INSTRUMENT_KEY] = judge_instrument(judges)
    return out


def _resolve_active_schema(
    pipeline_schema: PipelineSchema,
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
    workspace: Path | None = None,
) -> PipelineSchema:
    filtered = running_only(pipeline_schema, exclude=exclude)
    if narrowing:
        filtered = filtered.narrow(narrowing)
    # Every run keys its cells with the pins, workspace or not.
    filtered = filtered.model_copy(update={"default_routes": default_routes()})
    # LAST, over the narrowed schema, so `selectable_routes` reads the nodes as the run holds them.
    if workspace is not None:
        filtered = filtered.model_copy(
            update={"model_capabilities": resolve_schema_menu(filtered, workspace=workspace)}
        )
    return filtered


def _apply_starting_prompts(
    pipeline_params: PipelineParams,
    *,
    filtered: PipelineSchema,
    dataset: ResolvedDataset,
    dataset_name: str,
) -> PipelineParams:
    """Assumes the caller checked ``has_dataset_prompts``."""
    prompt_nodes = filtered.prompt_nodes()
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
        laid = {**pipeline_params.node(pnode.name).laid_keys(), "prompt": rendered}
        pipeline_params = pipeline_params.with_nodes({**pipeline_params.nodes, pnode.name: laid})
        logger.info("Starting prompt: %s origin.yaml[%s]", dataset_name, pnode.name)
    return pipeline_params


def _validate_prompt_reach(
    *,
    filtered: PipelineSchema,
    has_starting_prompts: bool,
    prompt_fields_as_node_params: bool,
    dataset_name: str,
) -> None:
    if filtered.prompt_nodes():
        return
    running = list(filtered.running)
    if has_starting_prompts:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r} ships starting prompts but no running node in {running} "
            f"declares `prompt_info`, so the rendered prompt is dropped before the wire and "
            f"every cell runs with an empty system prompt. An LLM node must declare it, in "
            f"GET /pipeline or in the dataset's `dataset.yaml::pipeline`.",
            code="pipeline_config_invalid",
        )
    if not prompt_fields_as_node_params:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r}: no running node in {running} declares `prompt_info`, and "
            f"this backend sends a node only the rendered prompt, so every candidate scores as "
            f"the same no-skill call and the round reports a tie it never measured. Declare "
            f"`prompt_info` with its `fields` on the node the prompt is meant for.",
            code="pipeline_config_invalid",
        )


def _validate_model_ownership(
    pipeline_params: PipelineParams,
    *,
    filtered: PipelineSchema,
    dataset_name: str,
) -> None:
    for node in filtered.running_nodes:
        if node.is_llm and not pipeline_params.node(node.name).model:
            raise PayloadInvalidError(
                f"dataset {dataset_name!r}: LLM node {node.name!r} has no owned model. "
                f"Declare it in the dataset's dataset.yaml::pipeline.nodes.{node.name}.config.model "
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
    schema: PipelineSchema, params: PipelineParams
) -> dict[str, NodeOutputSchema | None]:
    """Folded as the WIRE folds it, never the declaration; ``None`` is a point that answers as text."""
    folded = fold_output_contract(params, schema)
    out: dict[str, NodeOutputSchema | None] = {}
    for node, declared in schema.node_output_schemas().items():
        if params.node(node).laid_keys().get(SCHEMA_TOGGLE_PARAM) == ANSWER_AS_TEXT:
            out[node] = None
            continue
        js = folded.node(node).laid_keys().get(OUTPUT_SCHEMA_KEY)
        out[node] = (
            parse_resolved_schema({"json_schema": js}) if isinstance(js, dict) and js else declared
        )
    return out


class LaidConfig(NamedTuple):
    config: CampaignConfig
    # Per optimizer-node knob `config` holds, the last layer that SET it.
    knob_layers: dict[str, dict[str, KnobLayer]]


def laid_config(
    stores: Stores, layers: ConfigLayers, *, connector: str, schema: PipelineSchema | None
) -> LaidConfig:
    """THE read of a run's config, the draft's and the mint's: the dataset's block, the door's
    edit, then a free account's tier, bound to each node of *schema* (``None`` binds none)."""
    folded = layers.folded()
    config = selected_config(folded.document)
    fence = free_fence(stores, connector)
    if fence is not None and (tier := free_tier_layers(schema, fence)):
        folded = fold_layers(CAMPAIGN_RULE, tier, under=folded)
        config = CampaignConfig.model_validate(folded.document)
    knob_layers = {
        node: {key: folded.stamps[("optimization", "nodes", node, "config", key)] for key in held}
        for node, held in ((n, o.config) for n, o in config.optimization.nodes.items())
    }
    return LaidConfig(config, knob_layers)


def laid_session(session: Session, layers: ConfigLayers) -> tuple[Session, CampaignConfig]:
    """The mint's `laid_config`: *session* records which layer set each knob, its mint writes."""
    laid = laid_config(
        session.store,
        layers,
        connector=session.backend_client.connector.name,
        schema=session.pipeline_schema,
    )
    read = session.pipeline_read.model_copy(update={"knob_layers": laid.knob_layers})
    return replace(session, pipeline_read=read), laid.config


def draft_layers(stores: Stores, draft: DraftFile) -> ConfigLayers:
    """``draft.config`` over its basis: an upload's `defaults.yaml` campaign, its definition's half
    (`UPLOAD_DEFINITION_KEYS`) left to `_draft_local`; a reused dataset's own block."""
    match draft.basis:
        case UploadBasis():
            edit = {k: v for k, v in draft.config.items() if k not in UPLOAD_DEFINITION_KEYS}
            return ConfigLayers(campaign_defaults(), edit)
        case DatasetBasis(dataset=name):
            return ConfigLayers(dataset_campaign_block(resolve_dataset(stores, name)), draft.config)
        case _:
            assert_never(draft.basis)


def draft_config(stores: Stores, draft: DraftFile) -> CampaignConfig:
    """The draft's config, bound to no node: what its Start admits and sends reads no pipeline."""
    return laid_config(
        stores,
        draft_layers(stores, draft),
        connector=draft_connector(stores, draft),
        schema=None,
    ).config


def draft_budget(stores: Stores, draft: DraftFile) -> Budget:
    return draft_config(stores, draft).optimization.budget


def draft_sends(stores: Stores, draft: DraftFile) -> tuple[Send, ...]:
    """What a Start sends, and who pays each."""
    return sends_on(stores, draft_config(stores, draft), draft_connector(stores, draft))


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
        return CycleConfig.laid(declared, [LaidSpan(seed=None, scoring_lock=root_lock)])
    return CycleConfig.laid(declared, campaigns.read_laid_spans(hop))


@dataclass(frozen=True)
class _Merge:
    declared: PipelineSchema
    filtered: PipelineSchema
    cfg: CampaignConfig
    layers: ParamLayers
    # The free tier fences the target's nodes: it is offered only the tier's task models.
    free: bool
    # Whether a steer binds the reader to what `pickable` leaves it: a campaign read by
    # an account without `campaign.babysit`; never a draft, which sets what it permits.
    steer_bound: bool
    knob_layers: KnobLayers

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
class OutsideRunMerge(_Merge):
    # The definition an upload's Start writes (`DraftPipeline`).
    local: dict[str, Any]
    config_layers: ConfigLayers


def _draft_merge(stores: Stores, draft: DraftFile, served: ServedPipeline) -> OutsideRunMerge:
    return merge_outside_run(
        stores, _draft_local(stores, draft), draft_layers(stores, draft), served
    )


def merge_outside_run(
    stores: Stores, local: dict[str, Any], config_layers: ConfigLayers, served: ServedPipeline
) -> OutsideRunMerge:
    """*config_layers* over *local* as a mint would lay them, opening no session."""
    templated = with_node_templates(local)
    declared = parse_pipeline_response(served.over(templated))
    laid = laid_config(stores, config_layers, connector=served.connector, schema=declared)
    cfg = laid.config
    fence = free_fence(stores, served.connector)
    filtered = _resolve_active_schema(
        declared, exclude=list(cfg.exclude_nodes), narrowing=cfg.optimizer_narrowing
    )
    return OutsideRunMerge(
        # What was on OFFER before this draft narrowed it, so a menu cannot shrink to its own pick.
        declared=declared,
        filtered=filtered,
        cfg=cfg,
        layers=ParamLayers(
            schema=filtered,
            workspace=stores.base_dir,
            dataset=pipeline_node_overlay(templated),
            campaign=cfg.pipeline_overlay,
            free_tier=free_tier_overlay(cfg, declared, fence),
            identity=identity_layer(None, None, None, cfg.judges, filtered.active_steps),
        ),
        free=_task_fenced(fence),
        steer_bound=False,
        knob_layers=laid.knob_layers,
        local=local,
        config_layers=config_layers,
    )


def _task_fenced(fence: FreeFence | None) -> bool:
    return fence is not None and fence.task


def _campaign_merge(stores: Stores, campaign: Campaign, at: Subject) -> _CampaignMerge:
    """Off its mint's records alone, so a campaign outlives its dataset dir; lays no identity:
    what a point is keyed by is the subject's to add."""
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=at.cycle_id or campaign.root_cycle_id)
    resolved = scan_resolved_run(stores.campaigns.chain(hop))
    read = _recorded_read(resolved, hop)
    schema = parse_pipeline_response(read.declaration)
    cycle = cycle_config(stores, campaign, hop)
    cfg = cycle.config
    filtered = _resolve_active_schema(
        schema, exclude=list(cfg.exclude_nodes), narrowing=cfg.optimizer_narrowing
    )
    return _CampaignMerge(
        declared=schema,
        filtered=filtered,
        cfg=cfg,
        layers=ParamLayers(
            schema=filtered,
            workspace=stores.base_dir,
            dataset=read.dataset_overlay,
            campaign=cfg.pipeline_overlay,
            free_tier=read.free_tier_overlay,
            seed=cycle.seed_overlay,
        ),
        free=_task_fenced(free_fence(stores, campaign.connector)),
        steer_bound=not has_capability(stores.identity, CAMPAIGN_BABYSIT_CAP),
        knob_layers=cycle.knob_layers(read.knob_layers),
        hop=hop,
        experiment=resolved.experiment,
    )


def recorded_schema(stores: Stores, hop: CycleHop) -> PipelineSchema:
    """What *hop* ran, as its mint recorded run init read it; never today's files or backend."""
    return parse_pipeline_response(recorded_read(stores, hop).declaration)


def recorded_read(stores: Stores, hop: CycleHop) -> PipelineRead:
    return _recorded_read(scan_resolved_run(stores.campaigns.chain(hop)), hop)


def _recorded_read(resolved: ResolvedRun, hop: CycleHop) -> PipelineRead:
    if resolved.read is None:
        raise FileUnreadableError(
            FileKind.LEDGER,
            path=f"campaigns/{hop.campaign_id}/cycles/{hop.cycle_id}",
            reason="it holds no resolved run, which its mint appends before the campaign exists",
        )
    return resolved.read


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
    drawn = stores.campaigns.last(m.hop, PartitionRecord)
    bank = None if drawn is None else stores.tenant_datasets.bank(drawn.dataset_hash)
    if drawn is None or bank is None:
        return None
    return mint_facts(m.cfg, drawn.restored(bank.samples).demo, m.filtered, stores.block_packs)


def _rows(m: _Merge, folded: FoldedParams, ran: PipelineSchema) -> dict[str, list[NodeConfigParam]]:
    return m.filtered.node_config_schema(
        steer_bound=m.steer_bound,
        values=folded.params.laid_nodes,
        sources=folded.provenance,
        model_menu=_admitted_menu(m, ran),
        declared=m.declared,
    )


def _admitted_menu(m: _Merge, ran: PipelineSchema) -> list[str]:
    """Offers no model the Start would refuse: one no rate prices under a dollar ceiling, or one
    the free tier does not list for a free account. A model is priced on the provider *ran* lays
    its node on, never the one the backend declares. A served read never fetches a price list: a
    route's answer is the last any process's fetch gave (`pricing.remembered_priced`), kept fresh by
    the API's sweep, and a route none answered stays offered, as through an outage."""
    models = m.filtered.selectable_models()
    if m.free:
        models = [model for model in models if model in free_task_models()]
    providers = {n.provider for n in ran.declared_nodes if n.provider}
    if not providers or m.cfg.optimization.budget.usd is None:
        return models
    return [
        model
        for model in models
        if any(remembered_priced(model, p) is not False for p in providers)
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
    ran = schema_as_run(m.filtered, folded.params, workspace)
    rows = _rows(m, folded, ran)
    return CampaignPipelineResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
        connector=connector,
        self_optimization=is_self_optimization(connector),
        cell_terms=list(connectors.CONNECTORS.get(connector).cell_terms),
        optimizer=m.cfg.optimization.optimizer,
        optimizer_setup=optimizer_setup(m.optimizer, mint, m.filtered, workspace, m.knob_layers),
        params=folded.params.dump(),
        node_config_schema=rows,
        view=m.filtered.view,
        node_output_schema=resolved_output_schemas(m.filtered, folded.params),
        model_capabilities=resolve_schema_menu(ran, workspace=workspace),
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
    the chain they run in as its mint holds it (`schema_as_run`: each node on its laid provider and
    model), the chain as declared before the draft narrowed it, its config as this
    account runs it, the layers its mint lays, the definition an upload's Start writes (`local`),
    and the backend that serves it."""

    params: PipelineParams
    rows: dict[str, list[NodeConfigParam]]
    chain: PipelineSchema
    declared: PipelineSchema
    config: CampaignConfig
    layers: ConfigLayers
    local: dict[str, Any]
    connector: str

    @functools.cached_property
    def optimizer(self) -> SelectedOptimizer:
        """`config`'s selection, made only where a read takes it: a draft whose overlay no
        longer selects still folds, rows and gates."""
        return select_optimizer(self.config.optimization)

    @functools.cached_property
    def criterion(self) -> DraftCriterion:
        """``config.scoring`` as a check-in picks it, its dials over this backend's terms."""
        return draft_criterion(self.config.scoring, campaign_terms(self.connector))


def draft_pipeline(stores: Stores, draft: DraftFile, served: ServedPipeline) -> DraftPipeline:
    m = _draft_merge(stores, draft, served)
    folded = m.layers.fold()
    ran = schema_as_run(m.filtered, folded.params, stores.base_dir)
    return DraftPipeline(
        folded.params,
        _rows(m, folded, ran),
        ran,
        m.declared,
        m.cfg,
        m.config_layers,
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
            m.filtered.active_steps,
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
    return _permitted(_campaign_merge(stores, campaign, at).filtered)


def _permitted(filtered: PipelineSchema) -> dict[str, NodeSearchNarrowing]:
    return {
        n.name: NodeSearchNarrowing(param_allowed_values=dict(n.param_allowed_values))
        for n in filtered.declared_nodes
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


def campaign_config_map(stores: Stores, campaign_id: str) -> ConfigMapResponse:
    campaign = owned_campaign(stores, campaign_id)
    m = _campaign_merge(stores, campaign, Subject(campaign_id))
    return config_map(m.cfg, m.optimizer, m.knob_layers)


async def resolve_pipeline_for_dataset(
    stores: Stores, dataset_name: str, *, above: frozenset[str] = frozenset()
) -> DatasetPipelineResponse:
    """*above*: the datasets enclosing this one on its branch, which it may not nest."""
    dataset = resolve_dataset(stores, dataset_name)
    experiment = experiment_outside_run(stores, dataset)
    declared = await dataset_pipeline(stores, dataset, experiment=experiment)
    cfg = load_dataset_campaign_config(dataset)
    definition = runnable_definition(dataset)
    schema = parse_pipeline_response(declared)
    filtered = _resolve_active_schema(
        schema, exclude=list(cfg.exclude_nodes), narrowing=cfg.optimizer_narrowing
    )
    # Declared, valueless: nothing steers it.
    rows = filtered.node_config_schema(steer_bound=False)
    return DatasetPipelineResponse(
        connector=definition.connector,
        view=filtered.view,
        node_config_schema=rows,
        reach=reach_map(rows),
        nests=[await _nested(stores, ref, above | {dataset_name}) for ref in _nests(schema)],
    )


async def _nested(stores: Stores, ref: NestedPipelineRef, above: frozenset[str]) -> NestedPipeline:
    if ref.dataset in above:
        return NestedPipeline(**dict(ref), pipeline=None, unread=f"{ref.dataset} nests itself")
    try:
        nested = await resolve_pipeline_for_dataset(stores, ref.dataset, above=above)
    except PotterError as exc:
        return NestedPipeline(**dict(ref), pipeline=None, unread=str(exc))
    return NestedPipeline(**dict(ref), pipeline=nested, unread=None)


# A prompt, a description and a nested value are no setting one line can print.
_RUNS_WITH_KINDS = frozenset({"model", "enum", "number", "bool", "string"})
# The typed refusals a root's own data raises; any other exception is a defect and propagates.
_ROOT_UNRESOLVED = (*DEFINITION_READ_ERRORS, ScoringFormulaError)


class RootRunsWith(NamedTuple):
    """``runs_with`` ``None`` is a root whose pipeline did not resolve: a broken dataset drops its
    own row only, and the row serves the refusal it raised."""

    runs_with: CampaignRunsWith | None
    # The root's typed refusal, as raised; ``None`` where it resolved.
    unread: str | None


def campaign_runs_with(stores: Stores, campaign: Campaign) -> RootRunsWith:
    try:
        key = _root_key(stores, campaign)
    except _ROOT_UNRESOLVED as exc:
        return RootRunsWith(None, str(exc))

    def resolved() -> RootRunsWith:
        try:
            return RootRunsWith(_runs_with(stores, campaign), None)
        except _ROOT_UNRESOLVED as exc:
            return RootRunsWith(None, str(exc))

    held = derived(
        ("runs_with", stores.campaigns.cycle_dir(campaign.root_hop)), sig=key, compute=resolved
    )
    assert held is not None, "a keyed derivation always answers"
    return held


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
    read = scan_resolved_run(stores.campaigns.chain(campaign.root_hop)).read
    return (
        stable_hash(cycle.config.model_dump(mode="json")),
        stable_hash(cycle.seed_overlay),
        campaign.root_cycle_id,
        stable_hash(
            None
            if read is None
            else [
                read.declaration,
                laid_calls(read.dataset_overlay),
                read.free_tier_overlay,
            ]
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
    for name in m.filtered.running:
        if (cfg := stripped.laid_nodes.get(name)) is None:
            continue
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

    filtered = _resolve_active_schema(
        session.pipeline_schema,
        exclude=exclude,
        narrowing=campaign_config.optimizer_narrowing,
        workspace=session.store.base_dir,
    )
    # Laid at the mint, then read off its record: an account leaving the tier moves no minted run.
    if session.cycle_id:
        recorded = recorded_read(session.store, session.hop)
        minted = {
            "free_tier_overlay": recorded.free_tier_overlay,
            "knob_layers": recorded.knob_layers,
        }
    else:
        fence = free_fence(session.store, session.backend_client.connector.name)
        minted = {
            "free_tier_overlay": free_tier_overlay(campaign_config, session.pipeline_schema, fence),
        }
    read = session.pipeline_read.model_copy(update=minted)
    folded = ParamLayers(
        schema=filtered,
        workspace=session.store.base_dir,
        dataset=read.dataset_overlay,
        campaign=campaign_config.pipeline_overlay,
        free_tier=read.free_tier_overlay,
        seed=cycle.seed_overlay,
        identity=identity_layer(
            session.store,
            session.backend_client.connector,
            session.backend_client.workload.experiment,
            campaign_config.judges,
            filtered.active_steps,
        ),
    ).fold()
    pipeline_params = folded.params

    ships_prompts = dataset is not None and has_dataset_prompts(dataset)
    _validate_prompt_reach(
        filtered=filtered,
        has_starting_prompts=ships_prompts,
        prompt_fields_as_node_params=session.backend_client.connector.prompt_fields_as_node_params,
        dataset_name=dataset_name,
    )

    if dataset is not None and ships_prompts:
        pipeline_params = _apply_starting_prompts(
            pipeline_params,
            filtered=filtered,
            dataset=dataset,
            dataset_name=dataset_name,
        )

    _validate_model_ownership(pipeline_params, filtered=filtered, dataset_name=dataset_name)
    # Asked here so a channel the connector cannot run stops init, not every cell of the origin.
    session.backend_client.connector.prompt_delivery(pipeline_params)

    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    declared = session.pipeline_schema
    unrun = [n.name for n in declared.declared_nodes if n.name not in declared.running]
    unrun_str = f"  Declared, run by no walk: {', '.join(unrun)}" if unrun else ""
    logger.info("Running nodes: %s%s%s", ", ".join(filtered.running), excl_str, unrun_str)

    return replace(
        session,
        pipeline_schema=schema_as_run(filtered, pipeline_params, session.store.base_dir),
        pipeline_read=read,
        pipeline_params=pipeline_params,
        identity_keys=folded.identity_keys,
    )
