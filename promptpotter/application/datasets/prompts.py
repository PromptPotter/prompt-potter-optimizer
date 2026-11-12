from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset, read_dataset_origin
from promptpotter.shared.errors import PayloadInvalidError


def pipeline_node_overlay(pipeline: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for node_name, node_def in (pipeline.get("nodes") or {}).items():
        cfg = node_def.get("config") if isinstance(node_def, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[node_name] = dict(cfg)
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
