"""One canonical reader for an authored dataset's on-disk config.

An authored dataset dir (``datasets/{name}/`` or a tenant upload at
``projects/{tenant}/datasets/{slug}/``) carries ``campaign.json`` (config
wrapped under the outer ``campaign_config`` key), ``pipeline.json``
(``backend_type`` + per-node overlay), and ``task_description.md``. Four call
sites used to parse these their own way — the CLI ``new`` config-read, the
launcher web-launch read, ``draft_from_dataset``, and ``SessionCtx``. This is
the single reader they share.

Rows (``cache.json``) are deliberately NOT read here: the three row consumers
source them differently (the draft reads a dir's ``cache.json`` directly;
``new``/launcher go through ``TenantDatasetStore`` with a benchmark/registry
download fallback), and folding that 3-tier sourcing policy in would force the
reader to take ``Stores``. The reader stays file-only — dir in, dataclass out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptpotter.application.config import CampaignConfig
from promptpotter.application.config import load_campaign_config as validate_campaign_config


@dataclass(frozen=True, slots=True)
class AuthoredDataset:
    """An authored dataset's validated config, parsed once from its files."""

    campaign_config: CampaignConfig
    """Validated ``campaign.json`` config — the outer ``campaign_config`` key already unwrapped."""

    task_description: str
    """``task_description.md`` text, stripped; ``""`` when the file is absent."""

    backend_type: str
    """``pipeline.json::backend_type``, lowercased; ``""`` when the field is absent.

    Intentionally non-raising so each consumer decides: the draft path defaults
    to ``DEFAULT_CONNECTOR``, the launcher raises ``LaunchError`` on a blank."""

    pipeline_nodes: dict[str, Any]
    """The WHOLE ``pipeline.json::nodes.{name}`` dicts (config + optimizer + …),
    not just ``nodes.*.config``. ``_merged_backend_nodes`` shallow-merges each
    sub-block, so dropping to ``nodes.*.config`` (as ``load_dataset_node_overlay``
    does) would silently lose ``optimizer.param_allowed_values`` locks."""


def read_campaign_config_file(path: Path) -> dict[str, Any]:
    """Read ``campaign.json`` → dict, unwrapping the optional outer
    ``campaign_config`` key (the repo on-disk convention). ``{}`` when the file
    is absent, empty, or whitespace-only."""
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    result: dict[str, Any] = data.get("campaign_config", data) or {}
    return result


def read_authored_dataset(dataset_dir: Path) -> AuthoredDataset:
    """Parse an authored dataset dir's config files into an :class:`AuthoredDataset`.

    Validates ``campaign.json`` through :class:`CampaignConfig` (``extra="forbid"``)
    — a malformed/extra-key config raises ``pydantic.ValidationError`` rather than
    silently defaulting. Does not read ``cache.json`` / rows (see module docstring).
    """
    campaign_config = validate_campaign_config(
        read_campaign_config_file(dataset_dir / "campaign.json")
    )

    task_path = dataset_dir / "task_description.md"
    task_description = task_path.read_text(encoding="utf-8").strip() if task_path.is_file() else ""

    pipeline_path = dataset_dir / "pipeline.json"
    pipeline: dict[str, Any] = (
        json.loads(pipeline_path.read_text(encoding="utf-8")) if pipeline_path.is_file() else {}
    )
    backend_type = str(pipeline.get("backend_type") or "").lower()
    pipeline_nodes = dict(pipeline.get("nodes") or {})

    return AuthoredDataset(
        campaign_config=campaign_config,
        task_description=task_description,
        backend_type=backend_type,
        pipeline_nodes=pipeline_nodes,
    )


__all__ = ["AuthoredDataset", "read_authored_dataset", "read_campaign_config_file"]
