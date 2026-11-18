from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal, NamedTuple

from promptpotter.domain.layer_fold import Layer, fold_layers, untyped_rule
from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.pipeline_params import NodeCall
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset, read_dataset_origin
from promptpotter.shared.errors import PayloadInvalidError


def pipeline_node_overlay(pipeline: Mapping[str, Any]) -> dict[str, NodeCall]:
    """Each node's ``config`` the definition lays, typed: a mistyped key refuses, by its node."""
    out: dict[str, NodeCall] = {}
    for node_name, node_def in (pipeline.get("nodes") or {}).items():
        cfg = node_def.get("config") if isinstance(node_def, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[node_name] = PayloadInvalidError.validating(
                NodeCall, cfg, under=("pipeline", "nodes", node_name, "config")
            )
    return out


def has_dataset_prompts(dataset: ResolvedDataset) -> bool:
    return bool(read_dataset_origin(dataset))


def _authored_fields(dataset: ResolvedDataset, node: str, authored: object) -> dict[str, str]:
    if not isinstance(authored, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in authored.items()
    ):
        raise PayloadInvalidError(
            f"{dataset.directory}/origin.yaml: node {node!r} holds {authored!r}; a starting prompt is "
            "`{field: text}` under its node.",
            code="pipeline_config_invalid",
        )
    return dict(authored)


def origin_prompts(dataset: ResolvedDataset, steps: Collection[str]) -> dict[str, NodePrompt]:
    """Every starting prompt ``origin.yaml`` authors on a node of *steps*, as authored."""
    origin = read_dataset_origin(dataset)
    return {
        node: NodePrompt(fields=_authored_fields(dataset, node, origin[node]))
        for node in steps
        if node in origin
    }


def declared_origin_prompts(
    dataset: ResolvedDataset, nodes: Sequence[PipelineNode]
) -> dict[str, NodePrompt]:
    """Refuses a field its node does not declare: it would render nowhere."""
    authored = origin_prompts(dataset, [node.name for node in nodes])
    for node in nodes:
        fields = authored.get(node.name, NodePrompt()).fields
        if undeclared := sorted(set(fields) - set(node.prompt_fields)):
            raise PayloadInvalidError(
                f"{dataset.directory}/origin.yaml: node {node.name!r} declares the prompt fields "
                f"{list(node.prompt_fields)}, and its starting prompt sets {undeclared}.",
                code="pipeline_config_invalid",
            )
    return authored


StartLayer = Literal["served", "dataset"]


class StartingPrompts(NamedTuple):
    prompts: dict[str, NodePrompt]
    # The layers that laid any field, in fold order.
    laid_by: tuple[StartLayer, ...]


def starting_prompts(
    dataset: ResolvedDataset | None, nodes: Sequence[PipelineNode]
) -> StartingPrompts:
    """Each node's served start (`NodePromptInfo.start`) with the dataset's ``origin.yaml`` laid
    over it, field by field."""
    served = {n.name: n.prompt_info.start for n in nodes if n.prompt_info and n.prompt_info.start}
    authored = {} if dataset is None else declared_origin_prompts(dataset, nodes)
    layers: tuple[Layer[StartLayer], ...] = (
        Layer("served", served),
        Layer("dataset", {node: dict(p.fields) for node, p in authored.items()}),
    )
    folded = fold_layers(untyped_rule, layers)
    stamped = set(folded.stamps.values())
    return StartingPrompts(
        {node: NodePrompt(fields=fields) for node, fields in folded.document.items()},
        tuple(layer.name for layer in layers if layer.name in stamped),
    )
