"""One canonical reader for an authored dataset's on-disk config — dir in, dataclass out. Rows are
deliberately absent: their 3-tier sourcing policy would force this reader to take ``Stores``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from promptpotter.application.config import CampaignConfig
from promptpotter.application.config import load_campaign_config as validate_campaign_config
from promptpotter.application.datasets.csv_ingest import read_candidate_library_file
from promptpotter.infrastructure.store.dataset_access import dataset_pipeline_path
from promptpotter.infrastructure.store.io import read_yaml_optional
from promptpotter.shared.errors import StoredConfigInvalidError


@dataclass(frozen=True, slots=True)
class AuthoredDataset:
    campaign_config: CampaignConfig
    """Validated ``campaign.yaml`` config — the outer ``campaign_config`` key already unwrapped."""

    task_description: str
    """``task_description.md`` text, stripped; ``""`` when the file is absent."""

    backend_type: str
    """``pipeline.yaml::backend_type``, lowercased; ``""`` when the field is absent.

    Intentionally non-raising so each consumer decides: the draft path defaults
    to ``DEFAULT_CONNECTOR``, the launcher raises ``LaunchError`` on a blank."""

    pipeline_nodes: dict[str, Any]
    """The WHOLE ``pipeline.yaml::nodes.{name}`` dicts (config + optimizer + …),
    not just ``nodes.*.config``. ``_merged_backend_nodes`` shallow-merges each
    sub-block, so dropping to ``nodes.*.config`` (as ``load_dataset_node_overlay``
    does) would silently lose ``optimizer.param_allowed_values`` locks."""

    active_steps: list[str]
    """``pipeline.yaml::pipelines.default`` — the dataset's chosen pipeline (e.g.
    the full ``cache_lookup → … → token_matching`` vs a bare ``llm_only``). ``[]``
    when absent, in which case the connector's ``default_pipeline`` applies. The
    draft path carries this so reusing a dataset PRESERVES its pipeline instead of
    resetting to the connector default."""

    candidate_library: tuple[str, ...]
    """``candidate_library.txt`` — the per-pipeline origin's target list, part of
    the origin spec (a ``candidate_source`` node ranks each query against it).
    ``()`` when absent. The draft path carries this so reopening a dataset surfaces
    its already-dropped library as FULFILLED (the origin HOLDS the value); the run
    reads the same file directly for the term-index union."""


def dataset_campaign_path(dataset_dir: Path) -> Path:
    """The dataset's campaign TEMPLATE, and the one place this filename is spelled. NOT the minted
    manifest ``campaigns/{id}/campaign.json`` — incompatible schemas, one stem, extension is the tell."""
    return dataset_dir / "campaign.yaml"


def read_campaign_config_file(path: Path) -> dict[str, Any]:
    """The sole reader of that template, always wrapped in ``campaign_config``; a hand-rolled
    ``.get("campaign_config", data)`` accepts an unwrapped shape no writer emits. Raises naming *path*."""
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise StoredConfigInvalidError(path=str(path), reason=f"not valid YAML — {exc}") from exc
    result: dict[str, Any] = (parsed or {}).get("campaign_config") or {}
    return result


def load_dataset_campaign_config(path: Path) -> CampaignConfig:
    """The read-and-validate pair, owned once. ``CampaignConfig`` is ``extra="forbid"``, so a dropped knob
    makes every file naming it unloadable — a property of OUR deploy, remedied by ``restamp --apply``."""
    try:
        return validate_campaign_config(read_campaign_config_file(path))
    except ValidationError as exc:
        reason = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise StoredConfigInvalidError(path=str(path), reason=reason) from exc


def read_authored_dataset(dataset_dir: Path) -> AuthoredDataset:
    campaign_config = load_dataset_campaign_config(dataset_campaign_path(dataset_dir))

    task_path = dataset_dir / "task_description.md"
    task_description = task_path.read_text(encoding="utf-8").strip() if task_path.is_file() else ""

    pipeline_path = dataset_pipeline_path(dataset_dir)
    pipeline: dict[str, Any] = read_yaml_optional(pipeline_path) or {}
    backend_type = str(pipeline.get("backend_type") or "").lower()
    pipeline_nodes = dict(pipeline.get("nodes") or {})
    active_steps = [str(s) for s in (pipeline.get("pipelines") or {}).get("default") or []]

    return AuthoredDataset(
        campaign_config=campaign_config,
        task_description=task_description,
        backend_type=backend_type,
        pipeline_nodes=pipeline_nodes,
        active_steps=active_steps,
        candidate_library=read_candidate_library_file(dataset_dir),
    )


__all__ = [
    "AuthoredDataset",
    "load_dataset_campaign_config",
    "read_authored_dataset",
    "read_campaign_config_file",
]
