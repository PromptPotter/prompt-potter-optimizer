"""Per-dataset starting-point prompt store + backend node overlay reader.

Each dataset ships ``{dataset_dir}/prompts/`` with PromptTemplate JSON.
Resolution: ``{node_name}.json`` → ``{variant}.json`` → ``FileNotFoundError``.

``dataset_dir`` is the resolved config dir carried on ``Session.dataset_config_dir``
(tenant-first via ``resolve_dataset_config_dir``) — these loaders never recompute a
repo-relative path from the bare dataset name, so tenant uploads and repo benchmarks
load through the same code."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.infrastructure.store.base import read_json_optional


def load_dataset_node_overlay(dataset_dir: Path) -> dict[str, dict[str, Any]]:
    """Sparse ``{node_name: {key: value}}`` overlay from ``{dataset_dir}/pipeline.json``,
    layered onto the wire payload at init. Backend ``GET /pipeline`` is SoT for runtime
    defaults; this overlay encodes per-dataset operator preferences (e.g. AIME via OpenRouter/Mistral)."""
    raw = read_json_optional(dataset_dir / "pipeline.json")
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node_name, node_def in (raw.get("nodes") or {}).items():
        cfg = node_def.get("config") if isinstance(node_def, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[node_name] = dict(cfg)
    return out


def dataset_prompt_dir(dataset_dir: Path) -> Path:
    """Return the expected ``prompts/`` directory under *dataset_dir*."""
    return dataset_dir / "prompts"


def has_dataset_prompts(dataset_dir: Path) -> bool:
    """Whether *dataset_dir* ships a ``prompts/`` directory."""
    return dataset_prompt_dir(dataset_dir).is_dir()


@functools.lru_cache(maxsize=128)
def load_node_prompt(
    dataset_dir: Path,
    node_name: str,
    variant: str = "default",
) -> PromptTemplate:
    """Load canonical ``PromptTemplate``: ``{node_name}.json`` → ``{variant}.json`` → ``FileNotFoundError``."""
    d = dataset_prompt_dir(dataset_dir)
    node_path = d / f"{node_name}.json"
    data = read_json_optional(node_path)
    if data is not None:
        return PromptTemplate(**data)

    variant_path = d / f"{variant}.json"
    data = read_json_optional(variant_path)
    if data is not None:
        return PromptTemplate(**data)

    raise FileNotFoundError(
        f"Canonical prompt template not found in {d} for node={node_name!r}. "
        f"Expected either {node_path} (per-node) or {variant_path} (dataset default). "
        f"Author one as a PromptTemplate JSON; see docs/concepts/state-record.md."
    )


@functools.lru_cache(maxsize=64)
def load_dataset_prompt(dataset_dir: Path, name: str = "default") -> PromptTemplate:
    """Load dataset-wide ``{name}.json`` (thin wrapper over ``load_node_prompt`` for single-node + display)."""
    path = dataset_prompt_dir(dataset_dir) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset prompt not found: {path}. "
            f"Create it, or change 'starting_prompt' in the campaign config."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return PromptTemplate(**data)


def list_dataset_prompts(dataset_dir: Path) -> list[str]:
    """Return sorted template names available under *dataset_dir* (empty if none)."""
    d = dataset_prompt_dir(dataset_dir)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))
