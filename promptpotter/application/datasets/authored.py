from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import reduce
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from promptpotter.application.campaign_config import CampaignConfig, merge_config_layers
from promptpotter.application.datasets.tabular_ingest import read_candidate_library_file
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.scoring.formula import compile_scorer, split_scoring_block
from promptpotter.domain.scoring import Scorer
from promptpotter.infrastructure.store.dataset_access import (
    DEFINITION_FILE,
    DatasetDefinition,
    declared_pipelines,
    read_dataset_definition,
    read_dataset_framing,
)
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.judges.registry import judge_instrument
from promptpotter.shared.errors import PayloadInvalidError, StoredConfigInvalidError


@dataclass(frozen=True, slots=True)
class AuthoredDataset:
    campaign_config: CampaignConfig
    """The definition's validated ``campaign`` section, every layer laid on."""

    task_description: str
    """``task.md``'s body, stripped; ``""`` when the definition has none."""

    pipeline_nodes: dict[str, Any]
    """The WHOLE ``pipeline.nodes.{name}`` dicts (config + optimizer + …),
    not just ``nodes.*.config``. Every sub-block is carried, so dropping to
    ``nodes.*.config`` (as ``load_dataset_node_overlay`` does) would silently lose
    ``optimizer.param_allowed_values`` locks."""

    active_steps: list[str]
    """``dataset_access.py::declared_pipelines``' default; ``[]`` only with no nodes, when the
    connector's ``default_pipeline`` applies. The draft path carries this so reusing a dataset
    PRESERVES its pipeline instead of resetting to the connector default."""

    candidate_library: tuple[str, ...]
    """``candidate_library.txt`` — the per-pipeline origin's target list, part of
    the origin spec (a ``candidate_source`` node ranks each query against it).
    ``()`` when absent. The draft path carries this so reopening a dataset surfaces
    its already-dropped library as FULFILLED (the origin HOLDS the value); the run
    reads the same file directly for the term-index union."""


def _definition(dataset_dir: Path) -> DatasetDefinition | None:
    try:
        return read_dataset_definition(dataset_dir)
    except (yaml.YAMLError, ValueError) as exc:
        raise StoredConfigInvalidError(
            path=str(dataset_dir / DEFINITION_FILE), reason=str(exc)
        ) from exc


def _campaign_block(definition: DatasetDefinition | None) -> dict[str, Any]:
    return {} if definition is None else reduce(merge_config_layers, definition.campaign_layers, {})


def dataset_campaign_block(dataset_dir: Path) -> dict[str, Any]:
    """The ``campaign`` section with every layer laid on; the minted ``campaign.json`` is another schema."""
    return _campaign_block(_definition(dataset_dir))


def campaign_file_block(path: Path) -> dict[str, Any]:
    """A definition FILE's own ``campaign`` section, laid over nothing."""
    try:
        parsed = read_yaml_optional(path)
    except yaml.YAMLError as exc:
        raise StoredConfigInvalidError(path=str(path), reason=f"not valid YAML — {exc}") from exc
    block = (parsed or {}).get("campaign") or {}
    if not isinstance(block, dict):
        raise StoredConfigInvalidError(path=str(path), reason="`campaign` must be a mapping")
    return block


def validated_campaign_config(block: Mapping[str, Any], *, path: Path) -> CampaignConfig:
    try:
        return CampaignConfig.model_validate(block)
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise StoredConfigInvalidError(path=str(path), reason=reason) from exc


def load_dataset_campaign_config(dataset_dir: Path) -> CampaignConfig:
    return validated_campaign_config(
        dataset_campaign_block(dataset_dir), path=dataset_dir / DEFINITION_FILE
    )


def with_optimization(config: CampaignConfig, overrides: Mapping[str, Any]) -> CampaignConfig:
    merged = merge_config_layers(config.model_dump(mode="json"), {"optimization": dict(overrides)})
    try:
        out = CampaignConfig.model_validate(merged)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise PayloadInvalidError(f"{where}: {first['msg']}", code="optimization_invalid") from exc
    select_optimizer(out.optimization)
    return out


def dataset_campaign_config(
    dataset_dir: Path, *, optimization: Mapping[str, Any], declared: CampaignConfig | None = None
) -> CampaignConfig:
    config = declared or load_dataset_campaign_config(dataset_dir)
    return with_optimization(config, optimization) if optimization else config


def scorer_of(config: CampaignConfig, *, verifier_graded: bool) -> Scorer:
    """``verifier_graded`` is the bank's fact; a reader holding no bank passes ``False``."""
    spec = split_scoring_block(config.scoring, judge_instrument=None)
    return compile_scorer(
        spec.per_sample,
        spec.per_cell,
        verifier_graded=verifier_graded,
        judge_instrument=judge_instrument(config.judges),
    )


def dataset_scorer(dataset_dir: Path) -> Scorer:
    return scorer_of(load_dataset_campaign_config(dataset_dir), verifier_graded=False)


def read_authored_dataset(dataset_dir: Path) -> AuthoredDataset:
    definition = _definition(dataset_dir)
    pipeline: dict[str, Any] = {} if definition is None else definition.pipeline
    return AuthoredDataset(
        campaign_config=validated_campaign_config(
            _campaign_block(definition), path=dataset_dir / DEFINITION_FILE
        ),
        task_description=read_dataset_framing(dataset_dir).description,
        pipeline_nodes=dict(pipeline.get("nodes") or {}),
        active_steps=declared_pipelines(pipeline).get("default", []),
        candidate_library=read_candidate_library_file(dataset_dir),
    )


__all__ = [
    "campaign_file_block",
    "dataset_campaign_block",
    "dataset_campaign_config",
    "dataset_scorer",
    "load_dataset_campaign_config",
    "read_authored_dataset",
    "scorer_of",
    "validated_campaign_config",
    "with_optimization",
]
