"""The free tier: what a free account may run, read off ``config/paths.py::free_tier_path``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from promptpotter import connectors
from promptpotter.application.jobs.coupon import paying_key
from promptpotter.application.jobs.quota import on_free_tier
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.config.paths import free_tier_path
from promptpotter.config.settings import settings
from promptpotter.domain.campaign_config import CampaignConfig, merge_config_layers
from promptpotter.domain.free_tier import FREE_TIER_ADAPTER, FreeTier
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.spend import draws_coupon
from promptpotter.infrastructure.llm.registry import providers
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import PayloadInvalidError


def free_tier() -> FreeTier:
    """Read each time, so an operator's edit holds from the next check."""
    return FREE_TIER_ADAPTER.validate_python(read_yaml(free_tier_path()))


class FreeFence(NamedTuple):
    """What the tier binds of a run: only what draws the host's coupon."""

    # The target's nodes, where its connector's cells draw the coupon.
    task: bool
    # The providers whose sends draw it: an optimizer node on any other is unfenced.
    providers: frozenset[str]


def free_fence(stores: Stores, connector: str) -> FreeFence | None:
    """``None`` off the free tier (`quota.py::on_free_tier`)."""
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


def with_free_tier_layer(
    config: CampaignConfig, schema: PipelineSchema | None, fence: FreeFence
) -> CampaignConfig:
    """The tier's layer where anything the run sends draws the coupon, plus per fenced task node
    of *schema* its model's bounds: each searched axis narrowed to them within what the node
    permits, and an unset one launched at the lowest. ``None`` reads no node."""
    if not fence.task and not fence.providers:
        return config
    tier = free_tier()
    merged = merge_config_layers(config.model_dump(mode="json", exclude_unset=True), tier.campaign)
    space = schema.narrow(config.optimizer_narrowing) if schema is not None and fence.task else None
    for node in space.declared_nodes if space is not None else ():
        cfg = {**node.current_config, **config.pipeline_overlay.get(node.name, {})}
        if not node.is_llm or not (bounds := tier.task_models.get(str(cfg.get("model")))):
            continue
        kept = node.param_allowed_values
        allowed = {p: [v for v in vs if p not in kept or v in kept[p]] for p, vs in bounds.items()}
        unset = {p: vs[0] for p, vs in bounds.items() if p not in cfg and vs}
        merged = merge_config_layers(
            merged,
            {
                "pipeline_overlay": {node.name: unset},
                "optimizer_narrowing": {node.name: {"param_allowed_values": allowed}},
            },
        )
    return CampaignConfig.model_validate(merged)


def free_task_models() -> frozenset[str]:
    return frozenset(free_tier().task_models)


def outside_free_tier(
    config: CampaignConfig,
    selected: SelectedOptimizer,
    pipeline_params: Mapping[str, Any],
    schema: PipelineSchema,
    fence: FreeFence,
) -> list[str]:
    """What the run could send on the coupon, launched or searched, that the tier does not list."""
    tier = free_tier()
    space = schema.narrow(config.optimizer_narrowing)
    task = fence.task
    out = [
        f"the node {s}"
        for s in (pipeline_params.get("steps", ()) if task else ())
        if s not in tier.task_nodes
    ]
    for node, cfg in node_config_items(dict(pipeline_params)) if task else ():
        if not (model := cfg.get("model")):
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
                values = [cfg.get(param, "default")] if m == model else []
                if declared is not None and param in searched:
                    values += space.param_options(declared, param, model=m) or ["any"]
                out += [f"{param} {v} on {m}" for v in values if v not in admitted]
    for name in selected.llm_nodes:
        called = selected.node_config(name)
        if called.provider not in fence.providers:
            continue
        if (bounds := tier.optimizer_models.get(model := called.model)) is None:
            out.append(f"the optimizer model {model}")
            continue
        laid = called.model_dump(include=set(bounds), exclude_unset=True)
        out += [
            f"{param} {value} on the optimizer model {model}"
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
