from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import NodePrompt
from promptpotter.domain.pipeline_schema import PipelineNode
from promptpotter.infrastructure.store.dataset_access import (
    read_dataset_origin,
    read_dataset_pipeline,
)
from promptpotter.shared.errors import PayloadInvalidError


def load_dataset_node_overlay(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    raw = read_dataset_pipeline(dataset_dir)
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node_name, node_def in (raw.get("nodes") or {}).items():
        cfg = node_def.get("config") if isinstance(node_def, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[node_name] = dict(cfg)
    return out


def dataset_declared_nodes(dataset_dir: Path) -> frozenset[str]:
    """Empty where the dataset declares no nodes: "no opinion", never "no nodes"."""
    raw = read_dataset_pipeline(dataset_dir)
    if not raw:
        return frozenset()
    return frozenset(raw.get("nodes") or {})


def has_dataset_prompts(dataset_dir: Path) -> bool:
    return bool(read_dataset_origin(dataset_dir))


def _authored_fields(dataset_dir: Path, node: str, authored: object) -> dict[str, str]:
    if not isinstance(authored, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in authored.items()
    ):
        raise PayloadInvalidError(
            f"{dataset_dir}/origin.yaml: node {node!r} holds {authored!r}; a starting prompt is "
            "`{field: text}` under its node.",
            code="pipeline_config_invalid",
        )
    return dict(authored)


def origin_prompts(dataset_dir: Path, steps: Collection[str]) -> dict[str, NodePrompt]:
    """Every starting prompt ``origin.yaml`` authors on a node of *steps*, as authored."""
    origin = read_dataset_origin(dataset_dir)
    return {
        node: NodePrompt(fields=_authored_fields(dataset_dir, node, origin[node]))
        for node in steps
        if node in origin
    }


def declared_origin_prompts(
    dataset_dir: Path, nodes: Sequence[PipelineNode]
) -> dict[str, NodePrompt]:
    """Refuses a field its node does not declare: it would render nowhere."""
    authored = origin_prompts(dataset_dir, [node.name for node in nodes])
    for node in nodes:
        fields = authored.get(node.name, NodePrompt()).fields
        if undeclared := sorted(set(fields) - set(node.prompt_fields)):
            raise PayloadInvalidError(
                f"{dataset_dir}/origin.yaml: node {node.name!r} declares the prompt fields "
                f"{list(node.prompt_fields)}, and its starting prompt sets {undeclared}.",
                code="pipeline_config_invalid",
            )
    return authored
