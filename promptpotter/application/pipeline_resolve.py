from __future__ import annotations

import functools
from collections.abc import Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, NamedTuple, Self, assert_never, get_args

from pydantic import BaseModel, ValidationError

from promptpotter import connectors
from promptpotter.application.config_map import ConfigMapResponse, bench_defaults_pin, config_map
from promptpotter.application.datasets.authored import (
    ConfigLayers,
    dataset_campaign_block,
    effective_config,
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
from promptpotter.application.datasets.startability import draft_bank
from promptpotter.application.free_tier import (
    FreeFence,
    free_fence,
    free_task_models,
    free_tier_layers,
    free_tier_overlay,
)
from promptpotter.application.initialization.session import Session, drawn_partition
from promptpotter.application.jobs.quota import sends_on
from promptpotter.application.optimizer_contract import MintFacts
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.pipeline_responses import (
    CampaignPipelineResponse,
    DatasetPipelineResponse,
    ForkPreviewResponse,
    NestedPipeline,
    SteerVerdict,
    optimizer_setup,
)
from promptpotter.application.scoring.evaluators import NameOwner, refuse_unowned_terms
from promptpotter.application.scoring.formula.compiler import (
    campaign_terms,
    compile_scorer,
    split_scoring_block,
)
from promptpotter.application.scoring.formula.matchers import admits, matcher_of
from promptpotter.application.scoring.judges.registry import judge_instrument, two_valued_terms
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.connectors.protocol import PROBE_WORKLOAD, Connector
from promptpotter.domain.arms import Concluded
from promptpotter.domain.bench import partition_bank
from promptpotter.domain.campaign import (
    Campaign,
    CampaignCycle,
    CampaignRunsWith,
    RunsWithParam,
    VendorModels,
)
from promptpotter.domain.campaign_config import (
    CAMPAIGN_RULE,
    CampaignConfig,
    CycleConfig,
    LaidSpan,
    node_config_stamps,
)
from promptpotter.domain.checkin_draft import DatasetBasis, DraftBasis, DraftFile, UploadBasis
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
from promptpotter.domain.records.run_wiring import (
    DefaultsDrift,
    DefaultsPin,
    PipelineRead,
    ScoringLockedRecord,
)
from promptpotter.domain.sample import Bank, Grading, Sample
from promptpotter.domain.scoring import Scorer, ScoringFormulaError
from promptpotter.domain.search_point import missing_template_vars, strip_rendered_prompt
from promptpotter.domain.spend import Budget
from promptpotter.domain.treatment import Treatment
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.llm.capabilities import resolve_menu, resolve_schema_menu
from promptpotter.infrastructure.llm.pricing import remembered_priced
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.llm.wallet import Send
from promptpotter.infrastructure.store.campaign_store.ledger_scan import (
    RESOLVED_RUN,
    ResolvedRun,
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
from promptpotter.infrastructure.store.stores import Stores, descend_store
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

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key the judges' fingerprint rides into measurement identity — ONE key over the
whole term → judge mapping: which grader read the answer, identity beside what a connector's
`identity_config` lays, never a tunable — nothing may put it in `param_keys`."""

PARAM_LAYERS: tuple[ParamLayer, ...] = get_args(ParamLayer)


@dataclass(frozen=True)
class FoldedParams:
    params: PipelineParams
    provenance: dict[str, dict[str, ParamSource]]


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
    start: Mapping[str, Any] = field(default_factory=dict)
    evolved: Mapping[str, Any] = field(default_factory=dict)

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
            case "start":
                return self.start
            case "model_floor":
                return self._floors(params)
            case "evolved":
                return self.evolved
            case _:
                assert_never(source)

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
                n.model_copy(
                    update={"current_config": n.current_config.model_copy(update=running[n.name])}
                )
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
            if (model := n.current_config.model) is not None
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

    connector: Connector
    # The connector endpoint it was read at (`DatasetDefinition.endpoint`).
    endpoint: str | None
    # `None`: the connector serves none, so the local definition is the whole of it.
    doc: Mapping[str, Any] | None

    def over(self, local: Mapping[str, Any]) -> dict[str, Any]:
        return dict(local) if self.doc is None else overlay_dataset_pipeline(self.doc, local)


async def served_pipeline(
    stores: Stores,
    connector: Connector,
    *,
    endpoint: str | None,
    experiment: BaseModel | None,
) -> ServedPipeline:
    """Read live at every moment that needs it; raises ``BackendUnreachableError``, never a stand-in."""
    served: Mapping[str, Any] | None = None
    if connector.serves_pipeline:
        probe = BackendClient(
            connector, endpoint=endpoint, workload=PROBE_WORKLOAD, jobs_dir=stores.jobs_dir
        )
        async with aclosing(probe) as client:
            served = await client.fetch_pipeline()
    elif connector.pipeline_declaration is not None:
        served = connector.pipeline_declaration(stores, experiment)
    return ServedPipeline(connector, endpoint, served)


def campaign_connector(campaign: Campaign) -> Connector:
    """The connector *campaign* named at its mint, the one lookup of it."""
    return connectors.CONNECTORS.get(campaign.connector)


@dataclass(frozen=True)
class DraftSource:
    """The first seam's answer for one request: the dataset a basis names, resolved once (``None``
    for an upload), the backend its Start runs, the edit laid over its basis, and an upload's
    picked chain (`UploadBasis.chain`)."""

    dataset: ResolvedDataset | None
    connector: Connector
    endpoint: str | None
    edit: Mapping[str, Any]
    chain: tuple[str, ...] | None = None

    @property
    def layers(self) -> ConfigLayers:
        """The edit over its basis: an upload's `defaults.yaml` campaign, its definition's half
        (`UPLOAD_DEFINITION_KEYS`) left to `local`; a reused dataset's own block."""
        if self.dataset is None:
            edit = {k: v for k, v in self.edit.items() if k not in UPLOAD_DEFINITION_KEYS}
            return ConfigLayers(campaign_defaults(), edit)
        return ConfigLayers(dataset_campaign_block(self.dataset), self.edit)

    @property
    def local(self) -> dict[str, Any]:
        """What run init lays over the served pipeline once Start lands: an upload's definition,
        the edit's definition half laid in, which its Start writes; a reused dataset's own file."""
        if self.dataset is None:
            half = ConfigLayers(campaign_defaults(), self.edit).config()
            return upload_pipeline_json(self.connector, self.chain, half.pipeline_overlay)
        return runnable_definition(self.dataset).pipeline


def basis_source(stores: Stores, basis: DraftBasis, edit: Mapping[str, Any]) -> DraftSource:
    """An upload runs its pick on its connector's one endpoint; a dataset the pair its
    definition names."""
    match basis:
        case UploadBasis(connector=name, chain=chain):
            return DraftSource(None, connectors.CONNECTORS.get(name), None, edit, chain)
        case DatasetBasis(dataset=name):
            dataset = resolve_dataset(stores, name)
            definition = runnable_definition(dataset)
            return DraftSource(dataset, definition.owner, definition.endpoint, edit)
        case _:
            assert_never(basis)


def draft_source(stores: Stores, draft: DraftFile) -> DraftSource:
    return basis_source(stores, draft.basis, draft.config)


async def source_served(stores: Stores, source: DraftSource) -> ServedPipeline:
    return await served_pipeline(
        stores,
        source.connector,
        endpoint=source.endpoint,
        experiment=experiment_outside_run(stores, source.dataset),
    )


async def draft_served(stores: Stores, draft: DraftFile) -> ServedPipeline:
    return await source_served(stores, draft_source(stores, draft))


def experiment_outside_run(stores: Stores, dataset: ResolvedDataset | None) -> BaseModel | None:
    if dataset is None:
        return None
    connector = dataset_connector(dataset)
    read = None if connector is None else dataset_experiment(stores, dataset, connector)
    return None if read is None else read.experiment


def identity_layer(
    stores: Stores | None,
    connector: Connector | None,
    experiment: BaseModel | None,
    judges: Mapping[str, JudgeSpec] | None,
    chain: Sequence[str],
    *,
    base_url: str,
) -> dict[str, dict[str, Any]]:
    """The connector's and the judges' ONE channel into the archive key; *base_url* is the
    address its cells were sent to."""
    out: dict[str, dict[str, Any]] = {}
    if connector is not None and connector.identity_config is not None:
        if stores is None:
            raise ValueError(f"{connector.name}: its identity needs the stores")
        out.update(connector.identity_config(stores, experiment, base_url))
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
    # LAST, over the narrowed schema, so `resolve_schema_menu` reads the nodes as the run holds them.
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
    return pipeline_params


def _validate_prompt_reach(
    *,
    filtered: PipelineSchema,
    has_starting_prompts: bool,
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
    raise PayloadInvalidError(
        f"dataset {dataset_name!r}: no running node in {running} declares `prompt_info`, and "
        f"this backend sends a node only its prompt, so every candidate scores as the same "
        f"no-skill call and the round reports a tie it never measured. Declare `prompt_info` "
        f"with its `fields` on the node the prompt is meant for.",
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


def _evolved_overlay(stores: Stores, at: Subject) -> dict[str, Any]:
    """The sparse delta: the complete config read as one would stamp every param ``evolved``."""
    if at.kind != "candidate" or at.round is None:
        return {}
    hop = CycleHop(campaign_id=at.campaign_id, cycle_id=at.cycle_id)
    held = stores.campaigns.standing_rounds(hop, moment=None).rounds.get(at.round)
    arm = None if held is None else held.close.arms.get(ArmKey(at.candidate_id))
    return dict(arm.overlay) if isinstance(arm, Concluded) else {}


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
    # Per optimizer-node key `optimization.nodes` or `call_target` lays, the last layer that SET it.
    knob_layers: dict[str, dict[str, KnobLayer]]


def laid_config(
    stores: Stores, layers: ConfigLayers, *, connector: Connector, schema: PipelineSchema | None
) -> LaidConfig:
    """THE read of a run's config, the draft's and the mint's: the dataset's block, the door's
    edit, then a free account's tier, bound to each node of *schema* (``None`` binds none)."""
    folded = layers.folded()
    config = selected_config(folded.document)
    fence = free_fence(stores, connector)
    if fence is not None and (tier := free_tier_layers(schema, fence)):
        folded = fold_layers(CAMPAIGN_RULE, tier, under=folded)
        config = CampaignConfig.model_validate(folded.document)
    # A key the call target lays is set by the layer that set `call_target`; a node's own wins.
    knob_layers = {
        node: {key: folded.stamps[("optimization", "call_target", key)] for key in keys}
        for node, keys in select_optimizer(config.optimization).called.items()
    }
    for node, key, layer in node_config_stamps(folded):
        knob_layers.setdefault(node, {})[key] = layer
    return LaidConfig(config, knob_layers)


def laid_session(session: Session, layers: ConfigLayers) -> tuple[Session, CampaignConfig]:
    """The mint's `laid_config`: *session* records which layer set each knob, its mint writes."""
    declared = session.pipeline_read.declared_schema()
    connector = session.backend_client.connector
    laid = laid_config(session.store, layers, connector=connector, schema=declared)
    fence = free_fence(session.store, connector)
    read = session.pipeline_read.model_copy(
        update={
            "knob_layers": laid.knob_layers,
            "free_tier_overlay": free_tier_overlay(laid.config, declared, fence),
        }
    )
    return replace(session, pipeline_read=read), laid.config


def _source_config(stores: Stores, source: DraftSource) -> CampaignConfig:
    """The draft's config, bound to no node: what its Start admits and sends reads no pipeline."""
    return laid_config(stores, source.layers, connector=source.connector, schema=None).config


def draft_config(stores: Stores, draft: DraftFile) -> CampaignConfig:
    return _source_config(stores, draft_source(stores, draft))


def draft_budget(stores: Stores, draft: DraftFile) -> Budget:
    return draft_config(stores, draft).optimization.budget


def draft_sends(stores: Stores, draft: DraftFile) -> tuple[Send, ...]:
    """What a Start sends, and who pays each."""
    source = draft_source(stores, draft)
    return sends_on(stores, _source_config(stores, source), source.connector)


def defaults_drift(campaign: Campaign, *, running: Treatment) -> DefaultsDrift | None:
    """The one answer to whether what the declaration is a delta over moved since its mint."""
    declared = DefaultsPin(
        bench=campaign.declaration.spec.bench_defaults_pin, treatment=campaign.treatment
    )
    now = DefaultsPin(bench=bench_defaults_pin(), treatment=running)
    return None if now == declared else DefaultsDrift(declared=declared, running=now)


def cycle_config(stores: Stores, at: CampaignCycle) -> CycleConfig:
    """The declaration's effective config under every start of *at*'s line, the criterion its dials
    lock on the origin laid on."""
    return CycleConfig.laid(
        _declared(stores, at.campaign), stores.campaigns.read_laid_spans(at.hop)
    )


def graded_scorer(grading: Grading, *, verifier_graded: bool) -> Scorer:
    """The ONE compile of a scorer: a run's (`Resolution.scorer`) or a filed answer's grading."""
    spec = split_scoring_block(grading.scoring, judge_instrument=None)
    return compile_scorer(
        spec.per_sample,
        spec.per_cell,
        verifier_graded=verifier_graded,
        judge_instrument=judge_instrument(grading.judges),
        two_valued_terms=two_valued_terms(grading.judges),
    )


@dataclass(frozen=True)
class Resolution:
    """What a run grades by: its config's grading and its bank's recorded answer to whether it
    carries no label (`sample.py::Bank.verifier_graded`)."""

    config: CampaignConfig
    verifier_graded: bool

    @property
    def grading(self) -> Grading:
        return Grading(scoring=self.config.scoring, judges=self.config.judges)

    @functools.cached_property
    def scorer(self) -> Scorer:
        return graded_scorer(self.grading, verifier_graded=self.verifier_graded)

    def proven_scorer(self, owners: Sequence[NameOwner]) -> Scorer:
        """`scorer`, each term it names held by an owner of this campaign's cells (`owners`, its
        judges beside them): asked where the resolution meets its connector, before an answer is
        filed under its grading, so `open_cell` compiles a grading proven once."""
        judges = NameOwner("a judge of this campaign", frozenset(self.config.judges))
        refuse_unowned_terms(self.scorer.terms, (*owners, judges))
        return self.scorer

    def overlaid(self, *layers: Layer[str]) -> Self:
        return replace(self, config=self.config.overlaid(*layers))


@dataclass(frozen=True)
class RunResolution(Resolution):
    """Its grading, params and optimizer as the ONE fold (`ParamLayers`) lays its line over *read*:
    its mint's record (`resolved`), a launch's own, which `Session.bound` lays the mint's layers
    into (`run_resolution`), or a read before any mint (`DraftPipeline`). A ledger that lost its
    read still grades: only its pipeline refuses. *menu_at*: where it reads today's menu; a read
    off records reads none."""

    recorded: PipelineRead | FileUnreadableError
    menu_at: Path | None
    cycle: CycleConfig
    workspace: Path

    @property
    def read(self) -> PipelineRead:
        if isinstance(self.recorded, FileUnreadableError):
            raise self.recorded
        return self.recorded

    @functools.cached_property
    def declared(self) -> PipelineSchema:
        return self.read.declared_schema()

    @functools.cached_property
    def schema(self) -> PipelineSchema:
        """The running chain, narrowed and excluded as the config declares."""
        return _resolve_active_schema(
            self.declared,
            exclude=list(self.config.exclude_nodes),
            narrowing=self.config.optimizer_narrowing,
            workspace=self.menu_at,
        )

    @functools.cached_property
    def layers(self) -> ParamLayers:
        return ParamLayers(
            schema=self.schema,
            workspace=self.workspace,
            dataset=self.read.dataset_overlay,
            campaign=self.config.pipeline_overlay,
            free_tier=self.read.free_tier_overlay,
            start=self.cycle.start_overlay,
        )

    @functools.cached_property
    def folded(self) -> FoldedParams:
        return self.layers.fold()

    @property
    def params(self) -> PipelineParams:
        return self.folded.params

    @functools.cached_property
    def chain(self) -> PipelineSchema:
        """The chain as run (`schema_as_run`): each node on its laid provider and model. A
        launch's starting prompts lay no route, so its chain is this one."""
        return schema_as_run(self.schema, self.params, self.workspace)

    @functools.cached_property
    def optimizer(self) -> SelectedOptimizer:
        return select_optimizer(self.config.optimization)

    @property
    def knob_layers(self) -> dict[str, dict[str, KnobLayer]]:
        return self.cycle.knob_layers(self.read.knob_layers)


def resolved(stores: Stores, at: CampaignCycle) -> RunResolution:
    """*at*'s resolution off its records alone: its line, the bank its mint drew and its read."""
    hop = at.hop
    cycle = cycle_config(stores, at)
    return RunResolution(
        cycle.config,
        drawn_partition(stores.campaigns, hop).verifier_graded,
        recorded=_recorded_read(stores.campaigns.read(hop, RESOLVED_RUN), hop),
        cycle=cycle,
        workspace=stores.base_dir,
        menu_at=None,
    )


def run_resolution(session: Session, cycle: CycleConfig) -> RunResolution:
    """What a launch runs *cycle* by: its own read, over the bank it holds."""
    workspace = session.store.base_dir
    return RunResolution(
        cycle.config,
        session.bank.verifier_graded,
        recorded=session.pipeline_read,
        cycle=cycle,
        workspace=workspace,
        menu_at=workspace,
    )


def declared_config(stores: Stores, campaign: Campaign) -> CycleConfig:
    """The declaration alone, which no start moved, its root's criterion lock laid on."""
    root_lock = stores.campaigns.last(campaign.root_hop, ScoringLockedRecord)
    spans = [LaidSpan(start=None, scoring_lock=root_lock)]
    return CycleConfig.laid(_declared(stores, campaign), spans)


def _declared(stores: Stores, campaign: Campaign) -> CampaignConfig:
    try:
        return effective_config(campaign.declaration.spec)
    except ValidationError as exc:
        raise FileUnreadableError(
            FileKind.MANIFEST,
            path=str(
                CampaignLayout(stores.campaigns.campaign_root_dir(campaign.campaign_id)).manifest
            ),
            reason=validation_reason(exc, shown=1),
        ) from exc


@dataclass(frozen=True, kw_only=True)
class DraftPipeline(RunResolution):
    """A resolution before a Start commits any of it, as its mint would lay it (a draft's, or a
    head-to-head arm's): what its nodes run with, their rows, the chain they run in as its mint holds
    it (`schema_as_run`: each node on its laid provider and model), the layers its mint lays, the
    definition an upload's Start writes (`local`), and the backend that serves it."""

    config_layers: ConfigLayers
    local: dict[str, Any]
    connector: Connector
    # The free tier fences the target's nodes: it is offered only the tier's task models.
    free: bool

    @functools.cached_property
    def rows(self) -> dict[str, list[NodeConfigParam]]:
        # A draft sets what it permits, so no steer binds its reader.
        return _rows(self, self.folded, self.chain, free=self.free, steer_bound=False)

    @functools.cached_property
    def criterion(self) -> DraftCriterion:
        """``config.scoring`` as a check-in picks it, its dials over this backend's terms."""
        return draft_criterion(self.config.scoring, campaign_terms(self.connector))


def merge_outside_run(
    stores: Stores,
    local: dict[str, Any],
    config_layers: ConfigLayers,
    served: ServedPipeline,
    *,
    verifier_graded: bool,
) -> DraftPipeline:
    """*config_layers* over *local* as a mint would lay them, graded under *verifier_graded*,
    opening no session."""
    templated = with_node_templates(local)
    declaration = served.over(templated)
    declared = parse_pipeline_response(declaration)
    laid = laid_config(stores, config_layers, connector=served.connector, schema=declared)
    fence = free_fence(stores, served.connector)
    read = PipelineRead(
        declaration=declaration,
        dataset_overlay=pipeline_node_overlay(templated),
        free_tier_overlay=free_tier_overlay(laid.config, declared, fence),
        knob_layers=laid.knob_layers,
    )
    return DraftPipeline(
        laid.config,
        verifier_graded,
        recorded=read,
        menu_at=None,
        cycle=CycleConfig.laid(laid.config, ()),
        workspace=stores.base_dir,
        config_layers=config_layers,
        local=local,
        connector=served.connector,
        free=_task_fenced(fence),
    )


def _source_pipeline(
    stores: Stores, source: DraftSource, served: ServedPipeline, bank: Bank | None
) -> DraftPipeline:
    return merge_outside_run(
        stores,
        source.local,
        source.layers,
        served,
        verifier_graded=bank is not None and bank.verifier_graded,
    )


def draft_pipeline(stores: Stores, draft: DraftFile, served: ServedPipeline) -> DraftPipeline:
    source = draft_source(stores, draft)
    return _source_pipeline(stores, source, served, draft_bank(stores, draft, source.dataset))


def _task_fenced(fence: FreeFence | None) -> bool:
    return fence is not None and fence.task


def _recorded_read(resolved: ResolvedRun, hop: CycleHop) -> PipelineRead | FileUnreadableError:
    if resolved.read is None:
        return FileUnreadableError(
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


def _draft_mint(stores: Stores, res: RunResolution, bank: Bank | None) -> MintFacts | None:
    """The demo split the draft's Start will draw, so its pool offers the keys before Start."""
    if bank is None:
        return None
    cfg = res.config
    demo = partition_bank(bank.samples, cfg.dataset_split, seed=cfg.run_seed).demo
    return mint_facts(cfg, demo, res.schema, stores.block_packs)


def _cycle_mint(stores: Stores, hop: CycleHop, res: RunResolution) -> MintFacts | None:
    """``None`` where the bank the cycle drew is no longer held."""
    drawn = drawn_partition(stores.campaigns, hop)
    if (bank := stores.tenant_datasets.bank(drawn.dataset_hash)) is None:
        return None
    return mint_facts(res.config, drawn.restored(bank.samples).demo, res.schema, stores.block_packs)


def _rows(
    res: RunResolution, folded: FoldedParams, ran: PipelineSchema, *, free: bool, steer_bound: bool
) -> dict[str, list[NodeConfigParam]]:
    """*steer_bound*: whether a steer binds the reader to what `pickable` leaves it, a campaign
    read by an account without `campaign.babysit`."""
    return res.schema.node_config_schema(
        steer_bound=steer_bound,
        values=folded.params.laid_nodes,
        sources=folded.provenance,
        model_menu=_admitted_menu(res, ran, free=free),
        declared=res.declared,
    )


def _admitted_menu(res: RunResolution, ran: PipelineSchema, *, free: bool) -> list[str]:
    """Offers no model the Start would refuse: one no rate prices under a dollar ceiling, or one
    the free tier does not list for a free account. A model is priced on the provider *ran* lays
    its node on, never the one the backend declares. A served read never fetches a price list: a
    route's answer is the last any process's fetch gave (`pricing.remembered_priced`), kept fresh by
    the API's sweep, and a route none answered stays offered, as through an outage."""
    models = res.schema.selectable_models()
    if free:
        models = [model for model in models if model in free_task_models()]
    providers = {n.provider for n in ran.declared_nodes if n.provider}
    if not providers or res.config.optimization.budget.usd is None:
        return models
    return [
        model
        for model in models
        if any(remembered_priced(model, p) is not False for p in providers)
    ]


def _response(
    stores: Stores,
    res: RunResolution,
    folded: FoldedParams,
    *,
    steer_bound: bool,
    campaign_id: str,
    cycle_id: str,
    dataset_name: str,
    connector: Connector,
    mint: MintFacts | None,
) -> CampaignPipelineResponse:
    workspace = stores.base_dir
    filtered = res.schema
    ran = schema_as_run(filtered, folded.params, workspace)
    rows = _rows(
        res,
        folded,
        ran,
        free=_task_fenced(free_fence(stores, connector)),
        steer_bound=steer_bound,
    )
    return CampaignPipelineResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
        connector=connector.name,
        self_optimization=connector.self_optimization,
        cell_terms=list(connector.cell_terms),
        optimizer=res.config.optimization.optimizer,
        optimizer_setup=optimizer_setup(res.optimizer, mint, filtered, workspace, res.knob_layers),
        params=folded.params.dump(),
        node_config_schema=rows,
        view=filtered.view,
        node_output_schema=resolved_output_schemas(filtered, folded.params),
        model_capabilities=resolve_schema_menu(ran, workspace=workspace),
        reach=reach_map(rows),
        nests=_nests(res.declared),
        is_single_node=filtered.is_single_node,
    )


def resolve_pipeline_for_draft(
    stores: Stores, draft: DraftFile, served: ServedPipeline
) -> CampaignPipelineResponse:
    source = draft_source(stores, draft)
    bank = draft_bank(stores, draft, source.dataset)
    pipeline = _source_pipeline(stores, source, served, bank)
    return _response(
        stores,
        pipeline,
        pipeline.folded,
        steer_bound=False,
        # A draft is no campaign and runs no cycle until its Start mints both.
        campaign_id=draft.draft_id,
        cycle_id="",
        dataset_name=draft.slug,
        connector=served.connector,
        mint=_draft_mint(stores, pipeline, bank),
    )


def resolve_pipeline_for_campaign(
    stores: Stores,
    campaign: Campaign,
    *,
    at: Subject,
) -> CampaignPipelineResponse:
    """``stores`` is already the store *campaign* lives in: the caller descends ``at.inside`` first.
    Off its mint's records alone, so a campaign outlives its dataset dir."""
    on = CampaignCycle(campaign, at.cycle_id or campaign.root_cycle_id)
    res = resolved(stores, on)
    folded = replace(res.layers, evolved=_evolved_overlay(stores, at)).fold()
    return _response(
        stores,
        res,
        folded,
        steer_bound=not has_capability(stores.identity, CAMPAIGN_BABYSIT_CAP),
        campaign_id=campaign.campaign_id,
        cycle_id=on.cycle_id,
        dataset_name=campaign.dataset_name,
        connector=campaign_connector(campaign),
        mint=_cycle_mint(stores, on.hop, res),
    )


def pipeline_subject(at: str, campaign_id: str) -> Subject:
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
    spec = pipeline_subject(at, campaign_id)
    # An inner searchpoint lives in the sandbox `;in=` names; the tenant tree answers for the OUTER.
    leaf = descend_store(stores, spec.inside)
    return resolve_pipeline_for_campaign(leaf, leaf.campaigns.campaign(campaign_id), at=spec)


def permitted_narrowing(stores: Stores, at: CampaignCycle) -> dict[str, NodeSearchNarrowing]:
    """What each node permits *at* a cycle: its recorded schema's value spaces narrowed by its line,
    never the root's nor the campaign's own layer alone, which holds none of the dataset's optimizer
    blocks. A steer launched at *at* outside them is the babysit act (decisions.md § 0005)."""
    return {
        n.name: NodeSearchNarrowing(param_allowed_values=dict(n.param_allowed_values))
        for n in resolved(stores, at).schema.declared_nodes
        if n.param_allowed_values
    }


def steer_verdict(
    stores: Stores, narrowing: Mapping[str, NodeSearchNarrowing], overlay: dict[str, Any] | None
) -> SteerVerdict:
    """The babysit act (decisions.md § 0005): a steer outside what the node permits needs `campaign.babysit`."""
    if not steers_disallowed_model(narrowing, overlay):
        return "clean"
    return "babysat" if has_capability(stores.identity, CAMPAIGN_BABYSIT_CAP) else "refused"


def fork_preview(stores: Stores, hop: CycleHop, overlay: dict[str, Any]) -> ForkPreviewResponse:
    """The verdict a fork cut at *hop* would meet, asked without forking."""
    at = CampaignCycle(stores.campaigns.campaign(hop.campaign_id), hop.cycle_id)
    narrowing = permitted_narrowing(stores, at)
    return ForkPreviewResponse(
        steer=steer_verdict(stores, narrowing, overlay),
        permitted=permitted_models_from_narrowing(narrowing),
    )


def campaign_config_map(stores: Stores, campaign_id: str) -> ConfigMapResponse:
    res = resolved(stores, stores.campaigns.campaign(campaign_id).root_cycle)
    return config_map(res.config, res.optimizer, res.knob_layers)


async def resolve_pipeline_for_dataset(
    stores: Stores, dataset_name: str, *, above: frozenset[str] = frozenset()
) -> DatasetPipelineResponse:
    """A reused dataset's draft before any edit, as its check-in would resolve it; *above*: the
    datasets enclosing this one on its branch, which it may not nest."""
    source = basis_source(stores, DatasetBasis(dataset=dataset_name), {})
    served = await source_served(stores, source)
    res = _source_pipeline(stores, source, served, bank=None)
    # Declared, valueless: nothing steers it.
    rows = res.schema.node_config_schema(steer_bound=False)
    return DatasetPipelineResponse(
        connector=source.connector.name,
        view=res.schema.view,
        node_config_schema=rows,
        reach=reach_map(rows),
        nests=[await _nested(stores, ref, above | {dataset_name}) for ref in _nests(res.declared)],
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


def line_config(stores: Stores, at: CampaignCycle) -> LineConfig:
    """Compiles no scorer: a formula's own refusal is the bench's, the one reading that grades."""
    try:
        config = cycle_config(stores, at).config
        if not split_scoring_block(config.scoring, judge_instrument=None).per_sample:
            return LineConfig(None, None)
    except _ROOT_UNRESOLVED as exc:
        return LineConfig(None, str(exc))
    return LineConfig(config, None)


def _root_key(stores: Stores, campaign: Campaign) -> tuple[Any, ...]:
    # VALUES, never the ledger's stat: a running root appends there on every sample.
    cycle = cycle_config(stores, campaign.root_cycle)
    read = stores.campaigns.read(campaign.root_hop, RESOLVED_RUN).read
    return (
        stable_hash(cycle.config.model_dump(mode="json")),
        stable_hash(cycle.start_overlay),
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
    res = resolved(stores, campaign.root_cycle)
    folded = res.folded
    stripped = strip_rendered_prompt(folded.params)
    nodes = {n.name: n for n in res.schema.declared_nodes}
    out: list[RunsWithParam] = []
    by_vendor: dict[str, list[str]] = {}
    for name in res.schema.running:
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
        optimizer=res.config.optimization.optimizer,
        budget_rounds=res.config.optimization.budget.rounds,
    )


def _vendor_of(model: str) -> str:
    return normalize_model_id(model).partition("/")[0]


def bind_resolution(session: Session, res: RunResolution) -> Session:
    """*session* bound to what *res* resolved, the dataset's starting prompts laid over its params:
    the definition a launch measures, which a read off records outlives."""
    dataset_name = session.measured_dataset
    dataset = session.dataset
    filtered = res.schema
    pipeline_params = res.params

    ships_prompts = dataset is not None and has_dataset_prompts(dataset)
    _validate_prompt_reach(
        filtered=filtered,
        has_starting_prompts=ships_prompts,
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

    return replace(
        session,
        resolution=res,
        pipeline_params=pipeline_params,
        cell_identity={
            node: keys
            for node, keys in identity_layer(
                session.store,
                session.backend_client.connector,
                session.backend_client.workload.experiment,
                res.config.judges,
                filtered.active_steps,
                base_url=session.backend_client.base_url,
            ).items()
            if node in filtered.running
        },
    )
