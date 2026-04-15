"""Per-dataset starting-point prompt templates — the canonical baseline store.

Each dataset ships a ``datasets/{name}/prompts/`` directory of named
``PromptTemplate`` JSON files (6-field canonical decomposition).  The
campaign init flow loads one per prompt-bearing node and projects it
into the starting ``JobSearchPoint`` as the node's ``prompt`` config.

**This is the one true source for baseline prompts.**

Layout::

    datasets/{name}/prompts/
      default.json            # single-node datasets
      {node_name}.json        # per-node canonical templates (multi-node)

Resolution order in ``load_node_prompt(dataset, node, variant="default")``:
  1. ``{node}.json`` if present — per-node canonical template
  2. ``{variant}.json`` — dataset-wide fallback (typical single-node case)
  3. ``FileNotFoundError`` with a migration hint listing both paths

The global ``promptpotter/config/prompt_variants.json`` is a separate
store — it holds the L1 crossover/recombination pool and recon axis
seeds, not starting points.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

from promptpotter.domain.opt_search_point import PromptTemplate

__all__ = [
    "dataset_prompt_dir",
    "has_dataset_prompts",
    "list_dataset_prompts",
    "load_dataset_prompt",
    "load_node_prompt",
]

# promptpotter/application/datasets/prompt_store.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]


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
    if node_path.exists():
        data = json.loads(node_path.read_text(encoding="utf-8"))
        return PromptTemplate(**data)

    variant_path = d / f"{variant}.json"
    if variant_path.exists():
        data = json.loads(variant_path.read_text(encoding="utf-8"))
        return PromptTemplate(**data)

    raise FileNotFoundError(
        f"Canonical prompt template not found for dataset={dataset!r} node={node_name!r}. "
        f"Expected either {node_path} (per-node) or {variant_path} (dataset default). "
        f"Author one as a 6-field PromptTemplate JSON; see docs/architecture/prompt-scheme.md."
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
