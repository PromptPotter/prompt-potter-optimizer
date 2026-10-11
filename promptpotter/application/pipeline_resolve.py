from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field, ValidationError

from promptpotter import connectors
from promptpotter.application.campaign_config import (
    CampaignConfig,
    apply_cycle_seed,
    load_campaign_config,
    under_record,
)
from promptpotter.application.datasets.authored import (
    dataset_campaign_path,
    load_dataset_campaign_config,
)
from promptpotter.application.datasets.draft_campaign import (
    DraftCampaign,
    declared_pipeline_json,
    draft_campaign_config,
    load_checkin_draft,
    rendered_pipeline_json,
)
from promptpotter.application.datasets.prompts import (
    dataset_declared_nodes,
    has_dataset_prompts,
    load_dataset_node_overlay,
    load_node_prompt,
)
from promptpotter.application.evidence.subjects import SubjectSpec, parse_subject
from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import (
    StartPrompt,
    resolve_optimizer,
    select_optimizer,
)
from promptpotter.application.runner.inner.tasks import (
    inner_tasks_path,
    is_self_optimization,
    load_inner_tasks,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.pipeline_overlay import fold_output_contract, node_config_items
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
    NodeReach,
    NodeSearchNarrowing,
    ParamSource,
    PipelineView,
    reach_map,
)
from promptpotter.domain.search_point import PROMPT_STRING_FIELDS, strip_rendered_prompt
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.llm.capabilities import resolve_menu, resolve_schema_menu
from promptpotter.infrastructure.llm.registry import normalize_model_id
from promptpotter.infrastructure.runtime_flags import is_checkin
from promptpotter.infrastructure.store.campaign_store.ledger_scan import scan_ledger_scoring_lock
from promptpotter.infrastructure.store.dataset_access import (
    DatasetAccessError,
    dataset_experiment,
    dataset_pipeline_path,
    readable_dataset_dir,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.infrastructure.store.layout import CycleLayout
from promptpotter.infrastructure.store.read_model import derived, file_sig
from promptpotter.infrastructure.store.stores import descend_store, owned_campaign
from promptpotter.judges.registry import judge_instrument
from promptpotter.shared.errors import (
    BadRequestError,
    CellUnscoreableError,
    NotFoundError,
    PayloadInvalidError,
    StoredConfigInvalidError,
)
from promptpotter.shared.hashing import stable_hash

if TYPE_CHECKING:
    from pathlib import Path

    from promptpotter.connectors.protocol import Connector
    from promptpotter.domain.campaign import Campaign
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.run_records import CycleSeed
    from promptpotter.infrastructure.store.stores import Stores
    from promptpotter.judges.protocol import JudgeSpec

logger = logging.getLogger(__name__)

JUDGE_INSTRUMENT_KEY = "judge_instrument"
"""The node-config key the judges' fingerprint rides into measurement identity — ONE key over the
whole term → judge mapping. Twin of `connectors/harbor.py::INSTRUMENT_KEY`, named apart because
that one says which task bytes ran and this one which grader read the answer. Both are identity,
neither a tunable — nothing may put either in `param_keys`."""


__all__ = [
    "CampaignPipelineResponse",
    "CampaignRunsWith",
    "DatasetPipelineResponse",
    "OptimizerPipelineResponse",
    "RunsWithParam",
    "VendorModels",
    "apply_identity_layer",
    "apply_node_overlay",
    "campaign_runs_with",
    "configure_and_apply_pipeline",
    "dataset_pipeline_declaration",
    "draft_config_rows",
    "frozen_config",
    "measurement_node",
    "merge_declared_layers",
    "merge_pipeline_params",
    "missing_template_vars",
    "overlay_dataset_pipeline",
    "resolve_campaign_config",
    "resolve_pipeline_at",
    "resolve_pipeline_config_params",
    "resolve_pipeline_for_campaign",
    "resolve_pipeline_for_dataset",
    "resolve_pipeline_for_draft",
    "resolve_pipeline_for_optimizer",
    "resolve_root_config",
    "resolved_dataset_name",
]


def apply_node_overlay(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    schema: PipelineSchema | None,
    *,
    source: ParamSource | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    merged = dict(base)
    for node, cfg in overlay.items():
        existing = merged.get(node)
        if not (isinstance(existing, dict) and isinstance(cfg, dict)):
            merged[node] = cfg
            _stamp(provenance, source, node, cfg)
            continue
        node_obj = schema.get_node(node) if schema else None
        param_types = node_obj.param_types if node_obj else {}
        node_cfg = {**existing, **cfg}
        for param, incoming in cfg.items():
            prior = existing.get(param)
            if (
                param_types.get(param) == "object"
                and isinstance(prior, dict)
                and isinstance(incoming, dict)
            ):
                node_cfg[param] = {**prior, **incoming}
        merged[node] = node_cfg
        _stamp(provenance, source, node, cfg)
    return merged


def merge_pipeline_params(
    base: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    schema: PipelineSchema | None,
) -> dict[str, Any] | None:
    if not overrides:
        return base
    merged = apply_node_overlay(base or {}, overrides, schema)
    if schema:
        # DECLARED, not the running chain: a node reached only by escalating exists.
        _declared = {n.name for n in schema.declared_nodes}
        for k, _cfg in list(node_config_items(merged)):
            if k not in _declared:
                logger.warning("Dropping LLM override for undeclared node %r", k)
                del merged[k]
    return merged


def _stamp(
    provenance: MutableMapping[str, dict[str, ParamSource]] | None,
    source: ParamSource | None,
    node: str,
    cfg: object,
) -> None:
    if provenance is None or source is None or not isinstance(cfg, dict):
        return
    provenance.setdefault(node, {}).update(dict.fromkeys(cfg, source))


def resolve_pipeline_config_params(
    active: list[str],
    campaign_overlay: Mapping[str, Any],
    dataset_dir: Path | None,
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
        dataset_dir,
        schema,
        workspace=workspace,
        base_config=base_config,
        provenance=provenance,
    )
    return apply_identity_layer(
        params,
        active,
        dataset_dir,
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
    dataset_dir: Path | None,
    schema: PipelineSchema,
    *,
    stores: Stores | None,
    judges: Mapping[str, JudgeSpec] | None,
    experiment: Mapping[str, Any] | None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    # LAST and unoverridable: the connector's and the judges' ONE channel into the archive key.
    contributions = _identity_contributions(stores, dataset_dir, experiment, judges, active)
    identity = {node: cfg for node, cfg in contributions.items() if node in active}
    if identity:
        pipeline_params = apply_node_overlay(
            pipeline_params, identity, schema, source="identity", provenance=provenance
        )
    return pipeline_params


def _connector_of(raw: Mapping[str, Any] | None) -> Connector | None:
    return connectors.registered().get(str((raw or {}).get("backend_type") or ""))


def _dataset_connector(dataset_dir: Path) -> Connector | None:
    return _connector_of(read_yaml_optional(dataset_pipeline_path(dataset_dir)))


def overlay_dataset_pipeline(served: dict[str, Any], local: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(served.get("data") or served)
    if "pipelines" in local:
        out["pipelines"] = local["pipelines"]
    # The operator's model MENU bounds the L1 enum; dropped, the service's catalogue is searched.
    if local.get("available_models"):
        out["available_models"] = local["available_models"]
    out["nodes"] = merge_node_blocks(out.get("nodes") or {}, local.get("nodes") or {})
    return out


def dataset_pipeline_declaration(
    stores: Stores, dataset_dir: Path, experiment: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    local = read_yaml_optional(dataset_pipeline_path(dataset_dir))
    connector = _connector_of(local)
    if local is None or connector is None or connector.pipeline_declaration is None:
        return local
    return overlay_dataset_pipeline(connector.pipeline_declaration(stores, experiment), local)


def experiment_outside_run(dataset_dir: Path | None) -> Mapping[str, Any] | None:
    if dataset_dir is None:
        return None
    connector = _dataset_connector(dataset_dir)
    return None if connector is None else dataset_experiment(dataset_dir, connector)


def _identity_contributions(
    stores: Stores | None,
    dataset_dir: Path | None,
    experiment: Mapping[str, Any] | None,
    judges: Mapping[str, JudgeSpec] | None,
    active: list[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if dataset_dir is not None:
        connector = _dataset_connector(dataset_dir)
        if connector is not None and connector.identity_config is not None:
            if stores is None:
                raise ValueError(f"{dataset_dir}: its connector's identity needs the stores")
            out.update(connector.identity_config(stores, dataset_dir, experiment))
    if judges and active:
        # The TERMINAL step: a judge grades the answer that node emits.
        out.setdefault(active[-1], {})[JUDGE_INSTRUMENT_KEY] = judge_instrument(judges)
    return out


def missing_template_vars(rendered: str, declared: list[str]) -> list[str]:
    """``PROMPT_STRING_FIELDS`` are excluded: ``render()`` assembles them, nothing substitutes them."""
    return [
        v for v in declared if v not in PROMPT_STRING_FIELDS and "{{" + v + "}}" not in rendered
    ]


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
    # Declared UNION the running chain: `promptpotter-self` declares `l2_context` / `l3_plan`
    # off-chain on purpose, and a dataset declaring fewer nodes than it runs must lose none.
    declared = dataset_declared_nodes(dataset_dir) if dataset_dir is not None else frozenset()
    if declared:
        keep = declared | set(filtered.active_steps)
        filtered = filtered.filter_to_steps(sorted(keep))
    if narrowing:
        filtered = filtered.narrow(narrowing)
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
    prompt_nodes = [n for n in filtered.prompt_node_names() if n in active]
    prompt_info_by_node = {n.name: n.prompt_info for n in filtered.nodes}
    for pnode in prompt_nodes:
        template = load_node_prompt(dataset_dir, pnode, "default")
        rendered = template.render()
        pinfo = prompt_info_by_node.get(pnode)
        declared = pinfo.template_variables if pinfo else []
        missing = missing_template_vars(rendered, declared)
        if missing:
            raise PayloadInvalidError(
                f"Dataset {dataset_name!r} prompt for node {pnode!r} is missing required "
                f"template variables {missing} — the backend injects these by literal "
                f"{{{{name}}}} substitution, so without them the query / research / output "
                f"schema never reach the model. Add the placeholders to "
                f"datasets/{dataset_name}/prompts/[{pnode}|default].yaml "
                f"(node declares: {declared}).",
                code="pipeline_config_invalid",
            )
        pipeline_params.setdefault(pnode, {})["prompt"] = rendered
        logger.info(
            "Starting prompt: %s/prompts/[%s|default].yaml → %s", dataset_name, pnode, pnode
        )


def _validate_prompt_reach(
    *,
    filtered: PipelineSchema,
    active: list[str],
    has_starting_prompts: bool,
    prompt_fields_as_node_params: bool,
    dataset_name: str,
) -> None:
    if any(n in active for n in filtered.prompt_node_names()):
        return
    names_prompt_fields = [
        name
        for name in active
        if (node := filtered.get_node(name)) is not None
        and node.param_keys & set(PROMPT_STRING_FIELDS)
    ]
    if names_prompt_fields and not prompt_fields_as_node_params:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r}: {names_prompt_fields} name prompt fields in "
            f"`optimizer.param_keys` but declare no `prompt_info`, and this backend sends a node "
            f"only the rendered prompt — the fields would be searched every round and reach no "
            f"model, so every candidate scores as the same no-skill call. Declare `prompt_info` "
            f"on the node the prompt is meant for.",
            code="pipeline_config_invalid",
        )
    if has_starting_prompts:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r} ships starting prompts but no active node in {active} "
            f"declares `prompt_info`, so the rendered prompt is dropped before the wire and "
            f"every cell runs with an empty system prompt. An LLM node must declare it, in "
            f"GET /pipeline or in the dataset's pipeline.yaml.",
            code="pipeline_config_invalid",
        )
    if not names_prompt_fields:
        raise PayloadInvalidError(
            f"dataset {dataset_name!r}: no active node in {active} can receive a prompt — none "
            f"declares `prompt_info` and none names a prompt field in `optimizer.param_keys`, so "
            f"every candidate scores as the same no-skill call and the round reports a tie it "
            f"never measured. Declare one of the two on the node the prompt is meant for.",
            code="pipeline_config_invalid",
        )


def _validate_model_ownership(
    pipeline_params: dict[str, Any],
    *,
    filtered: PipelineSchema,
    active: list[str],
    dataset_name: str,
) -> None:
    if filtered is None:
        return
    for name in active:
        node_obj = filtered.get_node(name)
        if node_obj and node_obj.is_llm and not pipeline_params.get(name, {}).get("model"):
            raise PayloadInvalidError(
                f"dataset {dataset_name!r}: LLM node {name!r} has no owned model. "
                f"Declare it in the dataset's pipeline.yaml::nodes.{name}.config.model "
                f"— the dataset owns its task model, never the backend default.",
                code="pipeline_config_invalid",
            )


def resolved_dataset_name(session: Session, campaign_config: CampaignConfig) -> str:
    """Must equal the mint seam's ``Campaign.dataset_name``, or measurements file under a dead name."""
    return campaign_config.dataset_name or session.dataset_name or ""


class CampaignPipelineResponse(StrictModel):
    """One campaign's pipeline at one searchpoint — the body of ``GET /campaigns/{id}/pipeline``.
    The peer of ``readable_dataset_dir`` one question up: that seam answers which bytes are on disk,
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
    optimizer_knobs: dict[str, dict[str, Any]] = Field(
        description="That optimizer's knob values per node as the addressed course runs them — "
        "the manifest's under the campaign's and the cycle seed's overlays"
    )
    params: dict[str, Any] = Field(
        description="Resolved config as the engine holds it — the bytes a round file carries "
        "as `resolved_pipeline_params`, which makes that field this endpoint's check"
    )
    node_config_schema: dict[str, list[NodeConfigParam]]
    view: PipelineView | None
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Keyed by the provider and model each node's rows carry AT this searchpoint"
    )
    reach: dict[str, NodeReach]
    nests: NestedPipelineRef | None = Field(
        description="The inner pipeline this chain nests, if any — the L4 drill-in, on this read"
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
    for held in reversed(stores.campaigns.standing_rounds(hop).rounds.values()):
        for cand in held.close.candidate_scores:
            if cand.candidate_id == at.candidate_id:
                return dict(cand.pipeline_overlay or {}), dict(cand.resolved_pipeline_params or {})
    return {}, {}


def measurement_node(view: PipelineView | None) -> str | None:
    return next((n.id for n in (view.nodes if view else []) if n.kind == "measurement"), None)


def nested_pipeline_ref(dataset_dir: Path, view: PipelineView | None) -> NestedPipelineRef | None:
    try:
        panel = load_inner_tasks(inner_tasks_path(dataset_dir))
    except CellUnscoreableError:
        # A read-only view must not raise where the runner would.
        return None
    node = measurement_node(view)
    return NestedPipelineRef(node=node, dataset=panel.inner_benchmark) if node else None


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


def _dataset_dir_of(stores: Stores, campaign: Campaign) -> Path | None:
    try:
        return readable_dataset_dir(stores, campaign.dataset_name)
    except DatasetAccessError:
        # A campaign outlives its dataset dir; its frozen config still answers.
        return None


def _criterion_locked[S](stores: Stores, hop: CycleHop, scoring: S) -> S | dict[str, str]:
    lock = scan_ledger_scoring_lock(CycleLayout(stores.campaigns.cycle_dir(hop)).ledger)
    return lock.locked if lock is not None and lock.declared == scoring else scoring


def frozen_config(stores: Stores, campaign: Campaign) -> dict[str, Any]:
    declared = campaign.config.get("scoring")
    locked = _criterion_locked(stores, campaign.root_hop, declared)
    return campaign.config if locked == declared else {**campaign.config, "scoring": locked}


def resolve_campaign_config(
    stores: Stores, campaign: Campaign, hop: CycleHop | None
) -> CampaignConfig:
    try:
        frozen = load_campaign_config(frozen_config(stores, campaign))
    except ValidationError as exc:
        raise StoredConfigInvalidError(
            path=f"campaigns/{campaign.campaign_id}/campaign.json::config",
            reason=f"{exc.error_count()} field(s) invalid — {exc.errors()[0]['msg']}",
        ) from exc
    if campaign.arm is not None:
        record = stores.campaigns.load_head_to_head(campaign.arm.head_to_head_id)
        if record is None:
            raise StoredConfigInvalidError(
                path=f"campaigns/{campaign.campaign_id}/campaign.json::arm",
                reason=f"head-to-head {campaign.arm.head_to_head_id} has no record to run under",
            )
        frozen = under_record(frozen, record)
    if hop is None:
        return frozen
    seeded = apply_cycle_seed(frozen, stores.campaigns.read_cycle_seed(hop))
    locked = _criterion_locked(stores, hop, seeded.scoring)
    return seeded if locked == seeded.scoring else seeded.model_copy(update={"scoring": locked})


def _authoring_draft(stores: Stores, campaign: Campaign) -> DraftCampaign | None:
    """The FLAG decides, never a draft's presence: ``draft.json`` stays in place after Start."""
    if not is_checkin(stores.campaigns.cycle_dir(campaign.root_hop)):
        return None
    return load_checkin_draft(stores, campaign.campaign_id)


def resolve_root_config(stores: Stores, campaign: Campaign) -> CampaignConfig:
    draft = _authoring_draft(stores, campaign)
    if draft is not None:
        return draft_campaign_config(draft)
    return resolve_campaign_config(stores, campaign, campaign.root_hop)


@dataclass(frozen=True)
class _Merge:
    declared: PipelineSchema
    filtered: PipelineSchema
    active: list[str]
    cfg: CampaignConfig
    params: dict[str, Any]
    provenance: dict[str, dict[str, ParamSource]]


@dataclass(frozen=True)
class _CampaignMerge(_Merge):
    dataset_dir: Path | None
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


def _draft_merge(draft: DraftCampaign, *, workspace: Path | None) -> _Merge:
    schema = parse_pipeline_response(rendered_pipeline_json(draft))
    cfg = draft_campaign_config(draft)
    active, filtered = _resolve_active_schema(
        schema,
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        dataset_dir=None,
    )
    provenance: dict[str, dict[str, ParamSource]] = {}
    params = merge_declared_layers(
        active,
        cfg.pipeline_overlay,
        None,
        filtered,
        workspace=workspace,
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
    )


def _campaign_merge(stores: Stores, campaign: Campaign, at: SubjectSpec) -> _CampaignMerge:
    dataset_dir = _dataset_dir_of(stores, campaign)
    hop = CycleHop(campaign_id=campaign.campaign_id, cycle_id=at.cycle_id or campaign.root_cycle_id)
    # The cycle's recorded merge first: the committed dataset file carries no `param_keys` at all.
    raw = stores.campaigns.read_resolved_pipeline(hop)
    if raw is None and dataset_dir:
        raw = dataset_pipeline_declaration(stores, dataset_dir, experiment_outside_run(dataset_dir))
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
        dataset_dir=dataset_dir,
        hop=hop,
        raw=raw,
        seed=seed,
    )


def _served_menu(m: _Merge, params: dict[str, Any], workspace: Path | None) -> CapabilityMenu:
    return resolve_schema_menu(_routed_as_run(m.filtered, params, m.active), workspace=workspace)


def _optimizer_knobs(cfg: CampaignConfig) -> dict[str, dict[str, Any]]:
    selected = select_optimizer(cfg.optimization)
    return {n: selected.knobs(n).model_dump(mode="json") for n in selected.member_nodes}


def resolve_pipeline_for_draft(
    draft: DraftCampaign,
    *,
    campaign_id: str,
    cycle_id: str,
    workspace: Path | None = None,
) -> CampaignPipelineResponse:
    """Never reads the dataset file; a reused origin arrives via ``datasets/ingest.py::draft_from_origin``."""
    m = _draft_merge(draft, workspace=workspace)
    params, rows = _draft_rows(m)
    return CampaignPipelineResponse(
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        dataset_name=draft.slug,
        connector=draft.connector,
        backend_type=draft.connector,
        self_optimization=is_self_optimization(draft.connector),
        optimizer=m.cfg.optimization.optimizer,
        optimizer_knobs=_optimizer_knobs(m.cfg),
        params=params,
        node_config_schema=rows,
        view=m.filtered.view,
        node_output_schema=resolved_output_schemas(m.filtered, params),
        model_capabilities=_served_menu(m, params, workspace),
        reach=reach_map(rows),
        # A dataset dir is what declares an inner panel, and a check-in has none yet.
        nests=None,
        is_single_node=m.filtered.is_single_node,
    )


def draft_config_rows(
    draft: DraftCampaign, *, workspace: Path | None
) -> dict[str, list[NodeConfigParam]]:
    return _draft_rows(_draft_merge(draft, workspace=workspace))[1]


def _draft_rows(m: _Merge) -> tuple[dict[str, Any], dict[str, list[NodeConfigParam]]]:
    params = apply_identity_layer(
        m.params,
        m.active,
        None,
        m.filtered,
        stores=None,
        judges=m.cfg.judges,
        experiment=None,
        provenance=m.provenance,
    )
    rows = m.filtered.node_config_schema(
        values={n: c for n, c in params.items() if isinstance(c, dict)},
        sources=m.provenance,
        model_menu=m.filtered.selectable_models(),
        declared=m.declared,
    )
    return params, rows


def resolve_pipeline_for_campaign(
    stores: Stores,
    campaign: Campaign,
    *,
    at: SubjectSpec,
) -> CampaignPipelineResponse:
    """``stores`` is already the store *campaign* lives in: the caller descends ``at.inside`` first."""
    draft = _authoring_draft(stores, campaign)
    if draft is not None:
        return resolve_pipeline_for_draft(
            draft,
            campaign_id=campaign.campaign_id,
            cycle_id=campaign.root_cycle_id,
            workspace=stores.base_dir,
        )

    m = _campaign_merge(stores, campaign, at)
    params = apply_identity_layer(
        m.params,
        m.active,
        m.dataset_dir,
        m.filtered,
        stores=stores,
        judges=m.cfg.judges,
        # Recorded first: a live resolve recomputes a banked identity off a roster it never ran.
        experiment=stores.campaigns.read_resolved_experiment(m.hop)
        or experiment_outside_run(m.dataset_dir),
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

    rows = m.filtered.node_config_schema(
        values={n: c for n, c in params.items() if isinstance(c, dict)},
        sources=m.provenance,
        model_menu=m.filtered.selectable_models(),
        declared=m.declared,
    )
    return CampaignPipelineResponse(
        campaign_id=campaign.campaign_id,
        cycle_id=m.hop.cycle_id,
        dataset_name=campaign.dataset_name,
        connector=str((m.raw or {}).get("backend_name") or campaign.dataset_name),
        # The campaign's FROZEN kind — one `pipeline.yaml` serves every campaign on the slug.
        backend_type=campaign.backend_type,
        self_optimization=is_self_optimization(campaign.backend_type),
        optimizer=m.cfg.optimization.optimizer,
        optimizer_knobs=_optimizer_knobs(m.cfg),
        params=params,
        node_config_schema=rows,
        view=m.filtered.view,
        node_output_schema=resolved_output_schemas(m.filtered, params),
        model_capabilities=_served_menu(m, params, stores.base_dir),
        reach=reach_map(rows),
        nests=nested_pipeline_ref(m.dataset_dir, m.filtered.view) if m.dataset_dir else None,
        is_single_node=m.filtered.is_single_node,
    )


def _pipeline_subject(at: str, campaign_id: str) -> SubjectSpec:
    if not at:
        return SubjectSpec("campaign", campaign_id)
    try:
        spec = parse_subject(at)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
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


class DatasetPipelineResponse(StrictModel):
    """One dataset's pipeline as DECLARED — the body of ``GET /datasets/{name}/pipeline``. Topology
    only: the level a ``nests`` pointer names, which no campaign has to have run."""

    connector: str
    view: PipelineView | None
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Every param each node carries, valueless — what `reach` is summed over"
    )
    reach: dict[str, NodeReach]
    nests: NestedPipelineRef | None = Field(
        description="The pipeline this one nests in turn; the only wire naming it before a cell "
        "has spawned"
    )


def resolve_pipeline_for_dataset(stores: Stores, dataset_name: str) -> DatasetPipelineResponse:
    dataset_dir = readable_dataset_dir(stores, dataset_name)
    raw = dataset_pipeline_declaration(stores, dataset_dir, experiment_outside_run(dataset_dir))
    if raw is None:
        raise NotFoundError(f"Dataset '{dataset_name}' has no pipeline.yaml")
    cfg = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))
    _, filtered = _resolve_active_schema(
        parse_pipeline_response(raw),
        exclude=list(cfg.exclude_nodes),
        narrowing=cfg.optimizer_narrowing,
        dataset_dir=dataset_dir,
    )
    rows = filtered.node_config_schema()
    return DatasetPipelineResponse(
        connector=str(raw.get("backend_name") or raw.get("name") or dataset_name).strip(),
        view=filtered.view,
        node_config_schema=rows,
        reach=reach_map(rows),
        nests=nested_pipeline_ref(dataset_dir, filtered.view),
    )


class OptimizerPipelineResponse(StrictModel):
    """What the OPTIMIZER runs — the manifest's peer of ``GET /campaigns/{id}/pipeline``. The raw
    manifest keys are not served: ``nodes`` was a second, untyped spelling of ``node_config_schema``."""

    view: PipelineView | None = Field(
        description="The graph topology — the same shape a campaign pipeline serves"
    )
    measurement_node: str | None = Field(
        description="The node of `view` that runs the measurement — where a campaign's pipeline "
        "nests under this graph, as `nests.node` names it per campaign. Null where the manifest "
        "declares no measurement node."
    )
    node_config_schema: dict[str, list[NodeConfigParam]] = Field(
        description="Per-node typed config rows, so the node detail renders the optimizer's own "
        "knobs through the canonical config element rather than a chip and a JSON dump"
    )
    node_output_schema: dict[str, NodeOutputSchema | None]
    model_capabilities: CapabilityMenu = Field(
        description="Optimizer-LOCKED is not unpriced: the model is fixed, but which effort rungs "
        "it accepts and what a round costs are the facts every other node's rows need too"
    )
    reach: dict[str, NodeReach] = Field(
        description="Where the search reaches per node, summed off the rows above rather than in "
        "the browser — the same reading a campaign pipeline serves"
    )
    start_prompts: dict[str, StartPrompt] = Field(
        description="The prompt each node STARTS from, by node, as the run resolves it off the "
        "node's `prompt_family`/`prompt_version` — the floor under a searchpoint carrying no "
        "evolved delta for that node. A node declaring no prompt has no entry."
    )


def resolve_pipeline_for_optimizer(stores: Stores, optimizer: str) -> OptimizerPipelineResponse:
    selected = resolve_optimizer(optimizer, {})
    schema = selected.schema
    # The reach must sum the SAME rows it serves, or the glyph and the padlock disagree.
    rows = schema.node_config_schema(selected.steered_axes)
    return OptimizerPipelineResponse(
        view=schema.view,
        measurement_node=measurement_node(schema.view),
        node_config_schema=rows,
        node_output_schema=schema.node_output_schemas(),
        reach=reach_map(rows),
        # Per tenant: a hand-authored catalogue override lives in their workspace.
        model_capabilities=resolve_schema_menu(schema, workspace=stores.base_dir),
        start_prompts=selected.start_prompts(),
    )


class RunsWithParam(StrictModel):
    """One setting a campaign's root course runs with, and the layer that chose it."""

    node: str
    key: str = Field(description="The config grid's own param key (`NodeConfigParam.key`)")
    value: Any
    source: ParamSource


class VendorModels(StrictModel):
    """The models of one vendor that a campaign's root course runs."""

    vendor: str = Field(
        description="Who TRAINED them: the namespace of the model id, or the bare id where it "
        "names none. Lowercased, routing suffix dropped."
    )
    models: list[str] = Field(
        description="Full ids, de-duplicated, routing suffix kept: it routes and bills, so it "
        "names a different run."
    )


class CampaignRunsWith(StrictModel):
    """What a campaign's root course runs with — the root's pipeline resolution, cut to settings."""

    params: list[RunsWithParam] = Field(
        description="Scalar settings in active-step order, `model` included; no prompt text"
    )
    vendors: list[VendorModels] = Field(
        description="Every model in `params`, grouped under its vendor in first-seen order"
    )
    optimizer: str = Field(description="The optimizer manifest the root course runs")
    max_rounds: int | None = Field(
        description="The DECLARED rounds cap, not the armed one: 0 = origin only, null = unlimited"
    )


# A prompt, a description and a nested value are no setting one line can print.
_RUNS_WITH_KINDS = frozenset({"model", "enum", "number", "bool", "string"})
_RUNS_WITH_UNRESOLVED = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    StoredConfigInvalidError,
    PayloadInvalidError,
)


def campaign_runs_with(stores: Stores, campaign: Campaign) -> CampaignRunsWith | None:
    """``None`` is a root whose pipeline did not resolve: a broken dataset drops its own row only."""
    root = stores.campaigns.cycle_dir(campaign.root_hop)

    def resolve() -> CampaignRunsWith | None:
        try:
            return _runs_with(stores, campaign, draft)
        except _RUNS_WITH_UNRESOLVED:
            logger.exception("runs_with: root pipeline of %s did not resolve", campaign.campaign_id)
            return None

    try:
        draft = _authoring_draft(stores, campaign)
        key = _runs_with_key(stores, campaign, root, draft)
    except _RUNS_WITH_UNRESOLVED:
        logger.exception("runs_with: root pipeline of %s did not resolve", campaign.campaign_id)
        return None
    return derived(("runs_with", root), sig=key, compute=resolve)


def _runs_with_key(
    stores: Stores, campaign: Campaign, root: Path, draft: DraftCampaign | None
) -> tuple[Any, ...]:
    dataset_dir = _dataset_dir_of(stores, campaign)
    # The seed's VALUE, never its ledger's stat: a running root appends there on every sample.
    seed = stores.campaigns.read_cycle_seed(campaign.root_hop)
    return (
        stable_hash(frozen_config(stores, campaign)),
        campaign.backend_type,
        campaign.dataset_name,
        campaign.root_cycle_id,
        dataset_dir,
        file_sig(dataset_pipeline_path(dataset_dir)) if dataset_dir is not None else None,
        file_sig(CycleLayout(root).resolved_pipeline),
        None if seed is None else stable_hash(seed.model_dump(mode="json")),
        None if draft is None else stable_hash(draft.to_disk()),
    )


def _runs_with(stores: Stores, campaign: Campaign, draft: DraftCampaign | None) -> CampaignRunsWith:
    m: _Merge
    if draft is not None:
        m = _draft_merge(draft, workspace=stores.base_dir)
        params = m.params
    else:
        read = _campaign_merge(stores, campaign, SubjectSpec("campaign", campaign.campaign_id))
        m, params = read, read.with_seed(read.params)
    stripped = strip_rendered_prompt(params)
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
        max_rounds=m.cfg.optimization.max_rounds,
    )


def _vendor_of(model: str) -> str:
    return normalize_model_id(model).partition("/")[0]


def configure_and_apply_pipeline(
    session: Session, campaign_config: CampaignConfig
) -> dict[str, Any]:

    exclude = list(campaign_config.exclude_nodes)
    dataset_name = resolved_dataset_name(session, campaign_config)
    dataset_dir = session.dataset_config_dir

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
        dataset_dir,
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

    session.pipeline_schema = schema_as_run(
        filtered, pipeline_params, active, session.store.base_dir
    )
    session.pipeline_params = pipeline_params
    session.identity_keys = _identity_keys(provenance)

    excl_str = f"  Excluded: {', '.join(exclude)}" if exclude else ""
    logger.info("Active nodes: %s%s", ", ".join(active), excl_str)

    return pipeline_params
