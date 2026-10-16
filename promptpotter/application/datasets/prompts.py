from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.infrastructure.store.dataset_access import (
    read_dataset_origin,
    read_dataset_pipeline,
)


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


def load_node_prompt(dataset_dir: Path, node_name: str) -> PromptTemplate:
    origin = read_dataset_origin(dataset_dir)
    if node_name not in origin:
        raise FileNotFoundError(
            f"The definition at {dataset_dir} names no starting prompt for node {node_name!r}: "
            f"`origin.yaml` keys {sorted(origin)}. Author one as a PromptTemplate under its node."
        )
    return PromptTemplate(**origin[node_name])


def first_origin_prompt(dataset_dir: Path, steps: Sequence[str]) -> PromptTemplate | None:
    """The one prompt a check-in edits: the first step's that holds one."""
    origin = read_dataset_origin(dataset_dir)
    node = next((n for n in steps if n in origin), next(iter(origin), None))
    return None if node is None else PromptTemplate(**origin[node])
