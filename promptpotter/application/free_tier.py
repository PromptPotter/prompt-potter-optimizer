"""The free tier: what a free account may run, read off ``config/paths.py::free_tier_path``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from promptpotter.application.campaign_config import CampaignConfig, merge_config_layers
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.config.paths import free_tier_path
from promptpotter.config.settings import settings
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.infrastructure.store.io import read_yaml
from promptpotter.shared.errors import PayloadInvalidError

# Per model, each gated parameter's admitted values, lowest first.
type Bounds = dict[str, tuple[str, ...]]


class Ask(FrozenModel):
    linkedin: str


class FreeTier(FrozenModel):
    task_nodes: tuple[str, ...]
    task_models: dict[str, Bounds]
    optimizer_models: dict[str, Bounds]
    # The check-in's last config layer, in a dataset file's `campaign:` shape.
    campaign: dict[str, Any]
    ask: Ask


def free_tier() -> FreeTier:
    """Read each time, so an operator's edit holds from the next check."""
    return FreeTier.model_validate(read_yaml(free_tier_path()))


def with_free_tier_layer(
    config: CampaignConfig, task_nodes: Mapping[str, Mapping[str, Any]]
) -> CampaignConfig:
    """The tier's layer, plus per task node its model's bounds: each searched axis narrowed to
    them, and an unset one launched at the lowest."""
    tier = free_tier()
    merged = merge_config_layers(config.model_dump(mode="json", exclude_unset=True), tier.campaign)
    for node, cfg in task_nodes.items():
        if not (bounds := tier.task_models.get(str(cfg.get("model")))):
            continue
        narrowed = config.optimizer_narrowing.get(node)
        kept = {} if narrowed is None else narrowed.param_allowed_values
        allowed = {p: [v for v in vs if p not in kept or v in kept[p]] for p, vs in bounds.items()}
        unset = {p: vs[0] for p, vs in bounds.items() if p not in cfg and vs}
        merged = merge_config_layers(
            merged,
            {
                "pipeline_overlay": {node: unset},
                "optimizer_narrowing": {node: {"param_allowed_values": allowed}},
            },
        )
    return CampaignConfig.model_validate(merged)


def free_task_models() -> frozenset[str]:
    return frozenset(free_tier().task_models)


def outside_free_tier(
    config: CampaignConfig, pipeline_params: Mapping[str, Any], schema: PipelineSchema
) -> list[str]:
    """What the run could send, at launch or by searching, that the tier does not list."""
    tier = free_tier()
    space = schema.narrow(config.optimizer_narrowing)
    out = [f"the node {s}" for s in pipeline_params.get("steps", ()) if s not in tier.task_nodes]
    for node, cfg in node_config_items(dict(pipeline_params)):
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
    selected = select_optimizer(config.optimization)
    for name in selected.llm_nodes:
        cfg = selected.node_config(name)
        if (bounds := tier.optimizer_models.get(model := str(cfg.get("model")))) is None:
            out.append(f"the optimizer model {model}")
            continue
        out += [
            f"{param} {value} on the optimizer model {model}"
            for param, admitted in bounds.items()
            if (value := cfg.get(param, "default")) not in admitted
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
