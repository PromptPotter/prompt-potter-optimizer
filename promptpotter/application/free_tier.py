"""The free tier: what a free account may run, read off ``config/paths.py::free_tier_path``."""

from __future__ import annotations

import functools
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, NamedTuple

from promptpotter import connectors
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.config.paths import free_tier_path
from promptpotter.config.settings import settings
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.free_tier import FREE_TIER_ADAPTER, Bounds, FreeTier
from promptpotter.domain.judges import JudgeStage
from promptpotter.domain.layer_fold import Layer
from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
from promptpotter.domain.pipeline_schema import KnobLayer, PipelineNode, PipelineSchema
from promptpotter.domain.search_point import NodeConfig
from promptpotter.domain.spend import draws_coupon
from promptpotter.infrastructure.identity.accounts import is_host
from promptpotter.infrastructure.llm.registry import providers
from promptpotter.infrastructure.llm.wallet import paying_key
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError


def free_tier() -> FreeTier:
    """Read each time, so an operator's edit holds from the next check."""
    return FREE_TIER_ADAPTER.validate_python(read_yaml(free_tier_path()))


def on_free_tier(stores: Stores) -> bool:
    """The box's DEFAULT coupon: the host and a hand-granted account are outside it, and a
    delegate stands where its delegator's account does. What the tier binds of a run is
    `free_fence`'s."""
    return not is_host(stores.identity) and stores.grant.load() is None


def tier_ceiling(stores: Stores) -> float | None:
    """The USD one launch may hold on the free tier: an admission bound (`quota.py::_admitted`),
    so no operator axis lifts it. ``None`` off it."""
    return free_tier().ceiling_usd if on_free_tier(stores) else None


def model_calls(
    selected: SelectedOptimizer, config: CampaignConfig
) -> list[NodeConfig | JudgeStage]:
    """THE roster of what a run declaring *config* calls beside its connector's cells: its
    optimizer's nodes, then its judges' stages. Admission prices it and the free tier fences it,
    so a new kind of call is both by construction."""
    return [
        *(selected.node_config(node) for node in selected.llm_nodes),
        *(stage for spec in config.judges.values() for stage in spec.stages),
    ]


class FreeFence(NamedTuple):
    """What the tier binds of a run: only what draws the host's coupon."""

    # The target's nodes, where its connector's cells draw the coupon.
    task: bool
    # The providers whose sends draw it: an optimizer node on any other is unfenced.
    providers: frozenset[str]


def free_fence(stores: Stores, connector: str) -> FreeFence | None:
    """``None`` off the free tier (`on_free_tier`)."""
    if not on_free_tier(stores):
        return None
    keys = stores.api_keys.load()
    return FreeFence(
        task=draws_coupon(connectors.CONNECTORS.get(connector).cell_payer),
        providers=frozenset(
            provider
            for provider in providers()
            if (key := paying_key(keys, provider)) is not None and draws_coupon(key.paid_by)
        ),
    )


def free_tier_layers(schema: PipelineSchema | None, fence: FreeFence) -> list[Layer[KnobLayer]]:
    """The tier's layer where anything the run sends draws the coupon, plus per fenced task node
    of *schema* each searched axis narrowed to its model's bounds within what the node permits
    (the model axis to the tier's models). ``None`` reads no node; what an unset param launches
    at is `free_tier_overlay`'s."""
    if not fence.task and not fence.providers:
        return []
    tier = free_tier()
    layers: list[Layer[KnobLayer]] = [Layer("free_tier", tier.campaign)]
    if schema is not None and fence.task:
        layers.append(Layer("free_tier", functools.partial(_bounded_narrowing, schema, tier)))
    return layers


def _bounded_narrowing(
    schema: PipelineSchema, tier: FreeTier, laid: Mapping[str, Any]
) -> dict[str, Any]:
    config = CampaignConfig.model_validate(laid)
    space = schema.narrow(config.optimizer_narrowing)
    narrowing: dict[str, Any] = {}
    for node, _, bounds in _bounded_nodes(config, space, tier):
        kept = node.param_allowed_values
        allowed = {p: [v for v in vs if p not in kept or v in kept[p]] for p, vs in bounds.items()}
        # A searched model axis offers the tier's models alone: Start refuses none it offered.
        if "model" in node.param_keys:
            options = space.model_options(node)
            allowed["model"] = [m for m in tier.task_models if m in options]
        narrowing[node.name] = {"param_allowed_values": allowed}
    return {"optimizer_narrowing": narrowing}


def free_tier_overlay(
    config: CampaignConfig, schema: PipelineSchema, fence: FreeFence | None
) -> dict[str, dict[str, Any]]:
    """The `free_tier` param layer (`pipeline_schema.py::ParamLayer`): per fenced task node of
    *schema*, each param its model's bounds name and neither the node nor *config* sets, launched
    at the lowest."""
    if fence is None or not fence.task:
        return {}
    tier = free_tier()
    return {
        node.name: {p: vs[0] for p, vs in bounds.items() if p not in cfg and vs}
        for node, cfg, bounds in _bounded_nodes(config, schema, tier)
    }


def _bounded_nodes(
    config: CampaignConfig, schema: PipelineSchema, tier: FreeTier
) -> Iterator[tuple[PipelineNode, dict[str, Any], Bounds]]:
    """Each llm node of *schema* on a task model the tier bounds: its config as *config* lays it,
    and its model's bounds."""
    for node in schema.declared_nodes:
        laid = config.pipeline_overlay.get(node.name, NodeCall()).laid_keys()
        cfg = {**node.current_config, **laid}
        if node.is_llm and (bounds := tier.task_models.get(str(cfg.get("model")))):
            yield node, cfg, bounds


def free_task_models() -> frozenset[str]:
    return frozenset(free_tier().task_models)


def outside_free_tier(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    pipeline_params: PipelineParams,
    schema: PipelineSchema,
    fence: FreeFence,
) -> list[str]:
    """What the run could send on the coupon, launched or searched, that the tier does not list."""
    tier = free_tier()
    space = schema.narrow(config.optimizer_narrowing)
    task = fence.task
    out = [
        f"the node {s}"
        for s in (pipeline_params.steps or () if task else ())
        if s not in tier.task_nodes
    ]
    for node, cfg in pipeline_params.nodes.items() if task else ():
        if not (model := cfg.model):
            continue
        declared = space.get_node(node)
        searched = set() if declared is None else declared.param_keys
        models = [model]
        if declared is not None and "model" in searched:
            models += space.model_options(declared)
        for m in dict.fromkeys(models):
            if (bounds := tier.task_models.get(m)) is None:
                out.append(f"the task model {m}")
                continue
            for param, admitted in bounds.items():
                values = [cfg.laid_keys().get(param, "default")] if m == model else []
                if declared is not None and param in searched:
                    values += space.param_options(declared, param, model=m) or ["any"]
                out += [f"{param} {v} on {m}" for v in values if v not in admitted]
    # A judge runs on the box's models under the optimizer's bar: one table, one rule.
    for called in model_calls(selected, config):
        if called.provider not in fence.providers:
            continue
        role = "judge" if isinstance(called, JudgeStage) else "optimizer"
        if (bounds := tier.optimizer_models.get(model := called.model)) is None:
            out.append(f"the {role} model {model}")
            continue
        laid = called.model_dump(include=set(bounds), exclude_unset=True)
        out += [
            f"{param} {value} on the {role} model {model}"
            for param, admitted in bounds.items()
            if (value := laid.get(param, "default")) not in admitted
        ]
    return list(dict.fromkeys(out))


def refuse_outside_free_tier(outside: Sequence[str]) -> None:
    if outside:
        raise PayloadInvalidError(
            "\n".join(
                f"{what} is not part of the free tier yet. To ask for it, open an issue: "
                f"{settings.BRAND_DOCS_URL}/issues or message us on LinkedIn: "
                f"{free_tier().ask.linkedin}"
                for what in outside
            ),
            code="not_on_free_tier",
        )
