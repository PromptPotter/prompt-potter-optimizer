"""Per-dataset starting-point prompt store.

Each dataset ships ``datasets/{name}/prompts/`` with PromptTemplate JSON
files (6-field canonical decomposition). Resolution: per-node
``{node_name}.json`` first, then dataset-wide ``{variant}.json``, then
``FileNotFoundError`` citing both expected paths. This is the one true
source for origin prompts. Also hosts the backend node overlay reader.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from promptpotter.domain.opt_search_point import PromptTemplate
from promptpotter.infrastructure.store.base import read_json_optional

# promptpotter/application/datasets/prompts.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]


def load_dataset_node_overlay(dataset: str) -> dict[str, dict[str, Any]]:
    """Read per-node config overrides from ``datasets/{name}/pipeline.json``.

    Returns ``{node_name: {key: value}}`` — a sparse overlay layered onto
    the wire payload at init. Backend's ``GET /pipeline`` is the source of
    truth for runtime defaults; this overlay encodes per-dataset operator
    preferences (e.g. AIME runs through OpenRouter on Mistral instead of
    the backend's Groq default). Empty ``{}`` when the file is absent or
    has no per-node config.
    """
    raw = read_json_optional(_REPO_ROOT / "datasets" / dataset / "pipeline.json")
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for node_name, node_def in (raw.get("nodes") or {}).items():
        cfg = node_def.get("config") if isinstance(node_def, dict) else None
        if isinstance(cfg, dict) and cfg:
            out[node_name] = dict(cfg)
    return out


def dataset_prompt_dir(dataset: str) -> Path:
    """Return the expected ``prompts/`` directory for *dataset*."""
    return _REPO_ROOT / "datasets" / dataset / "prompts"


def has_dataset_prompts(dataset: str) -> bool:
    """Whether *dataset* ships a ``prompts/`` directory."""
    return dataset_prompt_dir(dataset).is_dir()


@functools.lru_cache(maxsize=128)
def load_node_prompt(
    dataset: str,
    node_name: str,
    variant: str = "default",
) -> PromptTemplate:
    """Load the canonical starting-point ``PromptTemplate`` for a node.

    Resolution:
      1. ``datasets/{dataset}/prompts/{node_name}.json`` (per-node canonical)
      2. ``datasets/{dataset}/prompts/{variant}.json`` (dataset-wide fallback)
      3. ``FileNotFoundError`` citing both expected paths.
    """
    d = dataset_prompt_dir(dataset)
    node_path = d / f"{node_name}.json"
    data = read_json_optional(node_path)
    if data is not None:
        return PromptTemplate(**data)

    variant_path = d / f"{variant}.json"
    data = read_json_optional(variant_path)
    if data is not None:
        return PromptTemplate(**data)

    raise FileNotFoundError(
        f"Canonical prompt template not found for dataset={dataset!r} node={node_name!r}. "
        f"Expected either {node_path} (per-node) or {variant_path} (dataset default). "
        f"Author one as a PromptTemplate JSON; see docs/concepts/state-record.md."
    )


@functools.lru_cache(maxsize=64)
def load_dataset_prompt(dataset: str, name: str = "default") -> PromptTemplate:
    """Load a dataset-wide named ``PromptTemplate`` (``{name}.json``).

    Thin wrapper over ``load_node_prompt`` for single-node datasets and
    display code.  Raises ``FileNotFoundError`` when the file is missing.
    """
    path = dataset_prompt_dir(dataset) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Dataset prompt not found: {path}. "
            f"Create it, or change 'starting_prompt' in the campaign config."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return PromptTemplate(**data)


def list_dataset_prompts(dataset: str) -> list[str]:
    """Return sorted template names available for *dataset* (empty if none)."""
    d = dataset_prompt_dir(dataset)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))
