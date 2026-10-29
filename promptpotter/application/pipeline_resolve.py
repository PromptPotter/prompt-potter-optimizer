from __future__ import annotations

import logging
import re
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import Field, ValidationError

from promptpotter import connectors
from promptpotter.application import optimizers
from promptpotter.application.datasets.authored import (
    load_dataset_campaign_config,
    scorer_of,
    with_optimization,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    declared_pipeline_json,
    default_campaign_config,
    draft_scoring_block,
    rendered_pipeline_json,
    scoring_choice,
    split_overlay,
)
from promptpotter.application.datasets.prompts import (
    dataset_declared_nodes,
    declared_origin_prompts,
    has_dataset_prompts,
    load_dataset_node_overlay,
)
from promptpotter.application.datasets.startability import draft_samples
from promptpotter.application.evidence.subjects import SubjectSpec, parse_subject
from promptpotter.application.free_tier import free_task_models, with_free_tier_layer
from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.quota import on_free_tier
from promptpotter.application.knobs import bench_defaults_pin
from promptpotter.application.optimizer_manifest import (
    SelectedOptimizer,
    node_prompt,
    prompt_edits,
    prompt_key,
    resolve_optimizer,
    select_optimizer,
)
from promptpotter.application.optimizers.nodes import KnobDraw, MintFacts, admits
from promptpotter.application.runner.inner.tasks import is_self_optimization, parse_resolved
from promptpotter.application.scoring.formula.compiler import split_scoring_block
from promptpotter.application.scoring.formula.matchers import matcher_of
from promptpotter.application.scoring.judges.registry import judge_instrument
from promptpotter.domain.arms import Concluded
from promptpotter.domain.bench import partition_bank, restored_partition
from promptpotter.domain.campaign import CampaignRunsWith, RunsWithParam, VendorModels
from promptpotter.domain.campaign_config import (
    CampaignConfig,
    OptimizationConfig,
    apply_cycle_seed,
    effective_config,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import render_prompt
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.pipeline_overlay import (
    apply_node_overlay,
    fold_output_contract,
    merge_pipeline_params,
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
    MovableAgent,
    NestedPipelineRef,
    NodeConfigParam,
    NodeOutputSchema,
    NodeReach,
    NodeSearchNarrowing,
    ParamSource,
    PipelineView,
    reach_map,
)
from promptpotter.domain.pool import Access, PoolOffer, PoolSource, offers
from promptpotter.domain.run_records import DefaultsDrift, DefaultsPin, ScoringLockedRecord
from promptpotter.domain.search_point import missing_template_vars, strip_rendered_prompt
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.capabilities import resolve_menu, resolve_schema_menu
from promptpotter.infrastructure.llm.pricing import route_priced
from promptpotter.infrastructure.llm.registry import default_routes
from promptpotter.infrastructure.store.dataset_access import (
    ResolvedDataset,
    dataset_connector,
    dataset_experiment,
    declared_pipelines,
    definition_version,
    read_dataset_definition,
    read_dataset_pipeline,
    resolve_dataset,
)
from promptpotter.infrastructure.store.read_model import derived
from promptpotter.infrastructure.store.stores import descend_store, owned_campaign
from promptpotter.shared.errors import (
    BadRequestError,
    DatasetAccessError,
    ManifestUnreadableError,
    NotFoundError,
    PayloadInvalidError,
    StoredConfigInvalidError,
)
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.plugin_registry import BUILT_IN

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.judges import JudgeSpec
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.pool import Pool
    from promptpotter.domain.prompt_block import Presumption
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.treatment import Treatment
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key the judges' fingerprint rides into measurement identity — ONE key over the
whole term → judge mapping. Twin of `connectors/harbor.py::INSTRUMENT_KEY`, named apart because
that one says which task bytes ran and this one which grader read the answer. Both are identity,
neither a tunable — nothing may put either in `param_keys`."""


def resolve_pipeline_config_params(
    active: list[str],
    campaign_overlay: Mapping[str, Any],
    dataset: ResolvedDataset | None,
    schema: PipelineSchema,
    judges: Mapping[str, JudgeSpec] | None = None,
    *,
    experiment: Mapping[str, Any] | None,
    stores: Stores | None,
    workspace: Path | None,
    base_config: Mapping[str, Any] | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    params = merge_declared_layers(
        active,
        campaign_overlay,
        None if dataset is None else dataset.directory,
        schema,
        workspace=workspace,
        base_config=base_config,
        provenance=provenance,
    )
    return apply_identity_layer(
        params,
        active,
        dataset,
        schema,
        stores=stores,
        judges=judges,
        experiment=experiment,
        provenance=provenance,
    )


def merge_declared_layers(
    active: list[str],
    campaign_overlay: Mapping[str, Any],
    dataset_dir: Path | None,
    schema: PipelineSchema,
    *,
    workspace: Path | None,
    base_config: Mapping[str, Any] | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    pipeline_params: dict[str, Any] = {"steps": list(active)}
    if base_config is not None:
        pipeline_params = apply_node_overlay(
            pipeline_params,
            {node: cfg for node, cfg in base_config.items() if node in active},
            schema,
            source="backend",
            provenance=provenance,
        )
    if dataset_dir is not None:
        dataset_overlay = {
            node: cfg
            for node, cfg in load_dataset_node_overlay(dataset_dir).items()
            if node in active
        }
        pipeline_params = apply_node_overlay(
            pipeline_params, dataset_overlay, schema, source="dataset", provenance=provenance
        )
    valid_overrides: dict[str, Any] = {}
    for key, value in campaign_overlay.items():
        if isinstance(value, dict) and key in active:
            valid_overrides[key] = value
        elif isinstance(value, dict):
            logger.debug("merge_declared_layers: skipping override for inactive node %r", key)
        else:
            logger.warning(
                "merge_declared_layers: ignoring non-nested override %r=%r "
                '(use {"node_name": {"param": value}} format)',
                key,
                value,
            )
    pipeline_params = apply_node_overlay(
        pipeline_params, valid_overrides, schema, source="campaign", provenance=provenance
    )
    return _apply_model_floors(pipeline_params, active, schema, workspace, provenance)


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


def _apply_model_floors(
    pipeline_params: dict[str, Any],
    active: list[str],
    schema: PipelineSchema,
    workspace: Path | None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None,
) -> dict[str, Any]:
    answering = schema_as_run(schema, pipeline_params, active, workspace)
    floors: dict[str, dict[str, Any]] = {}
    for name in active:
        node = answering.get_node(name)
        cfg = pipeline_params.get(name)
        if node is None or not isinstance(cfg, dict) or "reasoning_effort" in cfg:
            continue
        if "reasoning_effort" not in node.param_keys | node.param_keys_held:
            continue
        floor = answering.effort_floor(node, model=None)
        if floor is not None:
            floors[name] = {"reasoning_effort": floor}
    return apply_node_overlay(
        pipeline_params, floors, schema, source="model_floor", provenance=provenance
    )


def apply_identity_layer(
    pipeline_params: dict[str, Any],
    active: list[str],
    dataset: ResolvedDataset | None,
    schema: PipelineSchema,
    *,
    stores: Stores | None,
    judges: Mapping[str, JudgeSpec] | None,
    experiment: Mapping[str, Any] | None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    # LAST and unoverridable: the connector's and the judges' ONE channel into the archive key.
    contributions = _identity_contributions(stores, dataset, experiment, judges, active)
    identity = {node: cfg for node, cfg in contributions.items() if node in active}
    if identity:
        pipeline_params = apply_node_overlay(
            pipeline_params, identity, schema, source="identity", provenance=provenance
        )
    return pipeline_params


def overlay_dataset_pipeline(served: dict[str, Any], local: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(served)
    # The definition names the backend it runs: what the operator chose outranks a self-description.
    if local.get("backend_name"):
        out["backend_name"] = local["backend_name"]
    if pipelines := declared_pipelines(local):
        out["pipelines"] = pipelines
    # The operator's model MENU bounds the L1 enum; dropped, the service's catalogue is searched.
    if local.get("available_models"):
        out["available_models"] = local["available_models"]
    out["nodes"] = merge_node_blocks(out.get("nodes") or {}, local.get("nodes") or {})
    return out


def dataset_pipeline_declaration(
    stores: Stores, dataset: ResolvedDataset, experiment: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    definition = read_dataset_definition(dataset.directory)
    if definition is None:
        return None
    connector = connectors.CONNECTORS.table.get(definition.backend_type)
    if connector is None or connector.pipeline_declaration is None:
        return definition.pipeline
    return overlay_dataset_pipeline(
        connector.pipeline_declaration(stores, dataset, experiment), definition.pipeline
    )


def experiment_outside_run(
    stores: Stores, dataset: ResolvedDataset | None
) -> Mapping[str, Any] | None:
    if dataset is None:
        return None
    connector = dataset_connector(dataset)
    read = None if connector is None else dataset_experiment(stores, dataset, connector)
    return None if read is None else read.experiment


def _identity_contributions(
    stores: Stores | None,
    dataset: ResolvedDataset | None,
    experiment: Mapping[str, Any] | None,
    judges: Mapping[str, JudgeSpec] | None,
    active: list[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if dataset is not None:
        connector = dataset_connector(dataset)
        if connector is not None and connector.identity_config is not None:
            if stores is None:
                raise ValueError(f"{dataset.name}: its connector's identity needs the stores")
            out.update(connector.identity_config(stores, dataset, experiment))
    if judges and active:
        # The TERMINAL step: a judge grades the answer that node emits.
        out.setdefault(active[-1], {})[JUDGE_INSTRUMENT_KEY] = judge_instrument(judges)
    return out


def _resolve_active_schema(
    pipeline_schema: PipelineSchema,
    *,
    exclude: list[str],
    narrowing: dict[str, NodeSearchNarrowing],
    dataset_dir: Path | None,
    workspace: Path | None = None,
) -> tuple[list[str], PipelineSchema]:
    active = pipeline_schema.active_steps_excluding(exclude)

    filtered = pipeline_schema
    if exclude:
        filtered = pipeline_schema.filter_to_steps(active)
    # Declared UNION the running chain: `promptpotter-self/panel` declares `l2_context` / `l3_plan`
    # off-chain on purpose, and a dataset declaring fewer nodes than it runs must lose none.
    declared = dataset_declared_nodes(dataset_dir) if dataset_dir is not None else frozenset()
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
    dataset_dir: Path,
    dataset_name: str,
) -> None:
    """Assumes the caller checked ``has_dataset_prompts``."""
    prompt_nodes = [n for n in filtered.prompt_nodes() if n.name in active]
    authored = declared_origin_prompts(dataset_dir, prompt_nodes)
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


KnobType = Literal["boolean", "integer", "number", "string", "array", "object"]

_KNOB_TYPES: tuple[KnobType, ...] = get_args(KnobType)


class KnobRow(StrictModel):
    """One knob of one optimizer node, as every surface offers it: its only served row."""

    key: str = Field(description="The knob's name under `nodes.{node}.config`")
    description: str = Field(
        description="What the knob does — its field description, as plain text (RST literals "
        "unmarked)"
    )
    summary: str = Field(
        description="The description's first sentence, cut at its first clause where that runs "
        "long: the one line a setup surface shows before the whole text"
    )
    type: KnobType = Field(description="JSON Schema type the value takes")
    options: list[str] | None = Field(
        description="The closed set a string knob takes, in declared order; null when open"
    )
    nullable: bool = Field(description="Whether null is a legal value (an opt-in knob, off)")
    minimum: float | None = Field(description="The least legal value, itself legal; null when none")
    exclusive_minimum: float | None = Field(
        description="A bound every legal value lies strictly above; null when none"
    )
    maximum: float | None = Field(
        description="The greatest legal value, itself legal; null when none"
    )
    exclusive_maximum: float | None = Field(
        description="A bound every legal value lies strictly below; null when none"
    )
    declared: Any = Field(description="The value the manifest declares — a campaign's floor")
    value: Any = Field(description="The value this config runs, the manifest's under its overlays")
    movable_by: list[MovableAgent] = Field(
        description="The agents that may move it within a run, as a config row's `movable_by` "
        "reads; `optimizer` where another member steers it, empty where only the operator does"
    )


class NodeKnobs(StrictModel):
    node: str = Field(description="The manifest node that owns these knobs")
    kind: str = Field(description="The node's type: sampler | eliminator | selector | …")
    knobs: list[KnobRow] = Field(description="Its knobs, in declared order")


class KnobRef(StrictModel):
    """One knob of one optimizer node, written to `optimization.nodes.{node}.config.{knob}`."""

    node: str = Field(description="The manifest node that owns the knob")
    knob: str = Field(description="The knob's name under `nodes.{node}.config`")


class PoolDraw(StrictModel):
    """The nodes that read a pool under one knob: one knob, one access, however many readers."""

    switch: KnobRef = Field(description="The knob that sets `access`")
    access: Access = Field(
        description="How they draw under this config: `off` not at all, `open` a window to "
        "suggest from, `closed` an entry's id only"
    )
    nodes: list[str] = Field(
        description="Every node reading the pool as this knob sets it, in walk order; potter's "
        "critique, L2 and L3 read it as `l1_generate` is shown it, to lay out what L1 sees"
    )


class PoolRow(StrictModel):
    pool: str = Field(description="The pool's state kind, e.g. `block_pool`")
    source: PoolSource = Field(description="Where its entries come from")
    initializer: str = Field(description="The run-init node writing it once, before round 0")
    active: list[str] = Field(description="The initializer's `active` paths: the entries in play")
    offered: list[PoolOffer] = Field(
        description="Every path `active` may name — `*` first, each leading run of an entry's "
        "group before what it holds, each entry id — with what it reads as and the entries it "
        "keeps; a demo pool's are the split its Start draws, none before that split is known"
    )
    drawn_by: list[PoolDraw]


class OptimizerNodePrompt(StrictModel):
    """The prompt an optimizer node runs under a config, before any L4 override."""

    fields: dict[str, str] = Field(description="Every prompt field by name, its edits laid on")
    edited: list[str] = Field(
        description="The fields this config sets over the template, each written to "
        "`optimization.nodes.{node}.config.{field}`"
    )
    version: str = Field(description="The `prompt_version` the node's config names")
    versions_declared: int = Field(
        description="How many versions the manifest declares for the node's prompt family — a "
        "surface showing this one says so where there are more"
    )


class OptimizerSetup(StrictModel):
    """What the selected optimizer runs under a config, derived once for check-in and campaign."""

    main: list[KnobRef] = Field(description="The knobs a setup surface leads with, in order")
    knobs: list[NodeKnobs] = Field(description="Every member taking a knob, in declared order")
    prompts: dict[str, OptimizerNodePrompt] = Field(
        description="By node, each node running a prompt; a node naming no `prompt_family` has "
        "no entry"
    )
    arms_per_round: int = Field(description="Arms each round tests")
    proposers: list[str] = Field(
        description="The nodes making each round's arms under this config, in walk order: one "
        "that opens or adds arms and adds none here is off, and absent"
    )
    pools: list[PoolRow] = Field(description="What the members draw from")


class CampaignPipelineResponse(StrictModel):
    """One campaign's pipeline at one searchpoint — the body of ``GET /campaigns/{id}/pipeline``.
    The peer of ``resolve_dataset`` one question up: that seam answers which bytes are on disk,
    this one which values a campaign runs (``architecture.md`` § Two resolution seams)."""

    campaign_id: str
    cycle_id: str
    dataset_name: str
    connector: str
    backend_type: str
    self_optimization: bool = Field(
        description="This campaign optimizes the optimizer itself (L4): one measured row is a "
        "whole inner campaign, so it has no registered backend and no per-sample data of its own"
    )
    optimizer: str = Field(
        description="The optimizer manifest the addressed course runs — the one answer a surface "
        "reads which optimizer's graph, knobs and analytics apply by, a check-in's draft included"
    )
    optimizer_setup: OptimizerSetup = Field(
        description="That optimizer as the addressed course runs it — the manifest's knobs under "
        "the campaign's and the cycle seed's overlays, its arms and its pools"
    )
    params: dict[str, Any] = Field(
        description="Resolved config as the engine holds it — the bytes each arm of a round file "
        "carries as `resolved_params`, which makes that field this endpoint's check"
    )
    node_config_schema: dict[str, list[NodeConfigParam]]
    view: PipelineView | None
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Keyed by the provider and model each node's rows carry AT this searchpoint"
    )
    reach: dict[str, NodeReach]
    nests: list[NestedPipelineRef] = Field(
        description="The inner pipelines this chain nests, one per dataset its cells measure — "
        "the L4 drill-in, on this read"
    )
    is_single_node: bool


def _identity_keys(
    provenance: Mapping[str, dict[str, ParamSource]],
) -> dict[str, frozenset[str]]:
    return {
        node: owned
        for node, stamps in provenance.items()
        if (owned := frozenset(k for k, source in stamps.items() if source == "identity"))
    }


def _recorded_identity(
    params: dict[str, Any],
    recorded: Mapping[str, Any],
    provenance: Mapping[str, dict[str, ParamSource]],
) -> dict[str, Any]:
    """A MEASURED point keeps the identity it ran under; recomputing is right only on the run path."""
    out = dict(params)
    for node, owned in _identity_keys(provenance).items():
        node_recorded = recorded.get(node)
        if not isinstance(node_recorded, dict):
            continue
        merged = dict(out.get(node) or {})
        merged.update({k: node_recorded[k] for k in owned if k in node_recorded})
        out[node] = merged
    return out


def _evolved_overlay(stores: Stores, at: SubjectSpec) -> tuple[dict[str, Any], dict[str, Any]]:
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


def measurement_node(view: PipelineView | None) -> str | None:
    return next((n.id for n in (view.nodes if view else []) if n.kind == "measurement"), None)


def nested_pipeline_refs(
    dataset: ResolvedDataset,
    experiment: Mapping[str, Any] | None,
    view: PipelineView | None,
) -> list[NestedPipelineRef]:
    """One per dataset the cells' inner campaigns measure, by name."""
    node = measurement_node(view)
    connector = dataset_connector(dataset)
    if node is None or experiment is None or connector is None:
        return []
    if not is_self_optimization(connector.name):
        return []
    resolved = parse_resolved(experiment)
    return [
        NestedPipelineRef(node=node, dataset=name)
        for name in sorted(set(resolved.datasets.values()))
    ]


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


def _dataset_of(stores: Stores, campaign: Campaign) -> ResolvedDataset | None:
    try:
        return resolve_dataset(stores, campaign.dataset_name)
    except DatasetAccessError:
        # A campaign outlives its dataset dir; its frozen config still answers.
        return None


def _criterion_locked[S](stores: Stores, hop: CycleHop, scoring: S) -> S | dict[str, str]:
    lock = stores.campaigns.last(hop, ScoringLockedRecord)
    return lock.locked if lock is not None and lock.declared == scoring else scoring


def draft_config(
    stores: Stores, draft: DraftCampaign, *, session_chain: Sequence[str] | None = None
) -> CampaignConfig:
    """What Start declares and every read before it shows; only Start holds a ``session_chain``.
    A free account's takes the tier's layer last, bound to each node's model."""
    config = _draft_declared(stores, draft, session_chain)
    if not on_free_tier(stores):
        return config
    schema = parse_pipeline_response(rendered_pipeline_json(draft))
    nodes = {
        n.name: {**n.current_config, **config.pipeline_overlay.get(n.name, {})}
        for n in schema.declared_nodes
        if n.is_llm
    }
    return with_free_tier_layer(config, nodes)


def _draft_declared(
    stores: Stores, draft: DraftCampaign, session_chain: Sequence[str] | None
) -> CampaignConfig:
    canonical = draft.source_dataset
    if canonical is None:
        # A fresh upload commits its overlay and chain into its own `dataset.yaml::pipeline`.
        return with_optimization(default_campaign_config(draft), draft.optimization_overrides)
    dataset_dir = resolve_dataset(stores, canonical).directory
    declared = load_dataset_campaign_config(dataset_dir)
    overrides, narrowing = split_overlay(draft.pipeline_overlay)
    update: dict[str, Any] = {
        "pipeline_overlay": {**declared.pipeline_overlay, **overrides},
        "optimizer_narrowing": {**declared.optimizer_narrowing, **narrowing},
    }
    # The file's block carries anchors a draft's two fields cannot: it stands until they move.
    if scoring_choice(declared.scoring) != (draft.scoring_matcher, draft.scoring_dials):
        update["scoring"] = draft_scoring_block(draft)
    if draft.pipeline_steps:
        chain = _declared_chain(dataset_dir) if session_chain is None else session_chain
        # Via `exclude_nodes`: a reused dataset's `pipelines.default` stays the first campaign's.
        update["exclude_nodes"] = [n for n in chain if n not in draft.pipeline_steps]
    return with_optimization(declared, draft.optimization_overrides).model_copy(update=update)


def _declared_chain(dataset_dir: Path) -> list[str]:
    return declared_pipelines(read_dataset_pipeline(dataset_dir) or {}).get("default", [])


def _locked(stores: Stores, hop: CycleHop, config: CampaignConfig) -> CampaignConfig:
    locked = _criterion_locked(stores, hop, config.scoring)
    return config if locked == config.scoring else config.model_copy(update={"scoring": locked})


def defaults_drift(campaign: Campaign, *, running: Treatment) -> DefaultsDrift | None:
    """The one answer to whether what the declaration is a delta over moved since its mint."""
    declared = DefaultsPin(
        bench=campaign.declaration.bench_defaults_pin, treatment=campaign.treatment
    )
    now = DefaultsPin(bench=bench_defaults_pin(), treatment=running)
    return None if now == declared else DefaultsDrift(declared=declared, running=now)


def resolve_campaign_config(
    stores: Stores, campaign: Campaign, hop: CycleHop | None
) -> CampaignConfig:
    """The declaration's effective config, the criterion its dials lock on the origin laid on."""
    try:
        declared = effective_config(campaign.declaration)
    except ValidationError as exc:
        raise ManifestUnreadableError(
            path=f"campaigns/{campaign.campaign_id}/campaign.json::declaration",
            reason=f"{exc.error_count()} field(s) invalid — {exc.errors()[0]['msg']}",
        ) from exc
    root = _locked(stores, campaign.root_hop, declared)
    if hop is None:
        return root
    return _locked(stores, hop, apply_cycle_seed(root, stores.campaigns.read_cycle_seed(hop)))


@dataclass(frozen=True)
class _Merge:
    declared: PipelineSchema
    filtered: PipelineSchema
    active: list[str]
    cfg: CampaignConfig
    params: dict[str, Any]
    provenance: dict[str, dict[str, ParamSource]]
    # The reading account is free: it is offered only the tier's task models.
    free: bool


@dataclass(frozen=True)
class _CampaignMerge(_Merge):
    dataset: ResolvedDataset | None
    hop: CycleHop
    raw: dict[str, Any] | None
    seed: CycleSeed | None

    def with_seed(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.seed is None or not self.seed.pipeline_overlay:
            return params
        return apply_node_overlay(
            params,
            self.seed.pipeline_overlay,
            self.filtered,
            source="seed",
            provenance=self.provenance,
        )


def _draft_merge(stores: Stores, draft: DraftCampaign) -> _Merge:
    schema = parse_pipeline_response(rendered_pipeline_json(draft))
    cfg = draft_config(stores, draft)
    # Still the operator's own layer here, where a fresh upload's Start files it in `dataset.yaml::pipeline`.
    overrides, narrowing = split_overlay(draft.pipeline_overlay)
    active, filtered = _resolve_active_schema(
        schema,
        # The draft's schema already runs the chosen chain; excluding drops what it can still add.
        exclude=[],
        narrowing={**cfg.optimizer_narrowing, **narrowing},
        dataset_dir=None,
    )
    provenance: dict[str, dict[str, ParamSource]] = {}
    params = merge_declared_layers(
        active,
        merge_pipeline_params(cfg.pipeline_overlay, overrides, None) or {},
        None,
        filtered,
        workspace=stores.base_dir,
        base_config={n.name: dict(n.current_config) for n in filtered.declared_nodes},
        provenance=provenance,
    )
    return _Merge(
        # What was on OFFER before this draft narrowed it, so a menu cannot shrink to its own pick.
        declared=parse_pipeline_response(declared_pipeline_json(draft)),
        filtered=filtered,
        active=active,
        cfg=cfg,
        params=params,
        provenance=provenance,
        free=on_free_tier(stores),
    )


def _campaign_merge(stores: Stores, campaign: Campaign, at: SubjectSpec) -> _CampaignMerge:
    dataset = _dataset_of(stores, campaign)
    dataset_dir = None if dataset is None else dataset.directory
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=at.cycle_id or campaign.root_cycle_id)
    # The cycle's recorded merge first: the committed dataset file carries no `param_keys` at all.
    raw = stores.campaigns.read_resolved_run(hop).declaration
    if raw is None and dataset is not None:
        raw = dataset_pipeline_declaration(stores, dataset, experiment_outside_run(stores, dataset))
    schema = parse_pipeline_response(raw or {"nodes": {}, "pipelines": {"default": []}})
    seed = stores.campaigns.read_cycle_seed(hop)
    cfg = resolve_campaign_config(stores, campaign, hop)
    active, filtered = _resolve_active_schema(
        schema,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        dataset_dir=dataset_dir,
    )
    provenance: dict[str, dict[str, ParamSource]] = {}
    params = merge_declared_layers(
        active,
        cfg.pipeline_overlay,
        dataset_dir,
        filtered,
        workspace=stores.base_dir,
        provenance=provenance,
    )
    return _CampaignMerge(
        declared=schema,
        filtered=filtered,
        active=active,
        cfg=cfg,
        params=params,
        provenance=provenance,
        free=on_free_tier(stores),
        dataset=dataset,
        hop=hop,
        raw=raw,
        seed=seed,
    )


def _served_menu(m: _Merge, params: dict[str, Any], workspace: Path | None) -> CapabilityMenu:
    return resolve_schema_menu(_routed_as_run(m.filtered, params, m.active), workspace=workspace)


def campaign_admits(cfg: CampaignConfig, schema: PipelineSchema) -> frozenset[Presumption]:
    """What a campaign's chain and scorer let a block presume: its pools draw and its treatment hashes this one set."""
    per_sample = split_scoring_block(cfg.scoring, judge_instrument=None).per_sample
    return admits(schema, matcher_of(per_sample))


def mint_facts(cfg: CampaignConfig, demo: Sequence[Sample], schema: PipelineSchema) -> MintFacts:
    return MintFacts(demo, campaign_admits(cfg, schema))


def _draft_mint(stores: Stores, draft: DraftCampaign, m: _Merge) -> MintFacts | None:
    """The demo split the draft's Start will draw, so its pool offers the keys before Start."""
    samples = draft_samples(stores, draft)
    if samples is None:
        return None
    demo = partition_bank(samples, m.cfg.dataset_split, seed=m.cfg.run_seed).demo
    return mint_facts(m.cfg, demo, m.filtered)


def _cycle_mint(stores: Stores, m: _CampaignMerge) -> MintFacts | None:
    drawn = stores.campaigns.read_bank_partition(m.hop)
    bank = None if drawn is None else stores.tenant_datasets.bank(drawn.dataset_hash)
    if drawn is None or bank is None:
        return None
    return mint_facts(m.cfg, restored_partition(bank.samples, drawn).demo, m.filtered)


def _pool_draws(selected: SelectedOptimizer, pool: type[Pool]) -> list[PoolDraw]:
    grouped: dict[tuple[str, str], PoolDraw] = {}
    for member in selected.members:
        if not isinstance(draw := member.draws.get(pool), KnobDraw):
            continue
        ref = KnobRef(node=selected.knob_owner(member, draw.knobs), knob=draw.knob)
        held = grouped.setdefault(
            (ref.node, ref.knob),
            PoolDraw(switch=ref, access=selected.access(member, pool), nodes=[]),
        )
        held.nodes.append(member.name)
    return list(grouped.values())


def _node_knobs(selected: SelectedOptimizer) -> list[NodeKnobs]:
    nodes: list[NodeKnobs] = []
    params = _param_rows(selected)
    for node in selected.member_nodes:
        schema = optimizers.NODE_MEMBERS.get(node).knobs.model_json_schema()
        declared = selected.declared_knobs(node).model_dump(mode="json")
        value = selected.knobs(node).model_dump(mode="json")
        movable = {p.key: p.movable_by for p in params.get(node, [])}
        rows = [
            _knob_row(key, prop, schema.get("$defs") or {}, declared, value, movable[key])
            for key, prop in (schema.get("properties") or {}).items()
        ]
        if rows:
            nodes.append(NodeKnobs(node=node, kind=str(selected.node(node).kind), knobs=rows))
    return nodes


def _node_prompts(selected: SelectedOptimizer) -> dict[str, OptimizerNodePrompt]:
    out: dict[str, OptimizerNodePrompt] = {}
    declared = list(selected.manifest.resolved_prompts)
    for node in selected.schema.declared_nodes:
        config = selected.node_config(node.name)
        if prompt_key(config) is None:
            continue
        family = str(config["prompt_family"])
        out[node.name] = OptimizerNodePrompt(
            fields=node_prompt(node.name, config, selected.manifest).model_dump(),
            edited=list(prompt_edits(config)),
            version=str(config.get("prompt_version") or ""),
            versions_declared=sum(k.partition("/")[0] == family for k in declared),
        )
    return out


def optimizer_setup(cfg: CampaignConfig, mint: MintFacts | None) -> OptimizerSetup:
    selected = select_optimizer(cfg.optimization)
    return OptimizerSetup(
        main=[
            KnobRef(node=node, knob=knob)
            for node, _, knob in (ref.partition(".") for ref in selected.manifest.main)
        ],
        knobs=_node_knobs(selected),
        prompts=_node_prompts(selected),
        arms_per_round=selected.arms_per_round,
        proposers=[
            p.name for p in selected.plan.proposers if p.proposes == "edits" or p.arms(selected) > 0
        ],
        pools=[
            PoolRow(
                pool=initializer.pool.kind,
                source=initializer.pool.source,
                initializer=initializer.name,
                active=initializer.active(selected),
                offered=offers(initializer.held(selected, mint)),
                drawn_by=_pool_draws(selected, initializer.pool),
            )
            for initializer in selected.plan.initializers
        ],
    )


def _rows(m: _Merge, params: dict[str, Any]) -> dict[str, list[NodeConfigParam]]:
    return m.filtered.node_config_schema(
        values={n: c for n, c in params.items() if isinstance(c, dict)},
        sources=m.provenance,
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
    params: dict[str, Any],
    workspace: Path,
    *,
    campaign_id: str,
    cycle_id: str,
    dataset_name: str,
    connector: str,
    backend_type: str,
    nests: list[NestedPipelineRef],
    mint: MintFacts | None,
) -> CampaignPipelineResponse:
    rows = _rows(m, params)
    return CampaignPipelineResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=dataset_name,
        connector=connector,
        backend_type=backend_type,
        self_optimization=is_self_optimization(backend_type),
        optimizer=m.cfg.optimization.optimizer,
        optimizer_setup=optimizer_setup(m.cfg, mint),
        params=params,
        node_config_schema=rows,
        view=m.filtered.view,
        node_output_schema=resolved_output_schemas(m.filtered, params),
        model_capabilities=_served_menu(m, params, workspace),
        reach=reach_map(rows),
        nests=nests,
        is_single_node=m.filtered.is_single_node,
    )


def resolve_pipeline_for_draft(stores: Stores, draft: DraftCampaign) -> CampaignPipelineResponse:
    m = _draft_merge(stores, draft)
    return _response(
        m,
        _draft_params(m),
        stores.base_dir,
        # A draft is no campaign and runs no cycle until its Start mints both.
        campaign_id=draft.draft_id,
        cycle_id="",
        dataset_name=draft.slug,
        connector=draft.connector,
        backend_type=draft.connector,
        # A dataset dir is what declares inner cells, and a check-in has none yet.
        nests=[],
        mint=_draft_mint(stores, draft, m),
    )


def draft_pipeline(
    stores: Stores, draft: DraftCampaign
) -> tuple[dict[str, Any], dict[str, list[NodeConfigParam]], PipelineSchema]:
    """What the draft's nodes run with, their rows, and the chain they run in, before Start commits any of it."""
    m = _draft_merge(stores, draft)
    params = _draft_params(m)
    return params, _rows(m, params), m.filtered


def _draft_params(m: _Merge) -> dict[str, Any]:
    return apply_identity_layer(
        m.params,
        m.active,
        None,
        m.filtered,
        stores=None,
        judges=m.cfg.judges,
        experiment=None,
        provenance=m.provenance,
    )


def resolve_pipeline_for_campaign(
    stores: Stores,
    campaign: Campaign,
    *,
    at: SubjectSpec,
) -> CampaignPipelineResponse:
    """``stores`` is already the store *campaign* lives in: the caller descends ``at.inside`` first."""
    m = _campaign_merge(stores, campaign, at)
    # Recorded first: a live resolve recomputes a banked identity off a roster it never ran.
    experiment = stores.campaigns.read_resolved_run(m.hop).experiment or experiment_outside_run(
        stores, m.dataset
    )
    params = apply_identity_layer(
        m.params,
        m.active,
        m.dataset,
        m.filtered,
        stores=stores,
        judges=m.cfg.judges,
        experiment=experiment,
        provenance=m.provenance,
    )
    params = m.with_seed(params)
    evolved, measured = _evolved_overlay(stores, at)
    if evolved:
        params = apply_node_overlay(
            params, evolved, m.filtered, source="evolved", provenance=m.provenance
        )
    # LAST: a measured point's identity is a fact of the measurement, not of today's files.
    if measured:
        params = _recorded_identity(params, measured, m.provenance)
    return _response(
        m,
        params,
        stores.base_dir,
        campaign_id=campaign.campaign_id,
        cycle_id=m.hop.cycle_id,
        dataset_name=campaign.dataset_name,
        connector=str((m.raw or {}).get("backend_name", "")),
        # The campaign's DECLARED kind — one definition serves every campaign on the slug.
        backend_type=campaign.connector,
        nests=[]
        if m.dataset is None
        else nested_pipeline_refs(m.dataset, experiment, m.filtered.view),
        mint=_cycle_mint(stores, m),
    )


def _pipeline_subject(at: str, campaign_id: str) -> SubjectSpec:
    if not at:
        return SubjectSpec("campaign", campaign_id)
    spec = parse_subject(at)
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


class ForkPreviewRequest(StrictModel):
    pipeline_overlay: dict[str, Any] = Field(
        description="The `nodes.*.config` overlay the fork would carry, as its `CycleSeed` sends it"
    )


class ForkPreviewResponse(StrictModel):
    """What `POST /commands/fork-cycle` would decide about this steer, asked without forking."""

    steers_disallowed_model: bool = Field(
        description=(
            "The overlay picks a responder the campaign's declared `optimizer_narrowing` never "
            "sanctioned, or touches a cost lever, which no permitted set can sanction. True means "
            "the fork is the babysit act (decisions.md § 0005): it needs `campaign.babysit` (404 without it) "
            "and stamps the branch grade C."
        )
    )
    permitted_models: dict[str, list[str]] = Field(
        description=(
            "What the verdict above was compared AGAINST, per node — this campaign's declared "
            "`optimizer_narrowing[node].param_allowed_values.model`. Served beside the verdict so "
            "a surface naming the permitted models cannot name a different set than the one that "
            "decided. A node absent from it sanctions nothing, which is why any model steer there "
            "counts."
        )
    )


def fork_preview(stores: Stores, campaign_id: str, overlay: dict[str, Any]) -> ForkPreviewResponse:
    narrowing = resolve_campaign_config(
        stores, owned_campaign(stores, campaign_id), None
    ).optimizer_narrowing
    return ForkPreviewResponse(
        steers_disallowed_model=steers_disallowed_model(narrowing, overlay),
        permitted_models=permitted_models_from_narrowing(narrowing),
    )


class DatasetPipelineResponse(StrictModel):
    """One dataset's pipeline as DECLARED — the body of ``GET /datasets/{name}/pipeline``. Topology
    only: the level a ``nests`` pointer names, which no campaign has to have run."""

    connector: str
    view: PipelineView | None
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Every param each node carries, valueless — what `reach` is summed over"
    )
    reach: dict[str, NodeReach]
    nests: list[NestedPipelineRef] = Field(
        description="The pipelines this one nests in turn, one per dataset its cells measure; the "
        "only wire naming them before a cell has spawned"
    )


def resolve_pipeline_for_dataset(stores: Stores, dataset_name: str) -> DatasetPipelineResponse:
    dataset = resolve_dataset(stores, dataset_name)
    experiment = experiment_outside_run(stores, dataset)
    raw = dataset_pipeline_declaration(stores, dataset, experiment)
    if raw is None:
        raise NotFoundError(f"Dataset '{dataset_name}' declares no pipeline")
    cfg = load_dataset_campaign_config(dataset.directory)
    _, filtered = _resolve_active_schema(
        parse_pipeline_response(raw),
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        dataset_dir=dataset.directory,
    )
    rows = filtered.node_config_schema()
    return DatasetPipelineResponse(
        connector=str(raw.get("backend_name", "")),
        view=filtered.view,
        node_config_schema=rows,
        reach=reach_map(rows),
        nests=nested_pipeline_refs(dataset, experiment, filtered.view),
    )


class OptimizerPipelineResponse(StrictModel):
    """An optimizer's graph as its manifest declares it, under no config: what a config runs, its knobs and prompts included, is `OptimizerSetup`."""

    view: PipelineView | None = Field(
        description="The graph topology — the same shape a campaign pipeline serves"
    )
    measurement_node: str | None = Field(
        description="The node of `view` that runs the measurement — where a campaign's pipeline "
        "nests under this graph, as each `nests[].node` names it per campaign. Null where the manifest "
        "declares no measurement node."
    )
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Per-node typed config rows of what is no member's knob — an llm node's call "
        "config and prompt fields; a knob's one row is `OptimizerSetup.knobs`"
    )
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Optimizer-LOCKED is not unpriced: the model is fixed, but which effort rungs "
        "it accepts and what a round costs are the facts every other node's rows need too"
    )
    reach: dict[str, NodeReach] = Field(
        description="Where the search reaches per node, summed off every param it carries — the "
        "rows above and its knobs — rather than in the browser, the same reading a campaign "
        "pipeline serves"
    )


def _param_rows(selected: SelectedOptimizer) -> dict[str, list[NodeConfigParam]]:
    """Every param of every node, knobs included: what reach sums and a knob's `movable_by` reads."""
    return selected.schema.node_config_schema(selected.steered_axes)


def _knob_keys(selected: SelectedOptimizer, node: str) -> frozenset[str]:
    if node not in selected.member_nodes:
        return frozenset()
    return frozenset(optimizers.NODE_MEMBERS.get(node).knobs.model_fields)


def resolve_pipeline_for_optimizer(stores: Stores, optimizer: str) -> OptimizerPipelineResponse:
    selected = resolve_optimizer(optimizer, {}, gate="off")
    schema = selected.schema
    # One reading for the glyph and each padlock: a knob's padlock rides its `KnobRow`.
    rows = _param_rows(selected)
    return OptimizerPipelineResponse(
        view=schema.view,
        measurement_node=measurement_node(schema.view),
        node_config_schema={
            node: [r for r in node_rows if r.key not in _knob_keys(selected, node)]
            for node, node_rows in rows.items()
        },
        node_output_schema=schema.node_output_schemas(),
        reach=reach_map(rows),
        # Per tenant: a hand-authored catalogue override lives in their workspace.
        model_capabilities=resolve_schema_menu(schema, workspace=stores.base_dir),
    )


def _knob_type(kind: Mapping[str, Any]) -> KnobType:
    declared = kind.get("type") or ("object" if "properties" in kind else "string")
    for known in _KNOB_TYPES:
        if known == declared:
            return known
    raise ValueError(f"knob type {declared!r} is none of {', '.join(_KNOB_TYPES)}")


_RST_LITERAL = re.compile(r"``(.+?)``", re.DOTALL)
# Read on the marked text: a stop before a capital, a literal or the end ends a sentence; "e.g. x"
# and "0.15" end none.
_SENTENCE_END = re.compile(r"(?<=[.!?])(?=\s+[A-Z(`]|\s*$)")
_CLAUSE_END = re.compile(r"[:;](?=\s)|\s—\s")
_SUMMARY_CHARS = 120
# A clause shorter than this is a symbol being named (`T: …`, `b: …`), no summary.
_CLAUSE_MIN_CHARS = 24


def _plain(text: str) -> str:
    return _RST_LITERAL.sub(r"\1", text)


def _summary(marked: str) -> str:
    first = _plain(_SENTENCE_END.split(marked.strip(), maxsplit=1)[0].strip())
    if len(first) <= _SUMMARY_CHARS:
        return first
    clause = _CLAUSE_END.search(first, _CLAUSE_MIN_CHARS)
    return first[: clause.start()].rstrip() if clause else first


def _knob_row(
    key: str,
    prop: Mapping[str, Any],
    defs: Mapping[str, Any],
    declared: Mapping[str, Any],
    value: Mapping[str, Any],
    movable_by: list[MovableAgent],
) -> KnobRow:
    options = [
        defs[ref["$ref"].rsplit("/", 1)[-1]] if "$ref" in ref else ref
        for ref in prop.get("anyOf") or [prop]
    ]
    nullable = any(o.get("type") == "null" for o in options)
    kind = next(o for o in options if o.get("type") != "null")
    enum = kind.get("enum") or ([kind["const"]] if "const" in kind else None)
    description = str(prop.get("description") or "")
    return KnobRow(
        key=key,
        description=_plain(description),
        summary=_summary(description),
        type=_knob_type(kind),
        options=[str(v) for v in enum] if enum else None,
        nullable=nullable,
        minimum=kind.get("minimum"),
        exclusive_minimum=kind.get("exclusiveMinimum"),
        maximum=kind.get("maximum"),
        exclusive_maximum=kind.get("exclusiveMaximum"),
        declared=declared[key],
        value=value[key],
        movable_by=movable_by,
    )


class OptimizerEntry(StrictModel):
    """One optimizer this install can run, as a picker offers it."""

    name: str = Field(description="The manifest name, as `optimization.optimizer` names it")
    version: str = Field(description="The manifest's own version")
    paper: str | None = Field(
        description="The citation a paper preset reproduces; its declared knob values are that "
        "paper's configuration. Null for an optimizer reproducing none"
    )
    origin: str | None = Field(
        description="The installed package that registered it, as `<distribution>: <entry point>`; "
        "null for one shipped with PromptPotter"
    )


class OptimizerRoster(StrictModel):
    """The optimizers this install can run, the default first: one per manifest the registry holds."""

    default: str = Field(description="What a campaign naming no `optimization.optimizer` runs")
    optimizers: list[OptimizerEntry] = Field(description="The roster, the default first")


def optimizer_roster() -> OptimizerRoster:
    default = OptimizationConfig.model_fields["optimizer"].default
    names = sorted(optimizers.MANIFESTS.table, key=lambda n: (n != default, n))
    origins = optimizers.MANIFESTS.origins
    entries = []
    for name in names:
        selected = resolve_optimizer(name, {}, gate="off")
        origin = None if origins[name] == BUILT_IN else origins[name]
        entries.append(
            OptimizerEntry(name=name, version=selected.version, paper=selected.paper, origin=origin)
        )
    return OptimizerRoster(default=default, optimizers=entries)


# A prompt, a description and a nested value are no setting one line can print.
_RUNS_WITH_KINDS = frozenset({"model", "enum", "number", "bool", "string"})
_ROOT_UNRESOLVED = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    NotFoundError,
    StoredConfigInvalidError,
    ManifestUnreadableError,
    PayloadInvalidError,
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


def line_config(stores: Stores, campaign: Campaign, hop: CycleHop) -> CampaignConfig | None:
    """``None`` is a row whose config-bound readings are served null, never a failed list."""
    try:
        config = resolve_campaign_config(stores, campaign, hop)
        # A config naming no formula resolves and still grades nothing.
        scorer_of(config, verifier_graded=False)
    except _ROOT_UNRESOLVED:
        # Silent: `campaign_runs_with` logs the same root's cause, once per change.
        return None
    return config


def _root_key(stores: Stores, campaign: Campaign) -> tuple[Any, ...]:
    # VALUES, never the ledger's stat: a running root appends there on every sample.
    seed = stores.campaigns.read_cycle_seed(campaign.root_hop)
    declaration = stores.campaigns.read_resolved_run(campaign.root_hop).declaration
    lock = stores.campaigns.last(campaign.root_hop, ScoringLockedRecord)
    return (
        campaign.declaration.digest,
        None if lock is None else stable_hash(lock.locked),
        campaign.root_cycle_id,
        definition_version(stores, campaign.dataset_name),
        None if declaration is None else stable_hash(declaration),
        None if seed is None else stable_hash(seed.model_dump(mode="json")),
    )


def _runs_with(stores: Stores, campaign: Campaign) -> CampaignRunsWith:
    m = _campaign_merge(stores, campaign, SubjectSpec("campaign", campaign.campaign_id))
    stripped = strip_rendered_prompt(m.with_seed(m.params))
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
                RunsWithParam(node=name, key=key, value=cfg[key], source=m.provenance[name][key])
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


def configure_and_apply_pipeline(session: Session, campaign_config: CampaignConfig) -> Session:

    exclude = list(campaign_config.exclude_nodes)
    dataset_name = session.measured_dataset
    dataset = session.dataset
    dataset_dir = None if dataset is None else dataset.directory

    active, filtered = _resolve_active_schema(
        session.pipeline_schema,
        exclude=exclude,
        narrowing=campaign_config.optimizer_narrowing,
        dataset_dir=dataset_dir,
        workspace=session.store.base_dir,
    )

    provenance: dict[str, dict[str, ParamSource]] = {}
    pipeline_params = resolve_pipeline_config_params(
        active,
        campaign_config.pipeline_overlay,
        dataset,
        filtered,
        judges=campaign_config.judges,
        experiment=session.backend_client.workload.experiment,
        stores=session.store,
        workspace=session.store.base_dir,
        provenance=provenance,
    )

    ships_prompts = dataset_dir is not None and has_dataset_prompts(dataset_dir)
    _validate_prompt_reach(
        filtered=filtered,
        active=active,
        has_starting_prompts=ships_prompts,
        prompt_fields_as_node_params=session.backend_client.prompt_fields_as_node_params,
        dataset_name=dataset_name,
    )

    if dataset_dir is not None and ships_prompts:
        _apply_starting_prompts(
            pipeline_params,
            filtered=filtered,
            active=active,
            dataset_dir=dataset_dir,
            dataset_name=dataset_name,
        )

    _validate_model_ownership(
        pipeline_params, filtered=filtered, active=active, dataset_name=dataset_name
    )
    # Asked here so a channel the connector cannot run stops init, not every cell of the origin.
    session.backend_client.prompt_delivery(pipeline_params)

    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    logger.info("Active nodes: %s%s", ", ".join(active), excl_str)

    as_run = schema_as_run(filtered, pipeline_params, active, session.store.base_dir)
    # A bound cycle's seed is laid LAST, over the campaign's overrides, for that cycle alone.
    seed = session.store.campaigns.read_cycle_seed(session.hop) if session.cycle_id else None
    if seed is not None and seed.pipeline_overlay:
        pipeline_params = apply_node_overlay(pipeline_params, seed.pipeline_overlay, as_run)
    return replace(
        session,
        pipeline_schema=as_run,
        pipeline_params=pipeline_params,
        identity_keys=_identity_keys(provenance),
    )
