"""Per-dataset starting-point prompts. ``dataset_dir`` is the RESOLVED dir off the session (tenant-first), so these loaders never
recompute a repo-relative path and a tenant upload loads through the same code as a repo benchmark."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
from promptpotter.infrastructure.store.io import read_yaml, read_yaml_optional


def load_dataset_node_overlay(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """Sparse per-node overlay from the dataset's ``pipeline.yaml``, layered onto the wire payload at init. The backend's
    ``GET /pipeline`` stays SoT for runtime defaults; this encodes per-dataset operator preferences."""
    raw = read_yaml_optional(dataset_pipeline_path(dataset_dir))
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node_name, node_def in (raw.get("nodes") or {}).items():
        cfg = node_def.get("config") if isinstance(node_def, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[node_name] = dict(cfg)
    return out


def dataset_prompt_dir(dataset_dir: Path) -> Path:
    return dataset_dir / "prompts"


def has_dataset_prompts(dataset_dir: Path) -> bool:
    return dataset_prompt_dir(dataset_dir).is_dir()


@functools.lru_cache(maxsize=128)
def load_node_prompt(
    dataset_dir: Path,
    node_name: str,
    variant: str = "default",
) -> PromptTemplate:
    d = dataset_prompt_dir(dataset_dir)
    node_path = d / f"{node_name}.yaml"
    data = read_yaml_optional(node_path)
    if data is not None:
        return PromptTemplate(**data)

    variant_path = d / f"{variant}.yaml"
    data = read_yaml_optional(variant_path)
    if data is not None:
        return PromptTemplate(**data)

    raise FileNotFoundError(
        f"Canonical prompt template not found in {d} for node={node_name!r}. "
        f"Expected either {node_path} (per-node) or {variant_path} (dataset default). "
        f"Author one as a PromptTemplate YAML; see docs/concepts/the-loop.md."
    )


@functools.lru_cache(maxsize=64)
def load_dataset_prompt(dataset_dir: Path, name: str = "default") -> PromptTemplate:
    path = dataset_prompt_dir(dataset_dir) / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset prompt not found: {path}. Create it under the dataset's prompts/ dir."
        )
    data = read_yaml(path)
    return PromptTemplate(**data)


def list_dataset_prompts(dataset_dir: Path) -> list[str]:
    d = dataset_prompt_dir(dataset_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))
